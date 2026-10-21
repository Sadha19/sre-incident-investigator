"""
Guardrailed remediation.

Automated remediation is where an SRE tool can cause the outage it was
meant to fix, so this module is deliberately conservative:

    1. Plan, don't act. Every probable cause maps to a *proposal* with a
       risk level and the exact command. Most proposals are human-only.
    2. Only one action is automatable: `rollout_restart` of a Deployment,
       and only for causes where a restart can plausibly help.
    3. Writes are disabled unless SRE_AGENT_ALLOW_WRITES=true AND the
       caller passes execute=True. Otherwise a *server-side dry run* is
       sent, which validates RBAC/admission without changing anything.
    4. Idempotency: an action is keyed by (incident_id, action, target).
       Re-running the same plan for the same incident is a no-op.
    5. Rate limiting: per-target cooldown and a global hourly budget.
    6. Blast radius: refuse when the workload is already below half of
       its desired replicas - restarting the remaining healthy pods
       would turn a partial outage into a full one.
    7. Audit: the patch annotates the pod template with the incident id.
"""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

from src.sre_agent.observability import METRICS

logger = logging.getLogger(__name__)


ACTION_CATALOG = {
    "rollout_restart": {
        "risk": "LOW",
        "automatable": True,
        "description": "Rolling restart of the Deployment (respects maxUnavailable).",
        "command": "kubectl -n {namespace} rollout restart deployment/{workload}",
    },
    "rollback": {
        "risk": "MEDIUM",
        "automatable": False,
        "description": "Roll back to the previous ReplicaSet revision.",
        "command": "kubectl -n {namespace} rollout undo deployment/{workload}",
    },
    "increase_memory_limit": {
        "risk": "MEDIUM",
        "automatable": False,
        "description": "Raise the memory limit via the GitOps source of truth, not kubectl.",
        "command": "# edit resources.limits.memory for {workload} in the manifest repo",
    },
    "increase_cpu_limit": {
        "risk": "MEDIUM",
        "automatable": False,
        "description": "Raise or remove the CPU limit via the manifest repo.",
        "command": "# edit resources.limits.cpu for {workload} in the manifest repo",
    },
    "investigate_dependency": {
        "risk": "NONE",
        "automatable": False,
        "description": "Fix the failing dependency first; then restart to reset connection pools.",
        "command": "kubectl -n {namespace} get pods,svc,endpointslices",
    },
}

# Causes for which a restart is never the fix: it only burns the
# CrashLoopBackOff timer and hides evidence.
RESTART_FORBIDDEN_CAUSES = {
    "image_pull_failure",
    "bad_configuration",
    "memory_limit_exceeded",
    "unschedulable",
    "service_selector_mismatch",
}


class RemediationError(Exception):
    pass


