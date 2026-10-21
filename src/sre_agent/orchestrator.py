"""
Deterministic incident investigation pipeline.

    resolve workload -> per-pod evidence (health, events, logs)
                     -> service routing check
                     -> resource metrics
                     -> correlation (probable causes)
                     -> impact-adjusted severity
                     -> structured report

Design principles:
    * Bulkheads: each pod and each data source is isolated. One failure
      degrades the report; it never aborts the investigation.
    * Visible degradation: every source records whether it answered,
      and report confidence drops when sources are missing.
    * Bounded work: overall deadline, bounded concurrency, capped logs.
    * Idempotent identity: the incident id is a fingerprint of the
      workload + finding signatures, so re-running the investigation
      on the same unchanged failure yields the same id (safe to use as
      a dedupe key for paging / ticketing / remediation).
"""

import contextvars
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.sre_agent.investigators.cpu_investigator import CPUInvestigator
from src.sre_agent.investigators.event_investigator import EventInvestigator
from src.sre_agent.investigators.memory_investigator import MemoryInvestigator
from src.sre_agent.investigators.pod_investigator import PodHealthInvestigator
from src.sre_agent.investigators.service_investigator import ServiceInvestigator
from src.sre_agent.models import Severity, SourceStatus, max_severity
from src.sre_agent.observability import METRICS, correlation_id, span
from src.sre_agent.tools.kubernetes_tool import DataSourceError
from src.sre_agent.tools.log_tool import LogTool

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2.0"
MAX_POD_WORKERS = 4
MAX_PODS = 20


class _Sources:
    """Collects SourceStatus entries; first failure of a source wins."""

    def __init__(self):
        self._items = {}

    def ok(self, name, duration_ms=0.0):
        self._items.setdefault(name, SourceStatus(name, True, None, round(duration_ms, 1)))

    def failed(self, name, error):
        current = self._items.get(name)
        if current is None or current.available:
            self._items[name] = SourceStatus(name, False, str(error))

    def items(self):
        return [s.to_dict() for s in self._items.values()]

    def unavailable(self):
        return [s.name for s in self._items.values() if not s.available]


def _timed(func):
    start = time.monotonic()
    result = func()
    return result, (time.monotonic() - start) * 1000


