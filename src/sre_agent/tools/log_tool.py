import re
from collections import Counter

from src.sre_agent.models import Severity
from src.sre_agent.redaction import redact


class LogPattern:
    def __init__(self, name, regex, category, severity):
        self.name = name
        self.regex = re.compile(regex, re.IGNORECASE)
        self.category = category
        self.severity = severity


class LogTool:
    """
    Kubernetes container log collection and pattern search.

    Changes vs. the original:
        - Patterns are word-bounded regexes ("ERROR" no longer matches
          "error_count=0" or "errors_total").
        - Each pattern carries a category and a base severity; generic
          ERROR/Exception lines alone are MEDIUM at most. Previously a
          single "ERROR" line made the whole incident CRITICAL.
        - Fetch failures raise DataSourceError (from KubernetesTool)
          instead of returning "" - an unreadable log is not a clean log.
        - Matched lines are redacted and capped before leaving the tool.
    """

    PATTERNS = [
        LogPattern("OutOfMemory", r"\bOutOfMemory(Error)?\b|\bOOM[- ]?killed\b|\bCannot allocate memory\b", "MEMORY", Severity.HIGH),
        LogPattern("Database connection failed", r"\bdatabase connection failed\b|\bcould not connect to (the )?(database|server)\b", "DATABASE", Severity.HIGH),
        LogPattern("database is unavailable", r"\bdatabase is unavailable\b", "DATABASE", Severity.HIGH),
        LogPattern("Connection refused", r"\bconnection refused\b", "DEPENDENCY", Severity.HIGH),
        LogPattern("connection failed", r"\bconnection failed\b", "DEPENDENCY", Severity.MEDIUM),
        LogPattern("UndefinedTable", r"\bUndefinedTable\b|\brelation \"?[\w.]+\"? does not exist\b", "DATABASE", Severity.HIGH),
        LogPattern("UndefinedColumn", r"\bUndefinedColumn\b", "DATABASE", Severity.HIGH),
        LogPattern("Timeout", r"\btime[d]?[- ]?out\b|\bdeadline exceeded\b", "TIMEOUT", Severity.MEDIUM),
        LogPattern("HTTP 5xx", r"\bHTTP[/ ]?\d?\.?\d? 5\d\d\b|\b\" 5\d\d \d|\b50[0234] (Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout)\b", "HTTP_5XX", Severity.MEDIUM),
        LogPattern("Panic/Fatal", r"\b(panic|FATAL|segmentation fault)\b", "APPLICATION", Severity.HIGH),
        LogPattern("Exception", r"\b\w*(Exception|Error)\b:|^Traceback \(most recent call last\)", "APPLICATION", Severity.MEDIUM),
        LogPattern("ERROR", r"(^|[\s\[|])(ERROR|ERR)([\s\]|:]|$)", "APPLICATION", Severity.LOW),
    ]

    # Python stack-frame lines carry no signal of their own.
    _NOISE_PREFIXES = ("File ", "await ", "return ", "raise ", "^")

    MAX_MATCHES = 50
    MAX_LINE_LENGTH = 500
    # A pattern seen this many times is escalated one level.
    ESCALATION_THRESHOLD = 10

    def __init__(self, kubernetes_tool):
        self.k8s = kubernetes_tool

    # ---------------------------------------------------------
    # Collection
    # ---------------------------------------------------------

    def get_logs(self, pod_name, namespace="default", container=None, tail_lines=100):
        return self.k8s.get_container_logs(
            pod_name=pod_name, namespace=namespace,
            container=container, tail_lines=tail_lines,
        )

    def get_previous_logs(self, pod_name, namespace="default", container=None, tail_lines=100):
        """Logs of the previous container instance (CrashLoopBackOff, OOMKilled)."""
        return self.k8s.get_container_logs(
            pod_name=pod_name, namespace=namespace,
            container=container, tail_lines=tail_lines, previous=True,
        )

    # ---------------------------------------------------------
    # Analysis
    # ---------------------------------------------------------

    @classmethod
    def classify_pattern(cls, name):
        for pattern in cls.PATTERNS:
            if pattern.name == name:
                return pattern.category
        return "UNKNOWN"

    def search_logs(self, logs):
        results = {
            "total_lines": 0,
            "matched_patterns": [],
            "pattern_counts": {},
            "categories": [],
            "severity": Severity.OK.name,
            "matches": [],
            "matches_truncated": False,
        }

        if not logs:
            return results

        lines = logs.splitlines()
        results["total_lines"] = len(lines)

        counts = Counter()
        severity = Severity.OK

        for line in lines:
            stripped = line.strip()

            # Drop the kubelet timestamp prefix before noise checks.
            content = stripped.split(" ", 1)[1] if stripped[:4].isdigit() and " " in stripped else stripped

            if content.startswith(self._NOISE_PREFIXES):
                continue

            # First (most specific) pattern wins, so a single line is
            # not counted as both "Connection refused" and "ERROR".
            for pattern in self.PATTERNS:
                if not pattern.regex.search(content):
                    continue

                counts[pattern.name] += 1
                severity = max(severity, pattern.severity)

                if len(results["matches"]) < self.MAX_MATCHES:
                    results["matches"].append({
                        "pattern": pattern.name,
                        "category": pattern.category,
                        "line": redact(line[: self.MAX_LINE_LENGTH]),
                    })
                else:
                    results["matches_truncated"] = True
                break

        for pattern in self.PATTERNS:
            if counts[pattern.name] >= self.ESCALATION_THRESHOLD:
                severity = max(
                    severity, Severity(min(pattern.severity + 1, Severity.HIGH))
                )

        results["matched_patterns"] = [p.name for p in self.PATTERNS if counts[p.name]]
        results["pattern_counts"] = dict(counts)
        results["categories"] = sorted({
            p.category for p in self.PATTERNS if counts[p.name]
        })
        results["severity"] = severity.name

        return results