class RemediationState:
    """
    Tiny persisted ledger for idempotency and cooldowns.
    Writes are atomic (tempfile + os.replace).
    """

    def __init__(self, path, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {"executed": {}, "targets": {}}
        except (OSError, ValueError) as exc:
            # A corrupt ledger must fail closed, not reset to "nothing done".
            raise RemediationError(f"remediation state unreadable: {exc}") from exc

    def _save(self, data):
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sre_state")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)

    def already_executed(self, key):
        with self._lock:
            return key in self._load()["executed"]

    def last_action_at(self, target):
        with self._lock:
            return self._load()["targets"].get(target)

    def actions_since(self, seconds):
        cutoff = self._clock() - seconds
        with self._lock:
            return sum(1 for ts in self._load()["executed"].values() if ts >= cutoff)

    def record(self, key, target):
        with self._lock:
            data = self._load()
            now = self._clock()
            data["executed"][key] = now
            data["targets"][target] = now
            self._save(data)


def plan(report):
    """Turn probable causes into ordered, de-duplicated proposals."""

    proposals = []
    seen = set()
    cause_ids = {c["id"] for c in report.get("probable_causes", [])}

    for cause in report.get("probable_causes", []):
        action = cause.get("remediation_hint")
        if not action or action in seen:
            continue
        seen.add(action)

        spec = ACTION_CATALOG[action]
        proposals.append({
            "action": action,
            "cause": cause["id"],
            "risk": spec["risk"],
            "automatable": spec["automatable"],
            "description": spec["description"],
            "command": spec["command"].format(
                namespace=report["namespace"], workload=report["workload"]
            ),
        })

    # Restart is offered only after a dependency issue, as the
    # "reset connection pools once the dependency is back" step.
    if "dependency_unavailable" in cause_ids or "readiness_dependency" in cause_ids:
        spec = ACTION_CATALOG["rollout_restart"]
        proposals.append({
            "action": "rollout_restart",
            "cause": "dependency_unavailable",
            "risk": spec["risk"],
            "automatable": spec["automatable"],
            "description": spec["description"] + " Only after the dependency is healthy.",
            "command": spec["command"].format(
                namespace=report["namespace"], workload=report["workload"]
            ),
        })

    if cause_ids & RESTART_FORBIDDEN_CAUSES:
        proposals = [p for p in proposals if p["action"] != "rollout_restart"]

    return proposals


class RemediationExecutor:

    MAX_ACTIONS_PER_HOUR = 3

    def __init__(self, apps_api, state, allow_writes=False, cooldown_s=1800, clock=time.time):
        self.apps_api = apps_api
        self.state = state
        self.allow_writes = allow_writes
        self.cooldown_s = cooldown_s
        self._clock = clock

    def rollout_restart(self, report, execute=False):
        """
        Returns a result dict; never raises for policy refusals so the
        caller can report *why* nothing happened.
        """

        namespace, workload = report["namespace"], report["workload"]
        incident_id = report["incident_id"]
        target = f"{namespace}/deployment/{workload}"
        key = f"{incident_id}:rollout_restart:{target}"
        dry_run = not (execute and self.allow_writes)

        def refuse(reason):
            METRICS.inc("remediation", {"action": "rollout_restart", "outcome": "refused"})
            logger.warning("remediation refused", extra={"target": target, "reason": reason})
            return {"action": "rollout_restart", "target": target, "executed": False,
                    "dry_run": dry_run, "idempotency_key": key, "reason": reason}

        if execute and not self.allow_writes:
            return refuse("writes disabled (set SRE_AGENT_ALLOW_WRITES=true)")

        if not any(p["action"] == "rollout_restart" for p in plan(report)):
            return refuse("rollout_restart is not an approved action for this incident's causes")

        if report.get("status") == "FAILED" or report.get("confidence") in ("NONE", "LOW"):
            return refuse("investigation confidence too low to act")

        if self.state.already_executed(key):
            return {**refuse("already executed for this incident (idempotent no-op)"), "reason_code": "duplicate"}

        last = self.state.last_action_at(target)
        if last and self._clock() - last < self.cooldown_s:
            remaining = int(self.cooldown_s - (self._clock() - last))
            return refuse(f"target in cooldown for another {remaining}s")

        if self.state.actions_since(3600) >= self.MAX_ACTIONS_PER_HOUR:
            return refuse("global remediation budget exhausted for this hour")

        deployment = self.apps_api.read_namespaced_deployment(workload, namespace, _request_timeout=10)
        desired = deployment.spec.replicas or 0
        ready = deployment.status.ready_replicas or 0

        if desired and ready < desired / 2:
            return refuse(
                f"blast-radius guard: only {ready}/{desired} replicas ready; "
                "restarting could remove the remaining capacity"
            )

        now = datetime.now(timezone.utc).isoformat()
        patch = {
            "spec": {"template": {"metadata": {"annotations": {
                "kubectl.kubernetes.io/restartedAt": now,
                "sre-agent/incident-id": incident_id,
            }}}}
        }

        kwargs = {"_request_timeout": 10}
        if dry_run:
            kwargs["dry_run"] = "All"

        self.apps_api.patch_namespaced_deployment(workload, namespace, patch, **kwargs)

        if not dry_run:
            self.state.record(key, target)

        METRICS.inc("remediation", {
            "action": "rollout_restart",
            "outcome": "dry_run" if dry_run else "executed",
        })
        logger.info("remediation applied", extra={"target": target, "dry_run": dry_run, "incident_id": incident_id})

        return {"action": "rollout_restart", "target": target, "executed": not dry_run,
                "dry_run": dry_run, "idempotency_key": key, "reason": None}
