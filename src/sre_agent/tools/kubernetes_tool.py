from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException


class KubernetesTool:
    """
    Wrapper around the Kubernetes Python client.

    This class provides the basic Kubernetes API operations
    that our SRE agent will need.
    """
    def get_pod_events(self, pod_name, namespace="default"):
        """
        Get Kubernetes events related to a specific pod.
        """

        events = self.core_api.list_namespaced_event(
            namespace=namespace
        )

        results = []

        for event in events.items:

            involved_object = event.involved_object

            if involved_object.name != pod_name:
                continue

            results.append({
                "name": event.metadata.name,
                "reason": event.reason,
                "message": event.message,
                "type": event.type,
                "count": event.count or 1,
                "first_timestamp": (
                    str(event.first_timestamp)
                    if event.first_timestamp
                    else None
                ),
                "last_timestamp": (
                    str(event.last_timestamp)
                    if event.last_timestamp
                    else None
                ),
                "source": (
                    event.source.component
                    if event.source
                    else None
                ),
            })

        return results

    def __init__(self):
        self._load_kubernetes_config()

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()

    # ---------------------------------------------------------
    # Kubernetes configuration
    # ---------------------------------------------------------

    def _load_kubernetes_config(self):
        """
        Load Kubernetes configuration.

        When running inside Kubernetes:
            use in-cluster configuration.

        When running locally:
            use ~/.kube/config
        """

        try:
            config.load_incluster_config()

            print("Kubernetes configuration: in-cluster")

        except ConfigException:

            config.load_kube_config()

            print("Kubernetes configuration: kubeconfig")

    # ---------------------------------------------------------
    # PODS
    # ---------------------------------------------------------

    def get_pods(self, namespace="default"):
        """
        Get all pods in a namespace.

        Returns:
            list[dict]
        """

        pods = self.core_api.list_namespaced_pod(
            namespace=namespace
        )

        results = []

        for pod in pods.items:

            results.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "status": pod.status.phase,
                    "node": pod.spec.node_name,
                }
            )

        return results

    # ---------------------------------------------------------
    # SINGLE POD
    # ---------------------------------------------------------

    def get_pod(self, pod_name, namespace="default"):
        """
        Get detailed information about one pod.
        """

        pod = self.core_api.read_namespaced_pod(
            name=pod_name,
            namespace=namespace
        )

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "node": pod.spec.node_name,
            "pod_ip": pod.status.pod_ip,
        }

    # ---------------------------------------------------------
    # DEPLOYMENTS
    # ---------------------------------------------------------

    def get_deployments(self, namespace="default"):
        """
        Get deployments and their desired/ready replicas.
        """

        deployments = self.apps_api.list_namespaced_deployment(
            namespace=namespace
        )

        results = []

        for deployment in deployments.items:

            results.append(
                {
                    "name": deployment.metadata.name,
                    "namespace": deployment.metadata.namespace,
                    "desired": deployment.spec.replicas or 0,
                    "ready": deployment.status.ready_replicas or 0,
                    "available": deployment.status.available_replicas or 0,
                }
            )

        return results

    # ---------------------------------------------------------
    # SERVICES
    # ---------------------------------------------------------

    def get_services(self, namespace="default"):
        """
        Get Kubernetes services in a namespace.
        """

        services = self.core_api.list_namespaced_service(
            namespace=namespace
        )

        results = []

        for service in services.items:

            results.append(
                {
                    "name": service.metadata.name,
                    "namespace": service.metadata.namespace,
                    "type": service.spec.type,
                    "cluster_ip": service.spec.cluster_ip,
                    "ports": [
                        {
                            "port": port.port,
                            "target_port": str(port.target_port),
                            "protocol": port.protocol,
                        }
                        for port in (service.spec.ports or [])
                    ],
                }
            )

        return results

    # ---------------------------------------------------------
    # NODES
    # ---------------------------------------------------------

    def get_nodes(self):
        """
        Get Kubernetes nodes and their Ready status.
        """

        nodes = self.core_api.list_node()

        results = []

        for node in nodes.items:

            ready_status = "Unknown"

            if node.status.conditions:

                for condition in node.status.conditions:

                    if condition.type == "Ready":

                        ready_status = condition.status

            results.append(
                {
                    "name": node.metadata.name,
                    "ready": ready_status,
                }
            )

        return results

    # ---------------------------------------------------------
    # DETAILED POD INFORMATION
    # ---------------------------------------------------------

    def get_pod_details(self, pod_name, namespace="default"):
        """
        Get detailed pod/container information.

        This method is primarily used by the Day 4
        Pod Health Investigator.
        """

        pod = self.core_api.read_namespaced_pod(
            name=pod_name,
            namespace=namespace
        )

        containers = []

        container_statuses = (
            pod.status.container_statuses or []
        )

        for status in container_statuses:

            last_termination = None

            if status.last_state and status.last_state.terminated:

                termination = status.last_state.terminated

                last_termination = {
                    "reason": termination.reason,
                    "exit_code": termination.exit_code,
                    "message": termination.message,
                    "started_at": (
                        str(termination.started_at)
                        if termination.started_at
                        else None
                    ),
                    "finished_at": (
                        str(termination.finished_at)
                        if termination.finished_at
                        else None
                    ),
                }

            waiting_reason = None

            if status.state and status.state.waiting:

                waiting_reason = status.state.waiting.reason

            running = False

            if status.state and status.state.running:

                running = True

            terminated = False

            if status.state and status.state.terminated:

                terminated = True

            containers.append(
                {
                    "name": status.name,
                    "ready": status.ready,
                    "restart_count": status.restart_count,
                    "waiting_reason": waiting_reason,
                    "running": running,
                    "terminated": terminated,
                    "last_termination": last_termination,
                }
            )

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "pod_ip": pod.status.pod_ip,
            "containers": containers,
        }
