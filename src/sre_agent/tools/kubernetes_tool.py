import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from src.sre_agent.observability import METRICS, span
from src.sre_agent.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RateLimitedError,
    TokenBucket,
    retry_call,
)

logger = logging.getLogger(__name__)


class DataSourceError(Exception):
    """
    A dependency could not answer. Callers decide how to degrade;
    they must never treat this as "no problems found".
    """

    def __init__(self, source, operation, message, status=None):
        super().__init__(f"{source}.{operation}: {message}")
        self.source = source
        self.operation = operation
        self.status = status


class NotFoundError(DataSourceError):
    """The object does not exist (HTTP 404). An answer, not an outage."""


def _is_retryable(exc):
    if isinstance(exc, ApiException):
        return exc.status in (429, 500, 502, 503, 504) or exc.status == 0
    return isinstance(exc, (Urllib3HTTPError, ConnectionError, TimeoutError))


def _is_dependency_failure(exc):
    """404/403/400/409 are answers from a healthy API server."""
    if isinstance(exc, ApiException):
        return exc.status == 0 or exc.status >= 500 or exc.status == 429
    return True


def load_kubernetes_config():
    """In-cluster config when running as a pod, kubeconfig otherwise."""

    try:
        config.load_incluster_config()
        logger.info("kubernetes config loaded", extra={"mode": "in-cluster"})
    except ConfigException:
        config.load_kube_config()
        logger.info("kubernetes config loaded", extra={"mode": "kubeconfig"})


def selector_to_string(label_selector):
    """
    Convert a V1LabelSelector into the string form accepted by the
    list APIs (supports matchLabels and matchExpressions).
    """

    parts = [f"{k}={v}" for k, v in sorted((label_selector.match_labels or {}).items())]

    for expr in label_selector.match_expressions or []:
        op = expr.operator
        values = ",".join(expr.values or [])
        if op == "In":
            parts.append(f"{expr.key} in ({values})")
        elif op == "NotIn":
            parts.append(f"{expr.key} notin ({values})")
        elif op == "Exists":
            parts.append(expr.key)
        elif op == "DoesNotExist":
            parts.append(f"!{expr.key}")

    return ",".join(parts)


