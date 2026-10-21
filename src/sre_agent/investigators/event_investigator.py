from src.sre_agent.models import Severity, max_severity
from src.sre_agent.tools.kubernetes_tool import KubernetesTool


# Ordered, first match wins. Matched against "<reason> <message>".
EVENT_RULES = [
    (
        "image",
        ["failed to pull image", "errimagepull", "imagepullbackoff",
         "pull access denied", "repository does not exist", "manifest unknown"],
        Severity.CRITICAL,
        ["Verify container image name and tag.",
         "Check container registry availability.",
         "Check imagePullSecrets."],
    ),
    (
        "command",
        ["executable file not found", "exec:", "unable to start container process",
         "containercannotrun"],
        Severity.CRITICAL,
        ["Check container command, args and entrypoint.",
         "Verify the executable exists inside the image."],
    ),
    (
        "scheduling",
        ["failedscheduling", "insufficient cpu", "insufficient memory",
         "didn't match node selector", "untolerated taint", "had taint"],
        Severity.CRITICAL,
        ["Check node resource availability.",
         "Check node taints and pod tolerations.",
         "Check pod resource requests, node selectors and affinity rules."],
    ),
    (
        "volume",
        ["failedmount", "failedattachvolume", "mountvolume",
         "persistentvolumeclaim", "unbound immediate persistentvolumeclaims"],
        Severity.CRITICAL,
        ["Check PersistentVolume / PersistentVolumeClaim status.",
         "Check volume configuration and storage availability."],
    ),
    (
        "crashloop",
        ["back-off restarting failed container", "restarting failed container"],
        Severity.CRITICAL,
        ["Check current and previous container logs.",
         "Check container startup configuration."],
    ),
    (
        "oom",
        ["oomkilling", "oomkilled", "out of memory"],
        Severity.CRITICAL,
        ["Compare memory usage against limits; check for leaks."],
    ),
    (
        "eviction",
        ["evicted", "the node was low on resource"],
        Severity.HIGH,
        ["Check node memory/disk pressure and pod priority."],
    ),
    (
        "probe",
        ["unhealthy", "readiness probe", "liveness probe", "startup probe"],
        Severity.MEDIUM,
        ["Check readiness / liveness / startup probe configuration.",
         "Check the application health endpoint and its dependencies."],
    ),
]


class EventInvestigator:
    """
    Investigates Kubernetes events and converts them into
    troubleshooting information.

    Fixes vs. the original:
        - only Warning events are treated as problems
        - severity is escalate-only (a later probe event used to
          downgrade an earlier CRITICAL image-pull event to WARNING)
        - events are de-duplicated before analysis, not during
    """

    def __init__(self, kubernetes_tool=None):
        self.k8s = kubernetes_tool or KubernetesTool()

    @staticmethod
    def _dedupe(events):
        unique = {}

        for event in events:
            key = (event["type"], event["reason"], event["message"])
            if key in unique:
                unique[key]["count"] += event["count"]
                unique[key]["last_timestamp"] = max(
                    filter(None, [unique[key]["last_timestamp"], event["last_timestamp"]]),
                    default=None,
                )
            else:
                unique[key] = {
                    "type": event["type"],
                    "reason": event["reason"],
                    "message": event["message"],
                    "count": event["count"],
                    "source": event["source"],
                    "last_timestamp": event["last_timestamp"],
                }

        return list(unique.values())

    def investigate_pod(self, pod_name, namespace="default"):
        events = self._dedupe(
            self.k8s.get_pod_events(pod_name=pod_name, namespace=namespace)
        )

        severity = Severity.OK
        issues = []
        recommendations = []
        categories = []

        for event in events:
            if event["type"] != "Warning":
                continue

            text = f"{event['reason']} {event['message']}".lower()
            issue = f"{event['reason']}: {event['message']} (x{event['count']})"

            for category, keywords, rule_severity, recs in EVENT_RULES:
                if any(k in text for k in keywords):
                    severity = max_severity(severity, rule_severity)
                    categories.append(category)
                    recommendations.extend(recs)
                    break
            else:
                severity = max_severity(severity, Severity.MEDIUM)

            issues.append(issue)

        return {
            "pod": pod_name,
            "namespace": namespace,
            "severity": severity.name,
            "events": events,
            "categories": list(dict.fromkeys(categories)),
            "issues": list(dict.fromkeys(issues)),
            "recommendations": list(dict.fromkeys(recommendations)),
        }

    def print_report(self, diagnosis):
        print()
        print("=" * 60)
        print("             EVENT INVESTIGATION")
        print("=" * 60)
        print()
        print(f"Pod       : {diagnosis['pod']}")
        print(f"Namespace : {diagnosis['namespace']}")
        print(f"Severity  : {diagnosis['severity']}")
        print()
        print("KUBERNETES EVENTS")
        print("-" * 60)

        if not diagnosis["events"]:
            print("No events found.")

        for event in diagnosis["events"]:
            print(f"[{event['type']}] {event['reason']}")
            print(f"Message: {event['message']}")
            print(f"Count: {event['count']}")
            print(f"Source: {event['source']}")
            print()

        print()
        print("ISSUES")
        print("-" * 60)
        for issue in diagnosis["issues"] or ["No issues detected."]:
            print(f"- {issue}")

        print()
        print("RECOMMENDATIONS")
        print("-" * 60)
        for rec in diagnosis["recommendations"] or ["No recommendations."]:
            print(f"- {rec}")

        print()
        print("=" * 60)
