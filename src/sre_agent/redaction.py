"""
Secret redaction for anything that leaves the process (reports, LLM
prompts, logs). Logs and Kubernetes objects routinely contain
credentials; an investigation tool must not become an exfiltration path.
"""

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    # PEM private keys
    (re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ), REDACTED),
    # Authorization headers / bearer tokens
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 " + REDACTED),
    # JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    # AWS access key ids
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # Credentials embedded in URLs / DSNs: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+):[^@\s/]+@"), r"\1:" + REDACTED + "@"),
    # key=value / key: value for sensitive key names
    (re.compile(
        r"(?i)\b([\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
        r"access[_-]?key|private[_-]?key|credential)[\w.-]*)"
        r"(\"?\s*[:=]\s*\"?)([^\s\"',;}]+)"
    ), r"\1\2" + REDACTED),
]


def redact(text):
    if not text:
        return text

    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    return text
