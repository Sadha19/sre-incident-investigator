from unittest.mock import MagicMock

import pytest

import src.config as config
from src.config import Settings, load_settings


ENVIRONMENT_VARIABLES = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "SRE_DEFAULT_NAMESPACE",
    "SRE_ALLOWED_NAMESPACES",
    "PROMETHEUS_URL",
    "K8S_TIMEOUT_SECONDS",
    "PROMETHEUS_TIMEOUT_SECONDS",
    "INVESTIGATION_DEADLINE_SECONDS",
    "LOG_TAIL_LINES",
    "SRE_AGENT_ALLOW_WRITES",
    "REMEDIATION_COOLDOWN_SECONDS",
    "REMEDIATION_STATE_FILE",
)


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_load_settings_defaults_keep_llm_optional_and_writes_disabled():
    settings = load_settings()

    assert settings.default_namespace == "sre-lab"
    assert settings.allowed_namespaces == ["sre-lab"]
    assert settings.prometheus_url == "http://localhost:9090"
    assert settings.allow_writes is False
    assert settings.llm_enabled is False
    with pytest.raises(ValueError, match="LLM_API_KEY and LLM_MODEL"):
        settings.require_llm()


def test_load_settings_reads_environment_overrides(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("SRE_DEFAULT_NAMESPACE", "staging")
    monkeypatch.setenv("SRE_ALLOWED_NAMESPACES", "staging, production")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "15.5")
    monkeypatch.setenv("LOG_TAIL_LINES", "75")
    monkeypatch.setenv("SRE_AGENT_ALLOW_WRITES", "TRUE")

    settings = load_settings()

    assert settings.llm_enabled is True
    assert settings.llm_timeout_s == 15.5
    assert settings.default_namespace == "staging"
    assert settings.allowed_namespaces == ["staging", "production"]
    assert settings.log_tail_lines == 75
    assert settings.allow_writes is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_TIMEOUT_SECONDS", "nan"),
        ("K8S_TIMEOUT_SECONDS", "-1"),
        ("PROMETHEUS_TIMEOUT_SECONDS", "inf"),
        ("INVESTIGATION_DEADLINE_SECONDS", "0"),
        ("LOG_TAIL_LINES", "0"),
        ("LOG_TAIL_LINES", "not-an-integer"),
        ("REMEDIATION_COOLDOWN_SECONDS", "-1"),
        ("REMEDIATION_COOLDOWN_SECONDS", "not-an-integer"),
        ("SRE_AGENT_ALLOW_WRITES", "yes"),
    ],
)
def test_load_settings_rejects_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_settings()


def test_agent_uses_central_settings_for_llm_client(monkeypatch):
    from src.agent import agent

    settings = Settings(
        llm_api_key="test-only-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="test-model",
        llm_timeout_s=12.0,
    )
    openai = MagicMock()
    monkeypatch.setattr(agent, "load_settings", lambda: settings)
    monkeypatch.setattr(agent, "OpenAI", openai)
    monkeypatch.setattr(agent, "InvestigationTools", MagicMock)

    instance = agent.SREAgent()

    assert instance.model == "test-model"
    openai.assert_called_once_with(
        api_key="test-only-key",
        base_url="https://llm.example.test/v1",
        timeout=12.0,
    )
