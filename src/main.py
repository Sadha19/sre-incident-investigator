import argparse

from src.sre_agent.tools.kubernetes_tool import KubernetesTool
from src.sre_agent.tools.log_tool import LogTool
from src.sre_agent.investigators.pod_investigator import PodHealthInvestigator
from src.sre_agent.investigators.event_investigator import EventInvestigator


NAMESPACE = "sre-lab"


def find_workload_pods(k8s, workload_name, namespace):
    """
    Find pods belonging to a workload using the pod name prefix.

    Example:
        payment-api
        ->
        payment-api-7bb5cc8d6-7zxgg
        payment-api-7bb5cc8d6-df24v
    """

    pods = k8s.get_pods(namespace=namespace)

    matching_pods = []

    for pod in pods:
        pod_name = pod["name"]

        if pod_name.startswith(workload_name + "-"):
            matching_pods.append(pod_name)

    return matching_pods


def determine_severity(pod_diagnosis, event_diagnosis, log_analysis):
    """
    Determine overall incident severity.
    """

    severities = [
        pod_diagnosis["severity"],
        event_diagnosis["severity"],
    ]

    if log_analysis["matched_patterns"]:
        severities.append("CRITICAL")

    if "CRITICAL" in severities:
        return "CRITICAL"

    if "WARNING" in severities:
        return "HIGH"

    return "INFO"


def investigate(workload_name, namespace):
    print()
    print("=" * 70)
    print("                    INCIDENT INVESTIGATION")
    print("=" * 70)
    print()

    k8s = KubernetesTool()
    log_tool = LogTool(k8s)

    pod_investigator = PodHealthInvestigator(k8s)
    event_investigator = EventInvestigator(k8s)

    pods = find_workload_pods(
        k8s=k8s,
        workload_name=workload_name,
        namespace=namespace,
    )

    if not pods:
        print(f"No pods found for workload: {workload_name}")
        print(f"Namespace: {namespace}")
        return

    print(f"Workload  : {workload_name}")
    print(f"Namespace : {namespace}")
    print(f"Pods found: {len(pods)}")

    for pod_name in pods:

        print()
        print("=" * 70)
        print(f"Pod: {pod_name}")
        print("=" * 70)

        # ---------------------------------------------------------
        # 1. POD HEALTH
        # ---------------------------------------------------------

        pod_diagnosis = pod_investigator.investigate_pod(
            pod_name=pod_name,
            namespace=namespace,
        )

        # ---------------------------------------------------------
        # 2. EVENTS
        # ---------------------------------------------------------

        event_diagnosis = event_investigator.investigate_pod(
            pod_name=pod_name,
            namespace=namespace,
        )

        # ---------------------------------------------------------
        # 3. LOGS
        # ---------------------------------------------------------

        pod_details = k8s.get_pod_details(
            pod_name=pod_name,
            namespace=namespace,
        )

        all_log_analysis = {
            "total_lines": 0,
            "matched_patterns": [],
            "matches": [],
        }

        for container in pod_details["containers"]:

            container_name = container["name"]

            logs = log_tool.get_logs(
                pod_name=pod_name,
                namespace=namespace,
                container=container_name,
                tail_lines=100,
            )

            analysis = log_tool.search_logs(logs)

            all_log_analysis["total_lines"] += analysis["total_lines"]

            for pattern in analysis["matched_patterns"]:
                if pattern not in all_log_analysis["matched_patterns"]:
                    all_log_analysis["matched_patterns"].append(pattern)

            all_log_analysis["matches"].extend(
                analysis["matches"]
            )

        # ---------------------------------------------------------
        # 4. OVERALL SEVERITY
        # ---------------------------------------------------------

        severity = determine_severity(
            pod_diagnosis=pod_diagnosis,
            event_diagnosis=event_diagnosis,
            log_analysis=all_log_analysis,
        )

        # ---------------------------------------------------------
        # 5. SUMMARY
        # ---------------------------------------------------------

        print()
        print("INCIDENT SUMMARY")
        print("-" * 70)

        print(f"Pod:")
        print(f"  {pod_name}")

        print()
        print(f"Status:")
        print(f"  {pod_diagnosis['status']}")

        print()
        print("Restarts:")

        total_restarts = 0

        for container in pod_diagnosis["containers"]:
            total_restarts += container["restart_count"]

        print(f"  {total_restarts}")

        print()
        print("Last termination:")

        termination_found = False

        for container in pod_diagnosis["containers"]:

            termination = container.get("last_termination")

            if termination:
                print(
                    f"  {termination['reason']}"
                )
                termination_found = True

        if not termination_found:
            print("  None")

        print()
        print("Recent errors:")

        if all_log_analysis["matched_patterns"]:

            for pattern in all_log_analysis["matched_patterns"]:
                print(f"  - {pattern}")

        else:
            print("  None detected")

        print()
        print("Severity:")
        print(f"  {severity}")

        # ---------------------------------------------------------
        # 6. EVIDENCE
        # ---------------------------------------------------------

        print()
        print("EVIDENCE")
        print("-" * 70)

        if pod_diagnosis["issues"]:
            print()
            print("Pod Health:")

            for issue in pod_diagnosis["issues"]:
                print(f"  - {issue}")

        if event_diagnosis["issues"]:
            print()
            print("Kubernetes Events:")

            for issue in event_diagnosis["issues"]:
                print(f"  - {issue}")

        if all_log_analysis["matches"]:
            print()
            print("Log Evidence:")

            # Show only first 10 matches.
            for match in all_log_analysis["matches"][:10]:
                print(
                    f"  [{match['pattern']}] "
                    f"{match['line']}"
                )

        # ---------------------------------------------------------
        # 7. RECOMMENDATIONS
        # ---------------------------------------------------------

        recommendations = []

        recommendations.extend(
            pod_diagnosis["recommendations"]
        )

        recommendations.extend(
            event_diagnosis["recommendations"]
        )

        if recommendations:

            recommendations = list(
                dict.fromkeys(recommendations)
            )

            print()
            print("RECOMMENDATIONS")
            print("-" * 70)

            for recommendation in recommendations:
                print(f"  - {recommendation}")

        print()
        print("=" * 70)


def main():

    parser = argparse.ArgumentParser(
        description="SRE Incident Investigation Agent"
    )

    subparsers = parser.add_subparsers(
        dest="command"
    )

    investigate_parser = subparsers.add_parser(
        "investigate",
        help="Investigate a Kubernetes workload",
    )

    investigate_parser.add_argument(
        "workload",
        help="Workload name, e.g. payment-api",
    )

    investigate_parser.add_argument(
        "--namespace",
        default=NAMESPACE,
        help="Kubernetes namespace",
    )

    args = parser.parse_args()

    if args.command == "investigate":

        investigate(
            workload_name=args.workload,
            namespace=args.namespace,
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
