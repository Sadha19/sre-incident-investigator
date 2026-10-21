from src.sre_agent.investigators.cpu_investigator import (
    parse_samples,
    summarize,
    unavailable,
)
from src.sre_agent.models import Severity
from src.sre_agent.tools.prometheus_tool import PrometheusTool


class MemoryInvestigator:
    """
    Memory working set relative to the memory limit.

    Fixes vs. the original:
        - container!="" / container!="POD" filtering (the pod-level
          cgroup series was summed in, doubling usage and limits)
        - grouped by (namespace, pod) so same-named pods in different
          namespaces no longer collide
        - pods *without* a memory limit are reported (they were silently
          dropped, yet an unbounded container is a node-level OOM risk)
    """

    def __init__(self, prometheus=None):
        self.prometheus = prometheus or PrometheusTool()

    @staticmethod
    def _get_severity(memory_usage_pct):
        if memory_usage_pct > 90:
            return Severity.CRITICAL.name
        if memory_usage_pct > 80:
            return Severity.HIGH.name
        return Severity.OK.name

    def investigate(self, namespace=None, pods=None):
        usage_result = self.prometheus.get_pod_memory_usage(namespace=namespace)
        limit_result = self.prometheus.get_pod_memory_limits(namespace=namespace)

        for result in (usage_result, limit_result):
            if not result["success"]:
                return unavailable("memory", result["error"])

        def key(labels):
            return labels.get("namespace"), labels.get("pod", "unknown")

        limits = {
            key(labels): value
            for labels, value in parse_samples(limit_result["result"])
            if value > 0
        }

        findings = []

        for labels, usage in parse_samples(usage_result["result"]):
            pod = labels.get("pod", "unknown")
            if pods and pod not in pods:
                continue

            limit = limits.get(key(labels))

            if limit is None:
                findings.append({
                    "pod": pod,
                    "metric": "memory",
                    "current_usage": "no memory limit set",
                    "usage_bytes": usage,
                    "limit_bytes": None,
                    "severity": Severity.LOW.name,
                })
                continue

            pct = usage / limit * 100

            findings.append({
                "pod": pod,
                "metric": "memory",
                "current_usage": f"{pct:.2f}%",
                "usage_bytes": usage,
                "limit_bytes": limit,
                "severity": self._get_severity(pct),
            })

        return summarize("memory", findings)

    def investigate_usage_percentage(self, usage_percentage, pod="unknown"):
        return {
            "pod": pod,
            "metric": "memory",
            "current_usage": f"{usage_percentage:.2f}%",
            "severity": self._get_severity(usage_percentage),
        }

    def investigate_oom(self, pod="unknown"):
        return {
            "pod": pod,
            "metric": "memory",
            "current_usage": "OOMKilled",
            "severity": Severity.CRITICAL.name,
            "reason": "Container was terminated because it exceeded its memory limit",
        }

    def investigate_memory_limit(self, pod="unknown"):
        return {
            "pod": pod,
            "metric": "memory",
            "current_usage": "Memory limit exceeded",
            "severity": Severity.CRITICAL.name,
            "reason": "Container memory usage exceeded its configured limit",
        }
