from src.sre_agent.tools.kubernetes_tool import KubernetesTool


class EventInvestigator:
    """
    Investigates Kubernetes events and converts them
    into useful SRE troubleshooting information.
    """

    def __init__(self, kubernetes_tool=None):

        if kubernetes_tool:
            self.k8s = kubernetes_tool
        else:
            self.k8s = KubernetesTool()

    def investigate_pod(self, pod_name, namespace="default"):

        events = self.k8s.get_pod_events(
            pod_name=pod_name,
            namespace=namespace
        )

        diagnosis = {
            "pod": pod_name,
            "namespace": namespace,
            "severity": "INFO",
            "events": [],
            "issues": [],
            "recommendations": [],
        }

        # Group similar events
        unique_events = {}

        for event in events:

            key = (
                event["type"],
                event["reason"],
                event["message"]
            )

            if key not in unique_events:

                unique_events[key] = {
                    "type": event["type"],
                    "reason": event["reason"],
                    "message": event["message"],
                    "count": event["count"],
                    "source": event["source"],
                    "last_timestamp": event["last_timestamp"],
                }

            else:

                unique_events[key]["count"] += event["count"]


            diagnosis["events"] = list(
                unique_events.values()
            )

            reason = event["reason"]
            message = event["message"]

            # --------------------------------------------------
            # Image pull problems
            # --------------------------------------------------

            image_problem = any(keyword in message.lower() for keyword in [
                "failed to pull image",
                "errimagepull",
                "imagepullbackoff",
                "pull access denied",
                "repository does not exist",
            ])

            if image_problem:

                diagnosis["severity"] = "CRITICAL"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Verify container image name.",
                    "Verify container image tag.",
                    "Check container registry availability.",
                    "Check imagePullSecrets.",
                ])

                continue

            # --------------------------------------------------
            # Container command / entrypoint problems
            # --------------------------------------------------

            command_problem = any(keyword in message.lower() for keyword in [
                "executable file not found",
                "exec:",
                "unable to start container process",
                "containercannotrun",
            ])

            if command_problem:

                diagnosis["severity"] = "CRITICAL"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Check container command.",
                    "Check container args.",
                    "Check container entrypoint.",
                    "Verify the executable exists inside the image.",
                ])

                continue

            # --------------------------------------------------
            # Scheduling problems
            # --------------------------------------------------

            scheduling_problem = any(keyword in message.lower() for keyword in [
                "failedscheduling",
                "insufficient cpu",
                "insufficient memory",
                "didn't match node selector",
                "taint",
            ])

            if scheduling_problem:

                diagnosis["severity"] = "CRITICAL"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Check node resource availability.",
                    "Check node taints and pod tolerations.",
                    "Check pod resource requests.",
                    "Check node selectors and affinity rules.",
                ])

                continue

            # --------------------------------------------------
            # Volume problems
            # --------------------------------------------------

            volume_problem = any(keyword in message.lower() for keyword in [
                "failedmount",
                "failedattachvolume",
                "mountvolume",
                "persistentvolume",
                "persistentvolumeclaim",
            ])

            if volume_problem:

                diagnosis["severity"] = "CRITICAL"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Check PersistentVolume.",
                    "Check PersistentVolumeClaim.",
                    "Check volume configuration.",
                    "Check storage availability.",
                ])

                continue

            # --------------------------------------------------
            # Health probe problems
            # --------------------------------------------------

            probe_problem = any(keyword in message.lower() for keyword in [
                "unhealthy",
                "readiness probe",
                "liveness probe",
                "startup probe",
            ])

            if probe_problem:

                diagnosis["severity"] = "WARNING"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Check readiness probe.",
                    "Check liveness probe.",
                    "Check startup probe.",
                    "Check application health endpoint.",
                ])

                continue

            # --------------------------------------------------
            # Container restart / BackOff
            # --------------------------------------------------

            restart_problem = any(keyword in message.lower() for keyword in [
                "back-off restarting failed container",
                "backoff",
                "restarting failed container",
            ])

            if restart_problem:

                diagnosis["severity"] = "CRITICAL"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

                diagnosis["recommendations"].extend([
                    "Check current container logs.",
                    "Check previous container logs.",
                    "Check container startup configuration.",
                ])

                continue

            # --------------------------------------------------
            # Generic Warning event
            # --------------------------------------------------

            if event["type"] == "Warning":

                if diagnosis["severity"] != "CRITICAL":
                    diagnosis["severity"] = "WARNING"

                diagnosis["issues"].append(
                    f"{reason}: {message}"
                )

        # Remove duplicate recommendations
        diagnosis["recommendations"] = list(
            dict.fromkeys(
                diagnosis["recommendations"]
            )
        )

        # Remove duplicate issues
        diagnosis["issues"] = list(
            dict.fromkeys(
                diagnosis["issues"]
            )
        )

        return diagnosis

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

        if diagnosis["events"]:

            for event in diagnosis["events"]:

                print(
                    f"[{event['type']}] "
                    f"{event['reason']}"
                )

                print(
                    f"Message: "
                    f"{event['message']}"
                )

                print(
                    f"Count: "
                    f"{event['count']}"
                )

                print(
                    f"Source: "
                    f"{event['source']}"
                )

                print()

        else:

            print("No events found.")

        print()
        print("ISSUES")
        print("-" * 60)

        if diagnosis["issues"]:

            for issue in diagnosis["issues"]:
                print(f"- {issue}")

        else:

            print("No issues detected.")

        print()
        print("RECOMMENDATIONS")
        print("-" * 60)

        if diagnosis["recommendations"]:

            for recommendation in diagnosis["recommendations"]:
                print(f"- {recommendation}")

        else:

            print("No recommendations.")

        print()
        print("=" * 60)
