"""
SRE Incident Investigation Agent - deterministic CLI.

Exit codes (so the tool can gate CI/CD or feed automation):
    0  investigated, severity OK/LOW
    1  investigated, problems found (MEDIUM or above)
    2  investigation incomplete (UNKNOWN severity / failed data sources)
    3  usage or configuration error
"""

import argparse
import json
import logging
import sys

from src.config import load_settings
from src.sre_agent.investigators.service_investigator import ServiceInvestigator
from src.sre_agent.models import Severity
from src.sre_agent.observability import METRICS, configure_logging, new_correlation_id
from src.sre_agent.orchestrator import IncidentInvestigator
from src.sre_agent.remediation import RemediationExecutor, RemediationState, plan
from src.sre_agent.tools.kubernetes_tool import KubernetesTool
from src.sre_agent.tools.prometheus_tool import PrometheusTool, validate_k8s_name

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_PROBLEMS, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3


def exit_code_for(report):
    severity = Severity.parse(report["severity"])
    if report["status"] == "FAILED" or severity == Severity.UNKNOWN:
        return EXIT_INCOMPLETE
    if severity >= Severity.MEDIUM:
        return EXIT_PROBLEMS
    return EXIT_OK


def build_investigator(settings, use_prometheus=True):
    k8s = KubernetesTool(timeout_s=settings.k8s_timeout_s)

    prometheus = (
        PrometheusTool(url=settings.prometheus_url, timeout_s=settings.prometheus_timeout_s)
        if use_prometheus
        else None
    )

    return IncidentInvestigator(
        k8s=k8s,
        prometheus=prometheus,
        service_investigator=ServiceInvestigator(
            core_api=k8s.core_api, timeout_s=settings.k8s_timeout_s,
            discovery_api=None,
        ),
        log_tail_lines=settings.log_tail_lines,
        deadline_s=settings.investigation_deadline_s,
    ), k8s


# -------------------------------------------------------------
# Text rendering
# -------------------------------------------------------------

def render_text(report, out=sys.stdout):
    w = out.write
    line = "=" * 70

    w(f"\n{line}\n{'INCIDENT INVESTIGATION':^70}\n{line}\n\n")
    w(f"Incident   : {report['incident_id']}\n")
    w(f"Workload   : {report['workload']}\n")
    w(f"Namespace  : {report['namespace']}\n")
    w(f"Severity   : {report['severity']}\n")
    w(f"Status     : {report['status']} (confidence {report['confidence']})\n")
    w(f"Pods       : {len(report['pods'])}\n")
    w(f"Duration   : {report['duration_ms']:.0f} ms\n")

    for reason in report["severity_reasons"]:
        w(f"  - {reason}\n")

    if report["probable_causes"]:
        w(f"\nPROBABLE CAUSES\n{'-' * 70}\n")
        for cause in report["probable_causes"]:
            w(f"  [{cause['confidence']}] {cause['summary']}\n")
            for ev in cause["evidence"]:
                w(f"      evidence: {ev}\n")

    for pod in report["pods"]:
        w(f"\n{line}\nPod: {pod['pod']}  severity={pod['severity']}\n{line}\n")

        if pod.get("error"):
            w(f"  investigation error: {pod['error']}\n")
            continue
        if pod.get("note"):
            w(f"  {pod['note']}\n")
            continue
        if pod.get("skipped"):
            w("  skipped: deadline reached\n")
            continue

        health = pod["health"]
        restarts = sum(c["restart_count"] for c in health["containers"])
        terms = [c["last_termination"]["reason"] for c in health["containers"] if c.get("last_termination")]
        w(f"  Status           : {health['status']}\n")
        w(f"  Restarts         : {restarts}\n")
        w(f"  Last termination : {', '.join(terms) or 'None'}\n")

        if health["issues"]:
            w("  Pod health:\n")
            for issue in health["issues"]:
                w(f"    - {issue}\n")

        events = pod.get("events")
        if events is None:
            w("  Events: UNAVAILABLE\n")
        elif events["issues"]:
            w("  Kubernetes events:\n")
            for issue in events["issues"]:
                w(f"    - {issue}\n")

        logs = pod["logs"]
        unavailable = [c for c in logs["containers"] if not c["available"] and c.get("reason") != "not available"]
        if unavailable:
            w(f"  Logs: UNAVAILABLE for {len(unavailable)} stream(s)\n")
        if logs["matches"]:
            w(f"  Log evidence (severity {logs['severity']}):\n")
            for m in logs["matches"][:10]:
                w(f"    [{m['stream']}:{m['pattern']}] {m['line']}\n")

    service = report.get("service")
    if service:
        w(f"\nSERVICE\n{'-' * 70}\n  {service['service']}: {service['status']}\n")
        for f in service.get("findings", []):
            w(f"    - {f}\n")

    for key, metric in (report.get("metrics") or {}).items():
        if not metric["available"]:
            w(f"\nMetric {key}: UNAVAILABLE ({metric['error']})\n")
            continue
        flagged = [f for f in metric["findings"] if f["severity"] != Severity.OK.name]
        for f in flagged:
            w(f"\nMetric {key}: {f['pod']} {f['current_usage']} ({f['severity']})\n")

    if report["recommendations"]:
        w(f"\nRECOMMENDATIONS\n{'-' * 70}\n")
        for rec in report["recommendations"]:
            w(f"  - {rec}\n")

    proposals = report.get("remediation_plan") or []
    if proposals:
        w(f"\nREMEDIATION PLAN (not executed)\n{'-' * 70}\n")
        for p in proposals:
            mode = "automatable" if p["automatable"] else "human approval"
            w(f"  [{p['risk']} risk, {mode}] {p['description']}\n      $ {p['command']}\n")

    degraded = [s for s in report["data_sources"] if not s["available"]]
    if degraded or report["notes"]:
        w(f"\nNOTES\n{'-' * 70}\n")
        for s in degraded:
            w(f"  - Data source {s['name']} unavailable: {s['error']}\n")
        for n in report["notes"]:
            w(f"  - {n}\n")

    w(f"\n{line}\n")


