from unittest.mock import MagicMock

from src.sre_agent.investigators.cpu_investigator import CPUInvestigator


def test_cpu_normal():
    investigator = CPUInvestigator()

    severity = investigator._get_severity(50)

    assert severity == "NORMAL"


def test_cpu_high():
    investigator = CPUInvestigator()

    severity = investigator._get_severity(85)

    assert severity == "HIGH"


def test_cpu_critical():
    investigator = CPUInvestigator()

    severity = investigator._get_severity(95)

    assert severity == "CRITICAL"


def test_cpu_investigation_high():
    investigator = CPUInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_pod_cpu_usage.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "payment-api"
                },
                "value": [
                    1234567890,
                    "0.85"
                ]
            }
        ]
    }

    result = investigator.investigate()

    assert len(result) == 1
    assert result[0]["pod"] == "payment-api"
    assert result[0]["metric"] == "cpu"
    assert result[0]["current_usage"] == "85.00%"
    assert result[0]["severity"] == "HIGH"


def test_cpu_investigation_critical():
    investigator = CPUInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_pod_cpu_usage.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "inventory-api"
                },
                "value": [
                    1234567890,
                    "0.95"
                ]
            }
        ]
    }

    result = investigator.investigate()

    assert len(result) == 1
    assert result[0]["pod"] == "inventory-api"
    assert result[0]["current_usage"] == "95.00%"
    assert result[0]["severity"] == "CRITICAL"


def test_cpu_investigation_prometheus_failure():
    investigator = CPUInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_pod_cpu_usage.return_value = {
        "success": False,
        "error": "Prometheus unavailable"
    }

    result = investigator.investigate()

    assert result["metric"] == "cpu"
    assert result["severity"] == "UNKNOWN"
    assert result["error"] == "Prometheus unavailable"
def test_cpu_throttling_detected():
    investigator = CPUInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_cpu_throttling.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "payment-api"
                },
                "value": [
                    1234567890,
                    "0.15"
                ]
            }
        ]
    }

    result = investigator.investigate_throttling()

    assert len(result) == 1
    assert result[0]["pod"] == "payment-api"
    assert result[0]["metric"] == "cpu_throttling"
    assert result[0]["severity"] == "HIGH"


def test_cpu_throttling_normal():
    investigator = CPUInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_cpu_throttling.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "frontend"
                },
                "value": [
                    1234567890,
                    "0"
                ]
            }
        ]
    }

    result = investigator.investigate_throttling()

    assert len(result) == 1
    assert result[0]["pod"] == "frontend"
    assert result[0]["severity"] == "NORMAL"

