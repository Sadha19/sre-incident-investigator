import logging
import os
import re
import time

import requests
from prometheus_api_client import PrometheusConnect
from prometheus_api_client.exceptions import PrometheusApiClientException
from urllib3.util.retry import Retry

from src.sre_agent.observability import METRICS, span
from src.sre_agent.resilience import CircuitBreaker, CircuitOpenError, retry_call

logger = logging.getLogger(__name__)


# Kubernetes object names are DNS-1123 labels/subdomains. Validating
# before interpolating into PromQL prevents query injection.
_K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")

# Exclude the pod-level cgroup (container="") and the pause container.
# Without this, sum by (pod) double-counts usage and limits.
_CONTAINER_FILTER = 'container!="", container!="POD"'

MAX_ADHOC_QUERY_LENGTH = 600
MAX_ADHOC_RANGE = 3600  # seconds
MAX_RESULT_SERIES = 200


class UnsafeQueryError(ValueError):
    """An ad-hoc PromQL query was rejected by the safety guard."""


def validate_k8s_name(value, kind="name"):
    if not value or not _K8S_NAME.match(value):
        raise ValueError(f"invalid Kubernetes {kind}: {value!r}")
    return value


_STATUS = re.compile(r"HTTP Status Code (\d{3})")


def _http_status(exc):
    if isinstance(exc, PrometheusApiClientException):
        match = _STATUS.search(str(exc))
        return int(match.group(1)) if match else None
    return None


