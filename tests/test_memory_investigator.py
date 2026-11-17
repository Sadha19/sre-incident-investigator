from src.sre_agent.investigators.memory_investigator import MemoryInvestigator
from unittest.mock import MagicMock

def test_memory_normal():
    investigator = MemoryInvestigator()

    severity = investigator._get_severity(50)

    assert severity == "NORMAL"


def test_memory_high():
    investigator = MemoryInvestigator()

    severity = investigator._get_severity(85)

    assert severity == "HIGH"


def test_memory_critical():
    investigator = MemoryInvestigator()

    severity = investigator._get_severity(95)

    assert severity == "CRITICAL"


def test_memory_usage_high():
    investigator = MemoryInvestigator()

    result = investigator.investigate_usage_percentage(
        85,
        pod="payment-api"
    )

    assert result["pod"] == "payment-api"
    assert result["metric"] == "memory"
    assert result["current_usage"] == "85.00%"
    assert result["severity"] == "HIGH"


def test_memory_usage_critical():
    investigator = MemoryInvestigator()

    result = investigator.investigate_usage_percentage(
        95,
        pod="inventory-api"
    )

    assert result["pod"] == "inventory-api"
    assert result["current_usage"] == "95.00%"
    assert result["severity"] == "CRITICAL"


def test_oom_killed():
    investigator = MemoryInvestigator()

    result = investigator.investigate_oom(
        pod="payment-api"
    )

    assert result["pod"] == "payment-api"
    assert result["severity"] == "CRITICAL"
    assert result["current_usage"] == "OOMKilled"


def test_memory_limit_exceeded():
    investigator = MemoryInvestigator()

    result = investigator.investigate_memory_limit(
        pod="inventory-api"
    )

    assert result["pod"] == "inventory-api"
    assert result["severity"] == "CRITICAL"
    assert result["current_usage"] == "Memory limit exceeded"

def test_memory_investigation_percentage():
    investigator = MemoryInvestigator()

    investigator.prometheus = MagicMock()

    investigator.prometheus.get_pod_memory_usage.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "payment-api"
                },
                "value": [
                    1234567890,
                    "900"
                ]
            }
        ]
    }

    investigator.prometheus.get_pod_memory_limits.return_value = {
        "success": True,
        "result": [
            {
                "metric": {
                    "pod": "payment-api"
                },
                "value": [
                    1234567890,
                    "1000"
                ]
            }
        ]
    }

    result = investigator.investigate()

    assert len(result) == 1
    assert result[0]["pod"] == "payment-api"
    assert result[0]["current_usage"] == "90.00%"
    assert result[0]["usage_bytes"] == 900
    assert result[0]["limit_bytes"] == 1000
    assert result[0]["severity"] == "HIGH"
