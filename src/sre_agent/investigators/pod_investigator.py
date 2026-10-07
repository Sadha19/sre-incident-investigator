from src.sre_agent.tools.kubernetes_tool import KubernetesTool


class PodHealthInvestigator:
    """
    Investigates Kubernetes pod health.

    Detects:
        - Pending
        - Running
        - Failed
        - CrashLoopBackOff
        - ImagePullBackOff
        - OOMKilled
        - Restart counts
        - Container termination reasons
    """

    def __init__(self, kubernetes_tool=None):

        if kubernetes_tool:

            self.k8s = kubernetes_tool

        else:

            self.k8s = KubernetesTool()

    # ---------------------------------------------------------
    # INVESTIGATE ONE POD
    # ---------------------------------------------------------

    def investigate_pod(
        self,
        pod_name,
        namespace="default"
    ):
        """
        Investigate a single Kubernetes pod.
        """

        pod = self.k8s.get_pod_details(
            pod_name=pod_name,
            namespace=namespace
        )

        diagnosis = {
            "pod": pod["name"],
            "namespace": pod["namespace"],
            "status": pod["phase"],
            "severity": "INFO",
            "issues": [],
            "containers": [],
            "recommendations": [],
        }

        # -----------------------------------------------------
        # Analyze pod phase
        # -----------------------------------------------------

        if pod["phase"] == "Pending":

            diagnosis["severity"] = "WARNING"

            diagnosis["issues"].append(
                "Pod is stuck in Pending state."
            )

            diagnosis["recommendations"].append(
                "Check pod scheduling events and node resources."
            )

        elif pod["phase"] == "Failed":

            diagnosis["severity"] = "CRITICAL"

            diagnosis["issues"].append(
                "Pod has entered Failed state."
            )

            diagnosis["recommendations"].append(
                "Check pod events and container termination reason."
            )

        elif pod["phase"] == "Running":

            diagnosis["severity"] = "INFO"

        else:

            diagnosis["severity"] = "WARNING"

            diagnosis["issues"].append(
                f"Unexpected pod phase: {pod['phase']}"
            )

        # -----------------------------------------------------
        # Analyze containers
        # -----------------------------------------------------

        for container in pod["containers"]:

            container_result = {
                "name": container["name"],
                "ready": container["ready"],
                "restart_count": container["restart_count"],
                "status": "Healthy",
            }

            # -------------------------------------------------
            # Restart count
            # -------------------------------------------------

            restart_count = container["restart_count"]

            if restart_count > 0:

                diagnosis["issues"].append(
                    f"Container {container['name']} "
                    f"has restarted {restart_count} time(s)."
                )

                if restart_count >= 10:

                    diagnosis["severity"] = "CRITICAL"

                elif restart_count >= 3:

                    if diagnosis["severity"] != "CRITICAL":

                        diagnosis["severity"] = "WARNING"

            # -------------------------------------------------
            # Waiting state
            # -------------------------------------------------

            waiting_reason = container["waiting_reason"]

            if waiting_reason:

                container_result["status"] = waiting_reason

                if waiting_reason == "CrashLoopBackOff":

                    diagnosis["severity"] = "CRITICAL"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        f"is in CrashLoopBackOff."
                    )

                    diagnosis["recommendations"].extend(
                        [
                            "Check current container logs.",
                            "Check previous container logs.",
                            "Check application startup configuration.",
                            "Check environment variables and secrets.",
                        ]
                    )

                elif waiting_reason == "ImagePullBackOff":

                    diagnosis["severity"] = "CRITICAL"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        f"is in ImagePullBackOff."
                    )

                    diagnosis["recommendations"].extend(
                        [
                            "Verify container image name.",
                            "Verify image tag.",
                            "Check image registry availability.",
                            "Check imagePullSecrets.",
                        ]
                    )

                elif waiting_reason == "RunContainerError":

                    diagnosis["severity"] = "CRITICAL"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        f"is in RunContainerError."
                    )

                    diagnosis["recommendations"].extend(
                        [
                            "Verify ConfigMaps / Secrets",
                            "Verify Container Command or Entrypoint Arguments",
                            "Check Volume Mount.",
                        ]
                    )
                elif waiting_reason == "ErrImagePull":

                    diagnosis["severity"] = "CRITICAL"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        f"cannot pull its image."
                    )

                    diagnosis["recommendations"].append(
                        "Check image name, tag and registry credentials."
                    )

                else:

                    diagnosis["severity"] = "WARNING"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        f"is waiting: {waiting_reason}"
                    )

            # -------------------------------------------------
            # Last termination
            # -------------------------------------------------

            termination = container["last_termination"]

            if termination:

                reason = termination["reason"]

                container_result["last_termination"] = termination

                if reason == "OOMKilled":

                    diagnosis["severity"] = "CRITICAL"

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        "was terminated because of OOMKilled."
                    )

                    diagnosis["recommendations"].extend(
                        [
                            "Check container memory limits.",
                            "Check container memory usage.",
                            "Check for application memory leaks.",
                            "Review Kubernetes resource limits.",
                        ]
                    )

                elif reason == "Error":

                    diagnosis["issues"].append(
                        f"Container {container['name']} "
                        "was previously terminated with an error."
                    )

                    diagnosis["recommendations"].extend(
                        [
                            "Check previous container logs.",
                            "Check application startup errors.",
                        ]
                    )

            # -------------------------------------------------
            # Container not ready
            # -------------------------------------------------

            if not container["ready"]:

                if diagnosis["severity"] == "INFO":

                    diagnosis["severity"] = "WARNING"

                diagnosis["issues"].append(
                    f"Container {container['name']} is not ready."
                )

                diagnosis["recommendations"].append(
                    "Check readiness probe and application health."
                )

            diagnosis["containers"].append(
                container_result
            )

        # -----------------------------------------------------
        # Remove duplicate recommendations
        # -----------------------------------------------------

        diagnosis["recommendations"] = list(
            dict.fromkeys(
                diagnosis["recommendations"]
            )
        )

        return diagnosis

    # ---------------------------------------------------------
    # INVESTIGATE ALL PODS
    # ---------------------------------------------------------

    def investigate_namespace(
        self,
        namespace="default"
    ):
        """
        Investigate all pods in a namespace.
        """

        pods = self.k8s.get_pods(
            namespace=namespace
        )

        results = []

        for pod in pods:

            result = self.investigate_pod(
                pod_name=pod["name"],
                namespace=namespace
            )

            results.append(result)

        return results

    # ---------------------------------------------------------
    # PRINT HUMAN-READABLE REPORT
    # ---------------------------------------------------------

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

            print(
                f"Name: {container['name']}"
            )

            print(
                f"Ready: {container['ready']}"
            )

            print(
                f"Restarts: {container['restart_count']}"
            )

            print(
                f"Status: {container['status']}"
            )

            if "last_termination" in container:

                termination = container[
                    "last_termination"
                ]

                print(
                    f"Last Termination: "
                    f"{termination['reason']}"
                )

                print(
                    f"Exit Code: "
                    f"{termination['exit_code']}"
                )

        print()
        print("Issues")
        print("-" * 40)

        if diagnosis["issues"]:

            for issue in diagnosis["issues"]:

                print(f"- {issue}")

        else:

            print("No issues detected.")

        print()
        print("Recommendations")
        print("-" * 40)

        if diagnosis["recommendations"]:

            for recommendation in diagnosis[
                "recommendations"
            ]:

                print(f"- {recommendation}")

        else:

            print("No recommendations.")

        print()
        print("=" * 60)
