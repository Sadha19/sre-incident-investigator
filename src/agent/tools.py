
import json

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from src.sre_agent.tools.prometheus_tool import PrometheusTool


def _load_kubernetes_config():
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()


def _serialize(obj):
    """Convert Kubernetes API objects to JSON-compatible data."""
    return client.ApiClient().sanitize_for_serialization(obj)



class InvestigationTools:
    def __init__(self):
        _load_kubernetes_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.prometheus = PrometheusTool()

    def get_pods(self, namespace="sre-lab"):
        result = self.core.list_namespaced_pod(namespace=namespace)
        return _serialize(result)

    def get_pod_logs(
        self, pod_name, namespace="sre-lab", tail_lines=100
    ):
        logs = self.core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines,
        )
        return {"pod": pod_name, "namespace": namespace, "logs": logs}

    def get_events(self, namespace="sre-lab"):
        result = self.core.list_namespaced_event(namespace=namespace)
        return _serialize(result)

    def query_prometheus(self, promql):
        return self.prometheus.query(promql)

    def get_deployment(self, deployment_name, namespace="sre-lab"):
        result = self.apps.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
        )
        return _serialize(result)

    def get_service(self, service_name, namespace="sre-lab"):
        result = self.core.read_namespaced_service(
            name=service_name,
            namespace=namespace,
        )
        return _serialize(result)
