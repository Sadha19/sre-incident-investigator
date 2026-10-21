"""
Observability for the agent itself.

An incident tool that cannot explain its own behaviour is a liability
during an incident. This module provides:

    - JSON structured logging with a per-investigation correlation id
    - a tiny in-process metrics registry (counters + timings) that can
      be written in Prometheus textfile-collector format
    - `span()` context manager: logs duration, records metrics, and
      creates an OpenTelemetry span when the SDK is configured
"""

import contextvars
import json
import logging
import sys
import threading
import time
import uuid
from contextlib import contextmanager

try:
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("sre_agent")
except Exception:  # pragma: no cover - otel is optional
    _tracer = None


correlation_id = contextvars.ContextVar("correlation_id", default="-")


def new_correlation_id():
    value = uuid.uuid4().hex[:12]
    correlation_id.set(value)
    return value


_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level="INFO"):
    """Logs go to stderr so stdout stays clean for reports / JSON."""

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # urllib3 retry chatter is noise; our own retry logs cover it.
    logging.getLogger("urllib3").setLevel(logging.ERROR)


class Metrics:
    """Thread-safe counters and duration summaries keyed by label set."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters = {}
        self.timings = {}

    @staticmethod
    def _key(name, labels):
        return (name, tuple(sorted((labels or {}).items())))

    def inc(self, name, labels=None, value=1):
        with self._lock:
            key = self._key(name, labels)
            self.counters[key] = self.counters.get(key, 0) + value

    def observe(self, name, seconds, labels=None):
        with self._lock:
            key = self._key(name, labels)
            count, total = self.timings.get(key, (0, 0.0))
            self.timings[key] = (count + 1, total + seconds)

    def reset(self):
        with self._lock:
            self.counters.clear()
            self.timings.clear()

    def snapshot(self):
        with self._lock:
            return {
                "counters": [
                    {"name": n, "labels": dict(l), "value": v}
                    for (n, l), v in self.counters.items()
                ],
                "timings": [
                    {"name": n, "labels": dict(l), "count": c,
                     "sum_seconds": round(s, 4)}
                    for (n, l), (c, s) in self.timings.items()
                ],
            }

    def to_prometheus_text(self, prefix="sre_agent"):
        """Render for node_exporter's textfile collector."""

        def fmt_labels(labels):
            if not labels:
                return ""
            inner = ",".join(
                f'{k}="{str(v).replace(chr(34), "")}"' for k, v in labels
            )
            return "{" + inner + "}"

        lines = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{prefix}_{name}_total{fmt_labels(labels)} {value}")
            for (name, labels), (count, total) in sorted(self.timings.items()):
                lines.append(f"{prefix}_{name}_seconds_count{fmt_labels(labels)} {count}")
                lines.append(f"{prefix}_{name}_seconds_sum{fmt_labels(labels)} {total:.6f}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()

logger = logging.getLogger("sre_agent")


@contextmanager
def span(name, **attributes):
    """
    Time an operation. Records `<name>` duration and an outcome-labelled
    counter, emits a debug log, and opens an OTel span if available.
    """

    start = time.monotonic()
    outcome = "success"

    otel_cm = (
        _tracer.start_as_current_span(name, attributes={
            k: str(v) for k, v in attributes.items()
        })
        if _tracer is not None
        else None
    )
    otel_span = otel_cm.__enter__() if otel_cm else None

    try:
        yield
    except Exception as exc:
        outcome = "error"
        if otel_span is not None:
            otel_span.record_exception(exc)
        raise
    finally:
        elapsed = time.monotonic() - start
        labels = {"op": name, "outcome": outcome}
        METRICS.observe("operation_duration", elapsed, {"op": name})
        METRICS.inc("operations", labels)
        logger.debug(
            "span finished",
            extra={"span": name, "outcome": outcome,
                   "duration_ms": round(elapsed * 1000, 1), **attributes},
        )
        if otel_cm is not None:
            otel_cm.__exit__(None, None, None)
