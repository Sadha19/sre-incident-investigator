from src.sre_agent.tools.prometheus_tool import PrometheusTool


class MetricsInvestigator:

    def __init__(self, prometheus=None):
        self.prometheus = prometheus or PrometheusTool()

    def investigate(self, namespace=None):
        """
        Collect important Prometheus metrics for incident investigation.
        Each entry is an independent result envelope; one failing query
        does not hide the others.
        """

        return {
            "up": self.prometheus.get_up_metrics(),
            "pod_cpu": self.prometheus.get_pod_cpu_usage(namespace=namespace),
            "pod_memory": self.prometheus.get_pod_memory_usage(namespace=namespace),
            "pod_restarts": self.prometheus.get_pod_restarts(namespace=namespace),
        }