class IncidentInvestigator:

    def __init__(
        self,
        k8s,
        prometheus=None,
        service_investigator=None,
        log_tail_lines=200,
        deadline_s=120.0,
        clock=time.monotonic,
    ):
        self.k8s = k8s
        self.prometheus = prometheus
        self.pod_investigator = PodHealthInvestigator(k8s)
        self.event_investigator = EventInvestigator(k8s)
        self.log_tool = LogTool(k8s)
        self.service_investigator = service_investigator
        self.log_tail_lines = log_tail_lines
        self.deadline_s = deadline_s
        self._clock = clock

    # ---------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------

    def investigate(self, workload, namespace):
        started = datetime.now(timezone.utc)
        t0 = self._clock()
        deadline = t0 + self.deadline_s
        sources = _Sources()

        report = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": None,
            "correlation_id": correlation_id.get(),
            "workload": workload,
            "namespace": namespace,
            "started_at": started.isoformat(),
            "status": "COMPLETE",
            "severity": Severity.OK.name,
            "severity_reasons": [],
            "confidence": "HIGH",
            "resolution": None,
            "pods": [],
            "service": None,
            "metrics": {},
            "probable_causes": [],
            "recommendations": [],
            "data_sources": [],
            "notes": [],
        }

        with span("investigation", workload=workload, namespace=namespace):

            # 1. Resolve workload -> pods. Without this nothing else works.
            try:
                (pod_names, resolution), ms = _timed(
                    lambda: self.k8s.get_workload_pods(workload, namespace)
                )
                sources.ok("kubernetes.pods", ms)
            except DataSourceError as exc:
                sources.failed("kubernetes.pods", exc)
                report.update(
                    status="FAILED",
                    severity=Severity.UNKNOWN.name,
                    confidence="NONE",
                    notes=[f"Could not list pods: {exc}"],
                )
                return self._finalize(report, sources, t0)

            report["resolution"] = resolution

            if resolution["method"] == "name_prefix":
                report["notes"].append(
                    "No Deployment/StatefulSet/DaemonSet found with this name; "
                    "pods were matched by name prefix (lower confidence)."
                )

            if not pod_names:
                report["severity_reasons"].append("No pods found for workload.")
                report["severity"] = Severity.HIGH.name if resolution["kind"] else Severity.UNKNOWN.name
                report["notes"].append(f"No pods found for workload '{workload}'.")

            if len(pod_names) > MAX_PODS:
                report["notes"].append(
                    f"{len(pod_names)} pods found; investigating the first {MAX_PODS}."
                )
                pod_names = pod_names[:MAX_PODS]

            # 2. Per-pod evidence, bounded concurrency, bulkheaded.
            report["pods"] = self._investigate_pods(pod_names, namespace, sources, deadline, report)

            # 3. Service routing.
            if self.service_investigator is not None and self._clock() < deadline:
                report["service"] = self._investigate_service(workload, namespace, sources)

            # 4. Resource metrics.
            if self.prometheus is not None and pod_names and self._clock() < deadline:
                report["metrics"] = self._investigate_metrics(namespace, pod_names, sources)

            if self._clock() >= deadline:
                report["status"] = "PARTIAL"
                report["notes"].append(
                    f"Investigation deadline of {self.deadline_s:.0f}s reached; results are partial."
                )

            # 5. Correlate + score.
            report["probable_causes"] = correlate(report)
            self._score(report)

        return self._finalize(report, sources, t0)

    # ---------------------------------------------------------
    # Pods
    # ---------------------------------------------------------

    def _investigate_pods(self, pod_names, namespace, sources, deadline, report):
        results = {}

        with ThreadPoolExecutor(max_workers=min(MAX_POD_WORKERS, max(1, len(pod_names)))) as pool:
            futures = {
                pool.submit(
                    contextvars.copy_context().run,
                    self._investigate_one_pod, name, namespace, deadline,
                ): name
                for name in pod_names
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    pod_result, pod_sources = future.result()
                except Exception as exc:  # last-resort bulkhead
                    logger.exception("pod investigation crashed", extra={"pod": name})
                    pod_result = {"pod": name, "severity": Severity.UNKNOWN.name,
                                  "error": f"{type(exc).__name__}: {exc}"}
                    pod_sources = [("pod_investigation", False, str(exc))]

                for source, ok, err in pod_sources:
                    sources.ok(source) if ok else sources.failed(source, err)

                results[name] = pod_result

        skipped = [n for n in pod_names if results.get(n, {}).get("skipped")]
        if skipped:
            report["status"] = "PARTIAL"

        return [results[n] for n in pod_names]

    def _investigate_one_pod(self, pod_name, namespace, deadline):
        pod_sources = []
        result = {"pod": pod_name, "severity": Severity.UNKNOWN.name}

        if self._clock() >= deadline:
            result["skipped"] = True
            return result, pod_sources

        # Pod details. A 404 here is a normal race during rollouts.
        try:
            details = self.k8s.get_pod_details(pod_name, namespace)
            pod_sources.append(("kubernetes.pods", True, None))
        except DataSourceError as exc:
            if getattr(exc, "status", None) == 404:
                result.update(severity=Severity.OK.name, note="Pod disappeared during investigation (likely rollout).")
                return result, pod_sources
            pod_sources.append(("kubernetes.pods", False, str(exc)))
            result["error"] = str(exc)
            return result, pod_sources

        health = self.pod_investigator.investigate_pod(pod_name, namespace, pod=details)
        result["health"] = health

        try:
            result["events"] = self.event_investigator.investigate_pod(pod_name, namespace)
            pod_sources.append(("kubernetes.events", True, None))
        except DataSourceError as exc:
            pod_sources.append(("kubernetes.events", False, str(exc)))
            result["events"] = None

        result["logs"] = self._collect_logs(details, namespace, pod_sources)

        result["severity"] = max_severity(
            health["severity"],
            result["events"]["severity"] if result["events"] else None,
            result["logs"]["severity"],
        ).name

        return result, pod_sources

    def _collect_logs(self, details, namespace, pod_sources):
        combined = {
            "severity": Severity.OK.name,
            "matched_patterns": [],
            "categories": [],
            "matches": [],
            "containers": [],
        }

        for container in details["containers"]:
            name = container["name"]
            # Previous logs are where the crash reason actually lives.
            wants_previous = container["restart_count"] > 0 or container.get("last_termination")

            for previous in ([False, True] if wants_previous else [False]):
                label = "previous" if previous else "current"
                try:
                    fetch = self.log_tool.get_previous_logs if previous else self.log_tool.get_logs
                    text = fetch(details["name"], namespace, container=name, tail_lines=self.log_tail_lines)
                    pod_sources.append(("kubernetes.logs", True, None))
                except DataSourceError as exc:
                    # 400 = container not started yet / no previous instance.
                    if getattr(exc, "status", None) == 400:
                        combined["containers"].append({"container": name, "stream": label, "available": False, "reason": "not available"})
                        continue
                    pod_sources.append(("kubernetes.logs", False, str(exc)))
                    combined["containers"].append({"container": name, "stream": label, "available": False, "reason": str(exc)})
                    continue

                analysis = self.log_tool.search_logs(text)
                combined["containers"].append({
                    "container": name, "stream": label, "available": True,
                    "total_lines": analysis["total_lines"],
                    "pattern_counts": analysis["pattern_counts"],
                })
                combined["severity"] = max_severity(combined["severity"], analysis["severity"]).name
                for p in analysis["matched_patterns"]:
                    if p not in combined["matched_patterns"]:
                        combined["matched_patterns"].append(p)
                for c in analysis["categories"]:
                    if c not in combined["categories"]:
                        combined["categories"].append(c)
                for m in analysis["matches"][:10]:
                    combined["matches"].append({**m, "container": name, "stream": label})

        return combined

    # ---------------------------------------------------------
    # Service & metrics
    # ---------------------------------------------------------

    def _investigate_service(self, workload, namespace, sources):
        result, ms = _timed(lambda: self.service_investigator.investigate(workload, namespace))

        if result["status"] == "UNKNOWN":
            sources.failed("kubernetes.services", result.get("error"))
        else:
            sources.ok("kubernetes.services", ms)

        # By convention the Service shares the workload name; a worker
        # with no Service is legitimate, so absence is not CRITICAL.
        if result["status"] == "CRITICAL" and "does not exist" in " ".join(result["findings"]):
            result = {**result, "status": "NOT_FOUND", "severity": Severity.OK.name}

        return result

    def _investigate_metrics(self, namespace, pod_names, sources):
        metrics = {}

        for key, run in (
            ("cpu", lambda: CPUInvestigator(self.prometheus).investigate(namespace, pod_names)),
            ("cpu_throttling", lambda: CPUInvestigator(self.prometheus).investigate_throttling(namespace, pod_names)),
            ("memory", lambda: MemoryInvestigator(self.prometheus).investigate(namespace, pod_names)),
        ):
            result, ms = _timed(run)
            metrics[key] = result
            if result["available"]:
                sources.ok("prometheus", ms)
            else:
                sources.failed("prometheus", result["error"])

        return metrics

    # ---------------------------------------------------------
    # Scoring
    # ---------------------------------------------------------

    def _score(self, report):
        """
        Impact-adjusted severity.

        Raw signal severity says "something is broken"; impact says
        "users are affected". One CrashLooping replica behind a Service
        that still has healthy endpoints is HIGH, not CRITICAL - paging
        someone at 3am for that is how alert fatigue starts.
        """

        reasons = report["severity_reasons"]
        pods = [p for p in report["pods"] if "health" in p]
        severity = Severity.parse(report["severity"])

        pod_max = max_severity(*(p["severity"] for p in report["pods"])) if report["pods"] else Severity.OK
        service = report.get("service") or {}
        healthy_pods = [p for p in pods if Severity.parse(p["severity"]) <= Severity.LOW]

        if pod_max >= Severity.CRITICAL and service.get("status") == "HEALTHY" and healthy_pods:
            severity = max_severity(severity, Severity.HIGH)
            reasons.append(
                f"{len(pods) - len(healthy_pods)}/{len(pods)} pods unhealthy, "
                "but the Service still has ready endpoints (partial degradation)."
            )
        elif pod_max > Severity.OK:
            severity = max_severity(severity, pod_max)
            reasons.append(f"Worst pod severity: {pod_max.name}.")

        if service.get("severity"):
            svc_sev = Severity.parse(service["severity"])
            if svc_sev > Severity.OK:
                severity = max_severity(severity, svc_sev)
                reasons.append(f"Service {service.get('status')}: {'; '.join(service.get('findings', []))}")

        for key, metric in report["metrics"].items():
            if metric.get("available") and Severity.parse(metric["severity"]) > Severity.OK:
                severity = max_severity(severity, metric["severity"])
                reasons.append(f"Metric {key} at {metric['severity']}.")

        report["severity"] = severity.name

        recs = []
        for pod in pods:
            recs.extend(pod["health"]["recommendations"])
            if pod.get("events"):
                recs.extend(pod["events"]["recommendations"])
        for cause in report["probable_causes"]:
            recs.extend(cause.get("next_steps", []))
        report["recommendations"] = list(dict.fromkeys(recs))

    # ---------------------------------------------------------
    # Finalisation
    # ---------------------------------------------------------

    def _finalize(self, report, sources, t0):
        report["data_sources"] = sources.items()
        missing = sources.unavailable()

        if missing and report["status"] == "COMPLETE":
            report["status"] = "PARTIAL"

        if report["confidence"] != "NONE":
            if "kubernetes.pods" in missing:
                report["confidence"] = "LOW"
            elif missing:
                report["confidence"] = "MEDIUM"
                report["notes"].append(f"Unavailable data sources: {', '.join(missing)}.")

        # Healthy-looking but blind is UNKNOWN, never OK.
        if report["severity"] == Severity.OK.name and missing:
            report["severity"] = Severity.UNKNOWN.name

        report["incident_id"] = fingerprint(report)
        report["duration_ms"] = round((self._clock() - t0) * 1000, 1)

        METRICS.inc("investigations", {"status": report["status"], "severity": report["severity"]})
        logger.info(
            "investigation finished",
            extra={
                "workload": report["workload"],
                "namespace": report["namespace"],
                "incident_id": report["incident_id"],
                "severity": report["severity"],
                "status": report["status"],
                "duration_ms": report["duration_ms"],
            },
        )
        return report


def fingerprint(report):
    """
    Stable id derived from *what* is wrong, not *when* it was observed.

    Probable-cause ids are the primary key. Container waiting states are
    deliberately excluded when causes exist: a crash-looping container
    flips between CrashLoopBackOff and Running, which would otherwise
    churn the id and defeat de-duplication.
    """

    signatures = {f"cause:{c['id']}" for c in report["probable_causes"]}

    if not signatures:
        for pod in report["pods"]:
            for c in (pod.get("health") or {}).get("containers", []):
                term = c.get("last_termination")
                if term:
                    signatures.add(f"term:{c['name']}:{term['reason']}")
            for cat in (pod.get("events") or {}).get("categories", []):
                signatures.add(f"event:{cat}")
            for cat in (pod.get("logs") or {}).get("categories", []):
                signatures.add(f"log:{cat}")

    if not signatures:
        signatures.add(f"severity:{report['severity']}")

    material = "|".join([report["namespace"], report["workload"], *sorted(signatures)])
    return "INC-" + hashlib.sha256(material.encode()).hexdigest()[:12]


# -------------------------------------------------------------
# Correlation rules
# -------------------------------------------------------------

def _collect(report):
    waiting, terminations, event_cats, log_cats = set(), set(), set(), set()

    for pod in report["pods"]:
        for c in (pod.get("health") or {}).get("containers", []):
            if c["status"] != "Healthy":
                waiting.add(c["status"])
            if c.get("last_termination"):
                terminations.add(c["last_termination"]["reason"])
        event_cats.update((pod.get("events") or {}).get("categories", []))
        log_cats.update((pod.get("logs") or {}).get("categories", []))

    return waiting, terminations, event_cats, log_cats


def correlate(report):
    """
    Deterministic, explainable hypotheses. Each cause lists the evidence
    that triggered it, so a human (or the LLM layer) can verify it.
    """

    waiting, terminations, event_cats, log_cats = _collect(report)
    metrics = report.get("metrics") or {}
    service = report.get("service") or {}
    causes = []

    def add(cause_id, summary, confidence, evidence, next_steps, remediation=None):
        causes.append({
            "id": cause_id,
            "summary": summary,
            "confidence": confidence,
            "evidence": evidence,
            "next_steps": next_steps,
            "remediation_hint": remediation,
        })

    def metric_sev(key):
        m = metrics.get(key) or {}
        return Severity.parse(m.get("severity")) if m.get("available") else None

    if "OOMKilled" in terminations or "oom" in event_cats:
        mem = metric_sev("memory")
        add(
            "memory_limit_exceeded",
            "Container memory exceeds its limit (OOMKilled).",
            "HIGH",
            ["Last termination reason OOMKilled"]
            + ([f"Current memory utilization severity {mem.name}"] if mem else []),
            ["Compare working set to limit over 24h; raise limit or fix leak."],
            remediation="increase_memory_limit",
        )

    crashloop = "CrashLoopBackOff" in waiting
    dependency = log_cats & {"DATABASE", "DEPENDENCY"}

    if crashloop and dependency:
        add(
            "dependency_unavailable",
            "Application crashes because a dependency (e.g. database) is unreachable.",
            "MEDIUM",
            ["CrashLoopBackOff", f"Log categories: {sorted(dependency)}"],
            ["Check the dependency's pods/Service/endpoints.",
             "Make startup resilient: retry the dependency with backoff instead of exiting."],
            remediation="investigate_dependency",
        )
    elif "probe" in event_cats and dependency:
        add(
            "readiness_dependency",
            "Readiness probe fails because a dependency is unreachable.",
            "MEDIUM",
            ["Probe failure events", f"Log categories: {sorted(dependency)}"],
            ["Check the dependency's health; pods are correctly being removed from rotation."],
            remediation="investigate_dependency",
        )
    elif crashloop and "OOMKilled" not in terminations:
        add(
            "application_crash",
            "Application exits during startup.",
            "LOW" if not log_cats else "MEDIUM",
            ["CrashLoopBackOff"] + ([f"Log categories: {sorted(log_cats)}"] if log_cats else ["No known error pattern in logs"]),
            ["Read previous container logs in full.",
             "Diff config/image against the last known-good revision."],
            remediation="rollback",
        )

    if waiting & {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"} or "image" in event_cats:
        add(
            "image_pull_failure",
            "Container image cannot be pulled.",
            "HIGH",
            [f"Waiting reasons: {sorted(waiting)}"],
            ["Verify image name/tag exists in the registry and imagePullSecrets are valid."],
            remediation="rollback",
        )

    if waiting & {"CreateContainerConfigError", "RunContainerError"} or "command" in event_cats:
        add(
            "bad_configuration",
            "Pod spec references missing config or an invalid command.",
            "HIGH",
            [f"Waiting reasons: {sorted(waiting)}", f"Event categories: {sorted(event_cats)}"],
            ["Check referenced ConfigMaps/Secrets and command/args."],
            remediation="rollback",
        )

    if "scheduling" in event_cats:
        add(
            "unschedulable",
            "Pods cannot be scheduled (capacity or placement constraints).",
            "HIGH",
            ["FailedScheduling events"],
            ["Check node allocatable vs. requests, taints, affinity."],
        )

    if any("Selector mismatch" in f for f in service.get("findings", [])):
        add(
            "service_selector_mismatch",
            "Service selector does not match any pod labels.",
            "HIGH",
            service["findings"],
            ["Align Service spec.selector with the pod template labels."],
        )

    throttle = metric_sev("cpu_throttling")
    if throttle and throttle >= Severity.HIGH and "TIMEOUT" in log_cats:
        add(
            "cpu_starvation",
            "CPU throttling is likely causing timeouts.",
            "MEDIUM",
            [f"CPU throttling severity {throttle.name}", "Timeouts in logs"],
            ["Raise CPU limit or remove it (keep the request); check for hot loops."],
            remediation="increase_cpu_limit",
        )

    return causes