# -------------------------------------------------------------
# Commands
# -------------------------------------------------------------

def cmd_investigate(args, settings):
    investigator, _ = build_investigator(settings, use_prometheus=not args.no_metrics)
    report = investigator.investigate(args.workload, args.namespace)
    report["remediation_plan"] = plan(report)

    if args.output == "json":
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        render_text(report)

    if args.metrics_file:
        with open(args.metrics_file, "w", encoding="utf-8") as fh:
            fh.write(METRICS.to_prometheus_text())

    return exit_code_for(report)


def cmd_remediate(args, settings):
    investigator, k8s = build_investigator(settings, use_prometheus=not args.no_metrics)
    report = investigator.investigate(args.workload, args.namespace)

    executor = RemediationExecutor(
        apps_api=k8s.apps_api,
        state=RemediationState(settings.remediation_state_file),
        allow_writes=settings.allow_writes,
        cooldown_s=settings.remediation_cooldown_s,
    )

    result = executor.rollout_restart(report, execute=args.execute)
    json.dump({"incident_id": report["incident_id"], "severity": report["severity"],
               "plan": plan(report), "result": result}, sys.stdout, indent=2)
    sys.stdout.write("\n")

    return EXIT_OK if result["reason"] is None else EXIT_PROBLEMS


def build_parser(settings):
    parser = argparse.ArgumentParser(description="SRE Incident Investigation Agent")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command")

    for name, help_text in (
        ("investigate", "Investigate a Kubernetes workload"),
        ("remediate", "Investigate, then apply guardrailed remediation (dry-run by default)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("workload", help="Workload name, e.g. payment-api")
        p.add_argument("--namespace", default=settings.default_namespace)
        p.add_argument("--no-metrics", action="store_true", help="Skip Prometheus")

        if name == "investigate":
            p.add_argument("--output", choices=["text", "json"], default="text")
            p.add_argument("--metrics-file", help="Write agent metrics in Prometheus textfile format")
        else:
            p.add_argument("--execute", action="store_true",
                           help="Actually apply (also requires SRE_AGENT_ALLOW_WRITES=true)")

    return parser


def main(argv=None):
    settings = load_settings()
    parser = build_parser(settings)
    args = parser.parse_args(argv)

    configure_logging(args.log_level.upper())
    new_correlation_id()

    if args.command not in ("investigate", "remediate"):
        parser.print_help()
        return EXIT_USAGE

    try:
        validate_k8s_name(args.workload, "workload")
        validate_k8s_name(args.namespace, "namespace")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.namespace not in settings.allowed_namespaces:
        print(
            f"error: namespace '{args.namespace}' is not in SRE_ALLOWED_NAMESPACES "
            f"({', '.join(settings.allowed_namespaces)})",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        if args.command == "investigate":
            return cmd_investigate(args, settings)
        return cmd_remediate(args, settings)
    except Exception as exc:
        logger.exception("fatal error")
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
