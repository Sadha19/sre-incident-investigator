from src.sre_agent.models import Severity, max_severity
from src.sre_agent.tools.kubernetes_tool import KubernetesTool


# waiting reason -> (severity, issue template, recommendations)
WAITING_RULES = {
    "CrashLoopBackOff": (
        Severity.CRITICAL,
        "Container {name} is in CrashLoopBackOff.",
        [
            "Check previous container logs (kubectl logs --previous).",
            "Check application startup configuration.",
            "Check environment variables, ConfigMaps and Secrets.",
        ],
    ),
    "ImagePullBackOff": (
        Severity.CRITICAL,
        "Container {name} is in ImagePullBackOff.",
        [
            "Verify container image name and tag.",
            "Check image registry availability.",
            "Check imagePullSecrets.",
        ],
    ),
    "ErrImagePull": (
        Severity.CRITICAL,
        "Container {name} cannot pull its image.",
        ["Check image name, tag and registry credentials."],
    ),
    "InvalidImageName": (
        Severity.CRITICAL,
        "Container {name} has an invalid image reference.",
        ["Fix the image reference in the pod template."],
    ),
    "CreateContainerConfigError": (
        Severity.CRITICAL,
        "Container {name} cannot be created (config error).",
        ["Verify referenced ConfigMaps / Secrets and keys exist."],
    ),
    "RunContainerError": (
        Severity.CRITICAL,
        "Container {name} is in RunContainerError.",
        [
            "Verify ConfigMaps / Secrets.",
            "Verify container command / entrypoint arguments.",
            "Check volume mounts.",
        ],
    ),
    "ContainerCreating": (
        Severity.LOW,
        "Container {name} is still being created.",
        ["If this persists, check volume mounts and image pull events."],
    ),
    "PodInitializing": (
        Severity.LOW,
        "Container {name} is waiting for init containers.",
        [],
    ),
}

TERMINATION_RULES = {
    "OOMKilled": (
        Severity.CRITICAL,
        "Container {name} was OOMKilled (exit code {exit_code}).",
        [
            "Compare memory usage against the container memory limit.",
            "Check for application memory leaks.",
            "Review Kubernetes resource limits.",
        ],
    ),
    "Error": (
        Severity.MEDIUM,
        "Container {name} previously exited with an error (exit code {exit_code}).",
        [
            "Check previous container logs.",
            "Check application startup errors.",
        ],
    ),
    "ContainerCannotRun": (
        Severity.CRITICAL,
        "Container {name} could not run (exit code {exit_code}).",
        ["Verify the command / entrypoint exists in the image."],
    ),
}

PHASE_RULES = {
    "Pending": (Severity.MEDIUM, "Pod is Pending.", ["Check scheduling events and node resources."]),
    "Failed": (Severity.CRITICAL, "Pod has entered Failed state.", ["Check pod events and container termination reason."]),
    "Unknown": (Severity.HIGH, "Pod phase is Unknown (node may be unreachable).", ["Check node status."]),
}


class PodHealthInvestigator:
    """
    Investigates Kubernetes pod health: phase, waiting/terminated
    states (including init containers), restarts and readiness.

    Severity is escalate-only: evidence can raise it, never lower it.
    """

    RESTART_WARNING = 3
    RESTART_CRITICAL = 10

    def __init__(self, kubernetes_tool=None):
        self.k8s = kubernetes_tool or KubernetesTool()

    def investigate_pod(self, pod_name, namespace="default", pod=None):
        """`pod` may be passed in to avoid a second API read."""

        pod = pod or self.k8s.get_pod_details(pod_name=pod_name, namespace=namespace)

        severity = Severity.OK
        issues = []
        recommendations = []
        containers = []

        def add(rule_severity, issue, recs):
            nonlocal severity
            severity = max_severity(severity, rule_severity)
            issues.append(issue)
            recommendations.extend(recs)

        if pod["phase"] in PHASE_RULES:
            add(*PHASE_RULES[pod["phase"]])
        elif pod["phase"] not in ("Running", "Succeeded"):
            add(Severity.MEDIUM, f"Unexpected pod phase: {pod['phase']}", [])

        if pod.get("reason") == "Evicted":
            add(Severity.HIGH, "Pod was evicted.", ["Check node pressure (memory/disk) and pod priority."])

        for container in pod["containers"]:
            name = container["name"]
            result = {
                "name": name,
                "init": container.get("init", False),
                "ready": container["ready"],
                "restart_count": container["restart_count"],
                "status": "Healthy",
            }

            restarts = container["restart_count"]
            if restarts >= self.RESTART_CRITICAL:
                add(Severity.CRITICAL, f"Container {name} has restarted {restarts} time(s).", [])
            elif restarts >= self.RESTART_WARNING:
                add(Severity.MEDIUM, f"Container {name} has restarted {restarts} time(s).", [])
            elif restarts > 0:
                add(Severity.LOW, f"Container {name} has restarted {restarts} time(s).", [])

            waiting = container.get("waiting_reason")
            if waiting:
                result["status"] = waiting
                rule = WAITING_RULES.get(waiting)
                if rule:
                    add(rule[0], rule[1].format(name=name), rule[2])
                else:
                    add(Severity.MEDIUM, f"Container {name} is waiting: {waiting}", [])

            termination = container.get("last_termination")
            if termination:
                result["last_termination"] = termination
                rule = TERMINATION_RULES.get(termination["reason"])
                if rule:
                    add(rule[0], rule[1].format(name=name, exit_code=termination["exit_code"]), rule[2])

            # Completed init containers are expected to be not-ready.
            init_done = result["init"] and container.get("terminated_reason") == "Completed"

            if not container["ready"] and not init_done and pod["phase"] != "Succeeded":
                add(
                    Severity.MEDIUM,
                    f"Container {name} is not ready.",
                    ["Check readiness probe and application health."],
                )

            containers.append(result)

        return {
            "pod": pod["name"],
            "namespace": pod["namespace"],
            "status": pod["phase"],
            "severity": severity.name,
            "issues": list(dict.fromkeys(issues)),
            "containers": containers,
            "recommendations": list(dict.fromkeys(recommendations)),
        }

    def investigate_namespace(self, namespace="default"):
        return [
            self.investigate_pod(pod_name=p["name"], namespace=namespace)
            for p in self.k8s.get_pods(namespace=namespace)
        ]

    def print_report(self, diagnosis):
        print()
        print("=" * 60)
        print("             POD HEALTH INVESTIGATION")
        print("=" * 60)
        print()
        print(f"Pod       : {diagnosis['pod']}")
        print(f"Namespace : {diagnosis['namespace']}")
        print(f"Status    : {diagnosis['status']}")
        print(f"Severity  : {diagnosis['severity']}")
        print()
        print("Containers")
        print("-" * 40)

        for container in diagnosis["containers"]:
            print(f"Name: {container['name']}")
            print(f"Ready: {container['ready']}")
            print(f"Restarts: {container['restart_count']}")
            print(f"Status: {container['status']}")
            if "last_termination" in container:
                print(f"Last Termination: {container['last_termination']['reason']}")
                print(f"Exit Code: {container['last_termination']['exit_code']}")

        print()
        print("Issues")
        print("-" * 40)
        for issue in diagnosis["issues"] or ["No issues detected."]:
            print(f"- {issue}")

        print()
        print("Recommendations")
        print("-" * 40)
        for rec in diagnosis["recommendations"] or ["No recommendations."]:
            print(f"- {rec}")

        print()
        print("=" * 60)
