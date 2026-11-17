from src.sre_agent.models import Severity, max_severity
from src.sre_agent.tools.prometheus_tool import PrometheusTool


def parse_samples(result):
    """Yield (labels, float_value) from a Prometheus vector, skipping bad samples."""

    for sample in result:
        try:
            yield sample["metric"], float(sample["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue


def unavailable(metric, error):
    return {
        "metric": metric,
        "available": False,
        "severity": Severity.UNKNOWN.name,
        "error": error,
        "findings": [],
    }


def summarize(metric, findings):
    return {
        "metric": metric,
        "available": True,
        "severity": max_severity(*(f["severity"] for f in findings)).name,
        "error": None,
        "findings": findings,
    }


class CPUInvestigator:
    """Investigate per-pod CPU usage and CFS throttling."""

    def __init__(self, prometheus=None):
        self.prometheus = prometheus or PrometheusTool()

    @staticmethod
    def _get_severity(utilization_pct):
        if utilization_pct >= 90:
            return Severity.CRITICAL.name
        if utilization_pct >= 80:
            return Severity.HIGH.name
        return "NORMAL"

    @staticmethod
    def _throttle_severity(ratio):
        if ratio >= 0.25:
            return Severity.CRITICAL.name
        if ratio >= 0.10:
            return Severity.HIGH.name
        return "NORMAL"

    def investigate(self, namespace=None, pods=None):
        result = self.prometheus.get_pod_cpu_usage(namespace=namespace)

        if not result["success"]:
            return unavailable("cpu", result["error"])

        findings = []
        for labels, ratio in parse_samples(result["result"]):
            pod = labels.get("pod", "unknown")
            if pods and pod not in pods:
                continue

            pct = ratio * 100
            findings.append({
                "pod": pod,
                "metric": "cpu",
                "current_usage": f"{pct:.2f}%",
                "value": round(ratio, 4),
                "severity": self._get_severity(pct),
            })

        return findings

    def investigate_throttling(self, namespace=None, pods=None):
        result = self.prometheus.get_cpu_throttling(namespace=namespace)

        if not result["success"]:
            return unavailable("cpu_throttling", result["error"])

        findings = []
        for labels, ratio in parse_samples(result["result"]):
            pod = labels.get("pod", "unknown")
            if pods and pod not in pods:
                continue

            findings.append({
                "pod": pod,
                "metric": "cpu_throttling",
                "current_usage": f"{ratio * 100:.2f}% of periods throttled",
                "value": round(ratio, 4),
                "severity": self._throttle_severity(ratio),
            })

        return findings