def _is_retryable(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return _http_status(exc) in (429, 502, 503, 504)


def _is_dependency_failure(exc):
    """4xx (bad query, auth) means Prometheus is up; don't trip the breaker."""
    status = _http_status(exc)
    if status is not None:
        return status >= 500 or status == 429
    return True


_DURATION = re.compile(r"\[(\d+)([smhdwy])(?::\d*[smhdwy]?)?\]")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def validate_adhoc_promql(promql, allowed_namespaces):
    """
    Guard for PromQL written by the LLM.

    An unrestricted query tool lets a model (or a prompt-injected log
    line) issue `{__name__=~".+"}` or a 30d range over every series,
    which can take Prometheus down mid-incident. This guard enforces:
        - bounded length
        - no metric-name regex / match-everything selectors
        - range windows <= 1h
        - every query scoped to an allowed namespace
    """

    if not promql or len(promql) > MAX_ADHOC_QUERY_LENGTH:
        raise UnsafeQueryError(
            f"query must be 1..{MAX_ADHOC_QUERY_LENGTH} characters"
        )

    lowered = promql.replace(" ", "")

    if "__name__" in lowered:
        raise UnsafeQueryError("selecting by __name__ is not allowed")

    if re.search(r'=~"\.[*+]"', lowered):
        raise UnsafeQueryError("match-everything regex matchers are not allowed")

    if lowered.startswith("{") or re.search(r"[(,]\{", lowered):
        raise UnsafeQueryError("selectors must name a metric")

    for amount, unit in _DURATION.findall(promql):
        if int(amount) * _UNIT_SECONDS[unit] > MAX_ADHOC_RANGE:
            raise UnsafeQueryError("range windows above 1h are not allowed")

    namespaces = re.findall(r'namespace\s*=\s*"([^"]*)"', promql)
    if not namespaces:
        raise UnsafeQueryError(
            'query must include an exact namespace="..." matcher'
        )

    disallowed = sorted(set(namespaces) - set(allowed_namespaces))
    if disallowed:
        raise UnsafeQueryError(f"namespace(s) not allowed: {disallowed}")

    return promql


class PrometheusTool:
    SOURCE = "prometheus"

    def __init__(self, url=None, timeout_s=10.0, breaker=None, client=None):
        self.url = url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        self.timeout_s = timeout_s
        self.breaker = breaker or CircuitBreaker(
            self.SOURCE, failure_threshold=3, reset_timeout=30,
            is_failure=_is_dependency_failure,
        )

        # Disable the library's own urllib3 retries: retrying happens in
        # one place (retry_call) so backoff and deadlines are consistent.
        self.prom = client or PrometheusConnect(
            url=self.url,
            disable_ssl=True,
            retry=Retry(total=0),
            timeout=timeout_s,
        )

    def check_connection(self):
        return self.prom.check_prometheus_connection()

    def query(self, promql):
        """
        Execute an instant PromQL query.

        Returns a result envelope instead of raising so investigators can
        degrade gracefully: {"success", "query", "result" | "error"}.
        """

        promql = promql.strip()
        start = time.monotonic()

        try:
            with span("prometheus.query"):
                result = retry_call(
                    lambda: self.prom.custom_query(
                        query=promql,
                        params={"timeout": f"{int(self.timeout_s)}s"},
                    ),
                    is_retryable=_is_retryable,
                    breaker=self.breaker,
                    operation="prometheus.query",
                )

            truncated = len(result) > MAX_RESULT_SERIES

            return {
                "success": True,
                "query": promql,
                "result": result[:MAX_RESULT_SERIES],
                "truncated": truncated,
                "duration_ms": round((time.monotonic() - start) * 1000, 1),
            }

        except CircuitOpenError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        METRICS.inc("dependency_errors", {"source": self.SOURCE, "op": "query"})
        logger.warning("prometheus query failed", extra={"error": error})

        return {"success": False, "query": promql, "error": error}

    # ---------------------------------------------------------
    # Selector helpers
    # ---------------------------------------------------------

    @staticmethod
    def _selector(namespace=None, pod=None, extra=_CONTAINER_FILTER):
        matchers = []
        if namespace:
            matchers.append(f'namespace="{validate_k8s_name(namespace, "namespace")}"')
        if pod:
            matchers.append(f'pod="{validate_k8s_name(pod, "pod")}"')
        if extra:
            matchers.append(extra)
        return "{" + ", ".join(matchers) + "}"

    # ---------------------------------------------------------
    # Raw metrics (kept for compatibility)
    # ---------------------------------------------------------

    def get_up_metrics(self):
        return self.query("up")

    def get_cpu_usage(self, namespace=None):
        return self.query(
            f"rate(container_cpu_usage_seconds_total{self._selector(namespace)}[5m])"
        )

    def get_memory_usage(self, namespace=None):
        return self.query(
            f"container_memory_working_set_bytes{self._selector(namespace)}"
        )

    # ---------------------------------------------------------
    # Pod-level aggregates
    # ---------------------------------------------------------

    def get_pod_cpu_usage(self, namespace=None):
        """CPU usage in cores, per pod."""
        sel = self._selector(namespace)
        return self.query(
            f"sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{sel}[5m]))"
        )

    def get_pod_cpu_utilization(self, namespace=None):
        """
        Highest container CPU usage / CPU limit ratio per pod (0..1+).
        Containers without a CPU limit are excluded (see
        get_pods_without_limits).
        """
        sel = self._selector(namespace)
        return self.query(f"""
            max by (namespace, pod) (
                sum by (namespace, pod, container) (
                    rate(container_cpu_usage_seconds_total{sel}[5m])
                )
                /
                (
                    sum by (namespace, pod, container) (container_spec_cpu_quota{sel})
                    /
                    sum by (namespace, pod, container) (container_spec_cpu_period{sel})
                    > 0
                )
            )
        """)

    def get_pod_memory_usage(self, namespace=None):
        sel = self._selector(namespace)
        return self.query(
            f"sum by (namespace, pod) (container_memory_working_set_bytes{sel})"
        )

    def get_pod_memory_limits(self, namespace=None):
        sel = self._selector(namespace)
        return self.query(
            f"sum by (namespace, pod) (container_spec_memory_limit_bytes{sel} > 0)"
        )

    def get_pod_memory_utilization(self, namespace=None):
        """Highest container working-set / memory-limit ratio per pod."""
        sel = self._selector(namespace)
        return self.query(f"""
            max by (namespace, pod) (
                sum by (namespace, pod, container) (container_memory_working_set_bytes{sel})
                /
                (sum by (namespace, pod, container) (container_spec_memory_limit_bytes{sel}) > 0)
            )
        """)

    def get_pods_without_limits(self, resource="memory", namespace=None):
        sel = self._selector(namespace)
        metric = (
            "container_spec_memory_limit_bytes"
            if resource == "memory"
            else "container_spec_cpu_quota"
        )
        return self.query(
            f"count by (namespace, pod) ({metric}{sel} <= 0)"
        )

    def get_pod_restarts(self, namespace=None, window="1h"):
        """Restarts within the window, not lifetime totals."""
        sel = self._selector(namespace, extra=None)
        return self.query(
            f"sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total{sel}[{window}]))"
        )

    def get_cpu_throttling(self, namespace=None):
        """
        Fraction of CFS periods in which the pod was throttled (0..1).

        The previous query (rate of throttled seconds > 0) flagged any
        throttling at all, which is normal for bursty workloads and
        generates alert fatigue.
        """
        sel = self._selector(namespace)
        return self.query(f"""
            sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total{sel}[5m]))
            /
            (sum by (namespace, pod) (rate(container_cpu_cfs_periods_total{sel}[5m])) > 0)
        """)

    def get_cpu_spikes(self, namespace=None):
        sel = self._selector(namespace)
        return self.query(f"""
            sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{sel}[5m]))
            >
            2 * avg_over_time(
                sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{sel}[5m]))[30m:1m]
            )
        """)