class KubernetesTool:
    """
    Read-only wrapper around the Kubernetes Python client.

    Every call gets:
        - a request timeout (a hung API server must not hang the agent)
        - retries with jittered backoff for 429/5xx/connection errors
        - a shared circuit breaker
        - client-side rate limiting
        - a span (duration metric + structured log)
    """

    SOURCE = "kubernetes"

    def __init__(
        self,
        core_api=None,
        apps_api=None,
        timeout_s=10.0,
        breaker=None,
        rate_limiter=None,
        load_config=True,
    ):
        if load_config and core_api is None:
            load_kubernetes_config()

        self.core_api = core_api or client.CoreV1Api()
        self.apps_api = apps_api or client.AppsV1Api()
        self.timeout_s = timeout_s
        self.breaker = breaker or CircuitBreaker(
            self.SOURCE, failure_threshold=5, reset_timeout=30,
            is_failure=_is_dependency_failure,
        )
        self.rate_limiter = rate_limiter or TokenBucket(rate=20, capacity=40)

    # ---------------------------------------------------------
    # Core call wrapper
    # ---------------------------------------------------------

    def _call(self, operation, func, **kwargs):
        kwargs.setdefault("_request_timeout", self.timeout_s)

        try:
            self.rate_limiter.acquire(timeout=self.timeout_s)

            with span(f"k8s.{operation}"):
                return retry_call(
                    lambda: func(**kwargs),
                    is_retryable=_is_retryable,
                    breaker=self.breaker,
                    operation=f"k8s.{operation}",
                )

        except ApiException as exc:
            METRICS.inc("dependency_errors", {"source": self.SOURCE, "op": operation})
            if exc.status == 404:
                raise NotFoundError(self.SOURCE, operation, "not found", 404) from exc
            raise DataSourceError(
                self.SOURCE, operation, f"HTTP {exc.status} {exc.reason}", exc.status
            ) from exc

        except (CircuitOpenError, RateLimitedError) as exc:
            METRICS.inc("dependency_errors", {"source": self.SOURCE, "op": operation})
            raise DataSourceError(self.SOURCE, operation, str(exc)) from exc

        except Exception as exc:
            METRICS.inc("dependency_errors", {"source": self.SOURCE, "op": operation})
            raise DataSourceError(
                self.SOURCE, operation, f"{type(exc).__name__}: {exc}"
            ) from exc

    # ---------------------------------------------------------
    # PODS
    # ---------------------------------------------------------

    def get_pods(self, namespace="default", label_selector=None):
        kwargs = {"namespace": namespace}
        if label_selector:
            kwargs["label_selector"] = label_selector

        pods = self._call("list_pods", self.core_api.list_namespaced_pod, **kwargs)

        return [
            {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "node": pod.spec.node_name,
            }
            for pod in pods.items
        ]

    def get_pod(self, pod_name, namespace="default"):
        pod = self._call(
            "read_pod", self.core_api.read_namespaced_pod,
            name=pod_name, namespace=namespace,
        )

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "node": pod.spec.node_name,
            "pod_ip": pod.status.pod_ip,
        }

    def get_workload_pods(self, workload_name, namespace="default"):
        """
        Resolve a workload to its pods using the controller's label
        selector, which is what Kubernetes itself uses.

        The previous name-prefix match was wrong in both directions:
        "payment" matched "payment-worker-*", and pods of a workload
        whose name differed from its pods' prefix were missed.

        Returns (pod_names, resolution) where resolution says how the
        pods were found, so reports can flag a low-confidence match.
        """

        for kind, reader in (
            ("Deployment", self.apps_api.read_namespaced_deployment),
            ("StatefulSet", self.apps_api.read_namespaced_stateful_set),
            ("DaemonSet", self.apps_api.read_namespaced_daemon_set),
        ):
            try:
                obj = self._call(
                    f"read_{kind.lower()}", reader,
                    name=workload_name, namespace=namespace,
                )
            except NotFoundError:
                continue

            selector = selector_to_string(obj.spec.selector)
            pods = self.get_pods(namespace=namespace, label_selector=selector)

            return (
                sorted(p["name"] for p in pods),
                {"method": "label_selector", "kind": kind, "selector": selector},
            )

        # Fallback for bare pods / unknown controllers.
        logger.warning(
            "workload controller not found; falling back to name prefix",
            extra={"workload": workload_name, "namespace": namespace},
        )
        pods = self.get_pods(namespace=namespace)

        return (
            sorted(
                p["name"] for p in pods
                if p["name"] == workload_name
                or p["name"].startswith(workload_name + "-")
            ),
            {"method": "name_prefix", "kind": None, "selector": None},
        )

    # ---------------------------------------------------------
    # EVENTS
    # ---------------------------------------------------------

    def get_pod_events(self, pod_name, namespace="default"):
        """
        Server-side filtered: previously every call listed *all* events
        in the namespace and filtered client-side, i.e. O(pods x events)
        load on the API server at exactly the moment it may be stressed.
        """

        events = self._call(
            "list_events", self.core_api.list_namespaced_event,
            namespace=namespace,
            field_selector=f"involvedObject.kind=Pod,involvedObject.name={pod_name}",
        )

        return [
            {
                "name": event.metadata.name,
                "reason": event.reason,
                "message": event.message or "",
                "type": event.type,
                "count": event.count or 1,
                "first_timestamp": str(event.first_timestamp) if event.first_timestamp else None,
                "last_timestamp": str(event.last_timestamp) if event.last_timestamp else None,
                "source": event.source.component if event.source else None,
            }
            for event in events.items
        ]

    # ---------------------------------------------------------
    # LOGS
    # ---------------------------------------------------------

    def get_container_logs(
        self,
        pod_name,
        namespace="default",
        container=None,
        tail_lines=100,
        previous=False,
        limit_bytes=512 * 1024,
    ):
        return self._call(
            "read_logs_previous" if previous else "read_logs",
            self.core_api.read_namespaced_pod_log,
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            timestamps=True,
            previous=previous,
            limit_bytes=limit_bytes,
        )

    # ---------------------------------------------------------
    # DEPLOYMENTS / SERVICES / NODES
    # ---------------------------------------------------------

    def get_deployments(self, namespace="default"):
        deployments = self._call(
            "list_deployments", self.apps_api.list_namespaced_deployment,
            namespace=namespace,
        )

        return [
            {
                "name": d.metadata.name,
                "namespace": d.metadata.namespace,
                "desired": d.spec.replicas or 0,
                "ready": d.status.ready_replicas or 0,
                "available": d.status.available_replicas or 0,
            }
            for d in deployments.items
        ]

    def get_services(self, namespace="default"):
        services = self._call(
            "list_services", self.core_api.list_namespaced_service,
            namespace=namespace,
        )

        return [
            {
                "name": s.metadata.name,
                "namespace": s.metadata.namespace,
                "type": s.spec.type,
                "cluster_ip": s.spec.cluster_ip,
                "ports": [
                    {
                        "port": p.port,
                        "target_port": str(p.target_port),
                        "protocol": p.protocol,
                    }
                    for p in (s.spec.ports or [])
                ],
            }
            for s in services.items
        ]

    def get_nodes(self):
        nodes = self._call("list_nodes", self.core_api.list_node)

        results = []
        for node in nodes.items:
            ready = "Unknown"
            for condition in node.status.conditions or []:
                if condition.type == "Ready":
                    ready = condition.status
            results.append({"name": node.metadata.name, "ready": ready})

        return results

    # ---------------------------------------------------------
    # DETAILED POD INFORMATION
    # ---------------------------------------------------------

    def get_pod_details(self, pod_name, namespace="default"):
        pod = self._call(
            "read_pod", self.core_api.read_namespaced_pod,
            name=pod_name, namespace=namespace,
        )

        # Include init containers: a failing init container keeps the
        # pod in Init:CrashLoopBackOff and was previously invisible.
        statuses = [
            (s, True) for s in (pod.status.init_container_statuses or [])
        ] + [
            (s, False) for s in (pod.status.container_statuses or [])
        ]

        containers = []

        for status, is_init in statuses:
            last_termination = None

            if status.last_state and status.last_state.terminated:
                t = status.last_state.terminated
                last_termination = {
                    "reason": t.reason,
                    "exit_code": t.exit_code,
                    "message": t.message,
                    "started_at": str(t.started_at) if t.started_at else None,
                    "finished_at": str(t.finished_at) if t.finished_at else None,
                }

            state = status.state

            containers.append({
                "name": status.name,
                "init": is_init,
                "ready": bool(status.ready),
                "restart_count": status.restart_count or 0,
                "waiting_reason": state.waiting.reason if state and state.waiting else None,
                "waiting_message": state.waiting.message if state and state.waiting else None,
                "running": bool(state and state.running),
                "terminated": bool(state and state.terminated),
                "terminated_reason": state.terminated.reason if state and state.terminated else None,
                "last_termination": last_termination,
            })

        conditions = {
            c.type: {"status": c.status, "reason": c.reason, "message": c.message}
            for c in (pod.status.conditions or [])
        }

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "reason": pod.status.reason,
            "node": pod.spec.node_name,
            "pod_ip": pod.status.pod_ip,
            "deleting": pod.metadata.deletion_timestamp is not None,
            "conditions": conditions,
            "containers": containers,
        }
