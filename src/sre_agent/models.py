"""
Shared data model for every investigator.

Before this module existed each investigator used its own vocabulary
(INFO/WARNING/CRITICAL, NORMAL/HIGH/CRITICAL, HEALTHY/DEGRADED, ...),
which made aggregation error-prone. Everything now speaks `Severity`.
"""

from dataclasses import asdict, dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """
    Ordered severity. Comparisons work naturally:
        Severity.HIGH > Severity.MEDIUM

    UNKNOWN sits between OK and LOW on purpose: "we could not look"
    must never be reported as healthy, but it is not evidence of an
    outage either.
    """

    OK = 0
    UNKNOWN = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5

    @classmethod
    def parse(cls, value):
        """Map legacy / free-text labels onto the canonical scale."""

        if isinstance(value, Severity):
            return value

        aliases = {
            "OK": cls.OK,
            "INFO": cls.OK,
            "NORMAL": cls.OK,
            "HEALTHY": cls.OK,
            "UNKNOWN": cls.UNKNOWN,
            "LOW": cls.LOW,
            "WARNING": cls.MEDIUM,
            "MEDIUM": cls.MEDIUM,
            "DEGRADED": cls.HIGH,
            "HIGH": cls.HIGH,
            "CRITICAL": cls.CRITICAL,
        }

        return aliases.get(str(value).upper(), cls.UNKNOWN)


def max_severity(*values):
    """
    Escalate-only aggregation. Severity can go up, never down, no
    matter what order evidence arrives in.
    """

    parsed = [Severity.parse(v) for v in values if v is not None]
    return max(parsed, default=Severity.OK)


@dataclass
class SourceStatus:
    """
    Health of a data source used during an investigation.

    Graceful degradation is only safe when it is *visible*: a report
    that silently skipped logs looks identical to one where logs were
    clean. Every source therefore records whether it was available.
    """

    name: str
    available: bool
    error: str = None
    duration_ms: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class Finding:
    source: str
    severity: Severity
    summary: str
    target: str = None
    evidence: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    category: str = None

    def to_dict(self):
        data = asdict(self)
        data["severity"] = self.severity.name
        return data
