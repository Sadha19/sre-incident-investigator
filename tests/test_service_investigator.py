
from unittest.mock import MagicMock
from kubernetes.client.exceptions import ApiException

from src.sre_agent.investigators.service_investigator import (
    ServiceInvestigator,
)


def make_investigator():
    investigator = ServiceInvestigator()
    investigator.core_api = MagicMock()
    investigator.discovery_api = MagicMock()
    return investigator


def setup_empty_backends(investigator):
    service = MagicMock()
    service.spec.selector = {"app": "payment-api"}
    investigator.core_api.read_namespaced_service.return_value = service

    endpoints = MagicMock()
    endpoints.subsets = []
    investigator.core_api.read_namespaced_endpoints.return_value = endpoints

    slices = MagicMock()
    slices.items = []
    investigator.discovery_api.list_namespaced_endpoint_slice.return_value = slices

    pods = MagicMock()
    pods.items = []
    investigator.core_api.list_namespaced_pod.return_value = pods


def test_service_with_no_endpoints_is_degraded():
    investigator = make_investigator()
    setup_empty_backends(investigator)

    result = investigator.investigate("payment-api", "sre-lab")

    assert result["service"] == "payment-api"
    assert result["status"] == "DEGRADED"
    assert "Service has zero active endpoints." in result["findings"]


def test_missing_service_is_critical():
    investigator = make_investigator()
    investigator.core_api.read_namespaced_service.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    result = investigator.investigate("missing-api", "sre-lab")

    assert result["status"] == "CRITICAL"
    assert "Service 'missing-api' does not exist." in result["findings"]


def test_selector_mismatch_is_detected():
    investigator = make_investigator()
    setup_empty_backends(investigator)

    pod = MagicMock()
    pod.metadata.labels = {"app": "another-api"}

    pods = MagicMock()
    pods.items = [pod]
    investigator.core_api.list_namespaced_pod.return_value = pods

    result = investigator.investigate("payment-api", "sre-lab")

    assert result["status"] == "DEGRADED"
    assert any("Selector mismatch" in item for item in result["findings"])
