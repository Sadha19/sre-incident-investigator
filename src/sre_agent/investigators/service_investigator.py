import logging

from kubernetes import client
from kubernetes.client.rest import ApiException

from src.sre_agent.models import Severity
from src.sre_agent.tools.kubernetes_tool import load_kubernetes_config

logger = logging.getLogger(__name__)


class ServiceInvestigator:
    """
    Checks whether a Service can actually route traffic: selector
    matches pods, and EndpointSlices have ready backends.

    Status vocabulary: HEALTHY | DEGRADED | CRITICAL | UNKNOWN, with a
    canonical `severity` alongside it for aggregation.
    """

    def __init__(self, core_api=None, discovery_api=None, timeout_s=10.0):
        if core_api is None or discovery_api is None:
            try:
                load_kubernetes_config()
            except Exception as exc:
                # Keep construction possible so investigate() can
                # return a structured UNKNOWN instead of crashing.
                logger.warning("kubernetes config unavailable", extra={"error": str(exc)})

        self.core_api = core_api or client.CoreV1Api()
        self.discovery_api = discovery_api or client.DiscoveryV1Api()
        self.timeout_s = timeout_s

    def _result(self, service_name, namespace, status, severity, findings, **extra):
        return {
            "service": service_name,
            "namespace": namespace,
            "status": status,
            "severity": severity.name,
            "findings": findings,
            **extra,
        }

    def investigate(self, service_name, namespace="default"):
        timeout = {"_request_timeout": self.timeout_s}

        try:
            service = self.core_api.read_namespaced_service(
                name=service_name, namespace=namespace, **timeout
            )
        except ApiException as exc:
            if exc.status == 404:
                return self._result(
                    service_name, namespace, "CRITICAL", Severity.CRITICAL,
                    [f"Service '{service_name}' does not exist."],
                )
            return self._unknown(service_name, namespace, exc)
        except Exception as exc:
            return self._unknown(service_name, namespace, exc)

        try:
            selector = service.spec.selector or {}

            # EndpointSlices are the source of truth (core/v1 Endpoints
            # is deprecated since Kubernetes 1.33). Endpoints is still
            # read so mismatches between the two can be reported.
            slices = self.discovery_api.list_namespaced_endpoint_slice(
                namespace=namespace,
                label_selector=f"kubernetes.io/service-name={service_name}",
                **timeout,
            )

            slice_ready = 0
            slice_not_ready = 0

            for endpoint_slice in slices.items or []:
                for endpoint in endpoint_slice.endpoints or []:
                    # Per the API, an unset ready condition means ready.
                    if endpoint.conditions and endpoint.conditions.ready is False:
                        slice_not_ready += 1
                    else:
                        slice_ready += 1

            ready_addresses = []
            not_ready_addresses = []

            try:
                endpoints = self.core_api.read_namespaced_endpoints(
                    name=service_name, namespace=namespace, **timeout
                )
                for subset in endpoints.subsets or []:
                    ready_addresses.extend(subset.addresses or [])
                    not_ready_addresses.extend(subset.not_ready_addresses or [])
            except ApiException as exc:
                if exc.status != 404:
                    raise

            matching_pods = []
            if selector:
                pods = self.core_api.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=",".join(f"{k}={v}" for k, v in sorted(selector.items())),
                    **timeout,
                )
                matching_pods = [
                    pod
                    for pod in (pods.items or [])
                    if all(
                        (getattr(getattr(pod, "metadata", None), "labels", None) or {}).get(key)
                        == value
                        for key, value in selector.items()
                    )
                ]

            findings = []
            ready = max(len(ready_addresses), slice_ready)
            not_ready = max(len(not_ready_addresses), slice_not_ready)

            if not selector:
                findings.append("Service has no selector; backends may be managed manually.")
            elif not matching_pods:
                findings.append("Selector mismatch: no Pods match the Service selector.")

            if ready == 0:
                findings.append("Service has zero active endpoints.")
            elif not ready_addresses and slice_ready:
                findings.append("EndpointSlices have ready endpoints, but Endpoints does not.")

            if not_ready:
                findings.append("One or more backend endpoints are not ready.")

            if ready == 0:
                status, severity = "DEGRADED", Severity.CRITICAL
            elif not_ready:
                status, severity = "DEGRADED", Severity.HIGH
            else:
                status, severity = "HEALTHY", Severity.OK

            return self._result(
                service_name, namespace, status, severity, findings,
                selector=selector,
                matching_pods=len(matching_pods),
                ready_endpoints=len(ready_addresses),
                not_ready_endpoints=len(not_ready_addresses),
                endpoint_slice_ready=slice_ready,
                endpoint_slice_not_ready=slice_not_ready,
            )

        except Exception as exc:
            return self._unknown(service_name, namespace, exc)

    def _unknown(self, service_name, namespace, exc):
        logger.warning(
            "service investigation failed",
            extra={"service": service_name, "error": f"{type(exc).__name__}: {exc}"},
        )
        return self._result(
            service_name, namespace, "UNKNOWN", Severity.UNKNOWN,
            ["Service investigation failed: Kubernetes API request failed."],
            error=f"{type(exc).__name__}: {exc}",
        )
