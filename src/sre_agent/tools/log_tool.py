from kubernetes import client
from kubernetes.client.rest import ApiException


class LogTool:
    """
    Kubernetes container log collection and pattern search tool.
    """

    PATTERNS = [
        "ERROR",
        "Exception",
        "Timeout",
        "Connection refused",
        "OutOfMemory",
        "Database connection failed",
        "HTTP 500",
        "HTTP 502",
        "HTTP 503",
        "500 Internal Server Error",
        "502 Bad Gateway",
        "503 Service Unavailable",
        "UndefinedTable",
        "UndefinedColumn",
        "relation does not exist",
        "database is unavailable",
        "connection failed",
    ]

    def get_pod_names(self, workload_name, namespace="default"):
        """
        Find pods whose names start with the workload name.
        Example:
            payment-api
            -> payment-api-7bb5cc8d6-7zxgg
            -> payment-api-7bb5cc8d6-df24v
        """

        pods = self.k8s.get_pods(namespace=namespace)

        matching_pods = []

        for pod in pods:
            pod_name = pod["name"]

            if pod_name.startswith(workload_name + "-"):
                matching_pods.append(pod_name)

        return matching_pods

    def get_workload_logs(
        self,
        workload_name,
        namespace="default",
        tail_lines=100,
    ):
        """
        Get logs from all pods belonging to a workload.
        """

        pod_names = self.get_pod_names(
            workload_name=workload_name,
            namespace=namespace,
        )

        results = []

        for pod_name in pod_names:

            pod_details = self.k8s.get_pod_details(
                pod_name=pod_name,
                namespace=namespace,
            )

            for container in pod_details["containers"]:

                container_name = container["name"]

                logs = self.get_logs(
                    pod_name=pod_name,
                    namespace=namespace,
                    container=container_name,
                    tail_lines=tail_lines,
                )

                results.append({
                    "pod": pod_name,
                    "container": container_name,
                    "logs": logs,
                })

        return results

    
    def __init__(self, kubernetes_tool):
        self.k8s = kubernetes_tool
        self.core_api = kubernetes_tool.core_api

    def classify_pattern(self, pattern):
        """
        Classify a detected log pattern.
        """

        if pattern in [
            "UndefinedTable",
            "UndefinedColumn",
            "Database connection failed",
            "database is unavailable",
            "connection failed",
            "Connection refused",
        ]:
            return "DATABASE"

        if pattern in [
            "OutOfMemory",
        ]:
            return "MEMORY"

        if pattern in [
            "Timeout",
        ]:
            return "TIMEOUT"

        if pattern in [
            "HTTP 500",
            "500 Internal Server Error",
            "HTTP 502",
            "502 Bad Gateway",
            "HTTP 503",
            "503 Service Unavailable",
        ]:
            return "HTTP_5XX"

        if pattern in [
            "Exception",
            "ERROR",
        ]:
            return "APPLICATION"

        return "UNKNOWN"

    def get_logs(
        self,
        pod_name,
        namespace="default",
        container=None,
        tail_lines=100,
    ):
        """
        Get current container logs.
        """

        try:
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
            )

            return logs

        except ApiException as e:
            print(
                f"Unable to get logs for pod={pod_name}, "
                f"container={container}: {e.reason}"
            )
            return ""

        except Exception as e:
            print(
                f"Unexpected error while getting logs "
                f"for pod={pod_name}: {e}"
            )
            return ""

    def get_previous_logs(
        self,
        pod_name,
        namespace="default",
        container=None,
        tail_lines=100,
    ):
        """
        Get logs from the previous container instance.
        Useful for CrashLoopBackOff and restarted containers.
        """

        try:
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
                previous=True,
            )

            return logs

        except ApiException as e:
            print(
                f"Unable to get previous logs for pod={pod_name}, "
                f"container={container}: {e.reason}"
            )
            return ""

        except Exception as e:
            print(
                f"Unexpected error while getting previous logs "
                f"for pod={pod_name}: {e}"
            )
            return ""

    def search_logs(self, logs):
        """
        Search logs for known incident patterns.

        Ignores normal traceback stack-frame lines and
        focuses on useful error evidence.
        """

        results = {
            "total_lines": 0,
            "matched_patterns": [],
            "matches": [],
        }

        if not logs:
            return results

        lines = logs.splitlines()
        results["total_lines"] = len(lines)

        for line in lines:

            stripped_line = line.strip()

            # Ignore Python traceback stack-frame lines.
            if (
                stripped_line.startswith("File ")
                or stripped_line.startswith("await ")
                or stripped_line.startswith("return ")
                or stripped_line.startswith("raise ")
            ):
                continue

            for pattern in self.PATTERNS:

                if pattern.lower() in line.lower():

                    if pattern not in results["matched_patterns"]:
                        results["matched_patterns"].append(pattern)

                    results["matches"].append({
                        "pattern": pattern,
                        "line": line,
                    })

        return results
