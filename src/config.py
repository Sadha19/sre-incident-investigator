"""
Centralised, typed configuration.

Previously `src/config.py` read KODEKEY_* variables and raised at
*import* time, while the agent read LLM_* variables. Importing any
module that touched config crashed the deterministic (non-LLM) path.
Settings are loaded explicitly; invalid values are rejected as they
are loaded, while LLM credentials remain optional for other workflows.
"""

import math
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _env_int(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _env_float(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc


def _env_bool(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be 'true' or 'false'.")


def _env_list(name, default):
    value = os.getenv(name)
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # LLM (optional: deterministic investigation works without it)
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout_s: float = 60.0

    # Scope / safety
    default_namespace: str = "sre-lab"
    allowed_namespaces: list[str] = field(default_factory=lambda: ["sre-lab"])

    # Dependencies
    prometheus_url: str = "http://localhost:9090"
    k8s_timeout_s: float = 10.0
    prometheus_timeout_s: float = 10.0

    # Budgets
    investigation_deadline_s: float = 120.0
    log_tail_lines: int = 200

    # Remediation
    allow_writes: bool = False
    remediation_cooldown_s: int = 1800
    remediation_state_file: str = ".sre_agent_state.json"

    def __post_init__(self):
        positive_numbers = (
            ("LLM_TIMEOUT_SECONDS", self.llm_timeout_s),
            ("K8S_TIMEOUT_SECONDS", self.k8s_timeout_s),
            ("PROMETHEUS_TIMEOUT_SECONDS", self.prometheus_timeout_s),
            ("INVESTIGATION_DEADLINE_SECONDS", self.investigation_deadline_s),
        )
        for name, value in positive_numbers:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive number.")

        if (
            isinstance(self.log_tail_lines, bool)
            or not isinstance(self.log_tail_lines, int)
            or self.log_tail_lines <= 0
        ):
            raise ValueError("LOG_TAIL_LINES must be a positive integer.")

        if (
            isinstance(self.remediation_cooldown_s, bool)
            or not isinstance(self.remediation_cooldown_s, int)
            or self.remediation_cooldown_s < 0
        ):
            raise ValueError(
                "REMEDIATION_COOLDOWN_SECONDS must be a non-negative integer."
            )

    @property
    def llm_enabled(self):
        return bool(self.llm_api_key and self.llm_model)

    def require_llm(self):
        if not self.llm_enabled:
            raise ValueError(
                "LLM_API_KEY and LLM_MODEL must be set to use the AI agent."
            )


def load_settings():
    load_dotenv()

    default_ns = os.getenv("SRE_DEFAULT_NAMESPACE", "sre-lab")
    allow_writes = _env_bool("SRE_AGENT_ALLOW_WRITES", False)

    return Settings(
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_model=os.getenv("LLM_MODEL"),
        llm_timeout_s=_env_float("LLM_TIMEOUT_SECONDS", 60.0),
        default_namespace=default_ns,
        allowed_namespaces=_env_list("SRE_ALLOWED_NAMESPACES", [default_ns]),
        prometheus_url=os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
        k8s_timeout_s=_env_float("K8S_TIMEOUT_SECONDS", 10.0),
        prometheus_timeout_s=_env_float("PROMETHEUS_TIMEOUT_SECONDS", 10.0),
        investigation_deadline_s=_env_float("INVESTIGATION_DEADLINE_SECONDS", 120.0),
        log_tail_lines=_env_int("LOG_TAIL_LINES", 200),
        allow_writes=allow_writes,
        remediation_cooldown_s=_env_int("REMEDIATION_COOLDOWN_SECONDS", 1800),
        remediation_state_file=os.getenv(
            "REMEDIATION_STATE_FILE", ".sre_agent_state.json"
        ),
    )
