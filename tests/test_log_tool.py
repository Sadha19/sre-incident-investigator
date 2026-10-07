from src.sre_agent.tools.kubernetes_tool import KubernetesTool
from src.sre_agent.tools.log_tool import LogTool
def main():
    namespace = "sre-lab"

    k8s = KubernetesTool()
    log_tool = LogTool(k8s)

    workload_name = "payment-api"

    print()
    print("=" * 60)
    print("                 LOG INVESTIGATION")
    print("=" * 60)

    print()
    print(f"Workload  : {workload_name}")
    print(f"Namespace : {namespace}")

    results = log_tool.get_workload_logs(
        workload_name=workload_name,
        namespace=namespace,
        tail_lines=100,
    )

    for result in results:

        print()
        print("-" * 60)
        print(f"Pod       : {result['pod']}")
        print(f"Container : {result['container']}")
        print("-" * 60)

        logs = result["logs"]

        if not logs:
            print("No logs found.")
            continue

        print()
        print("CURRENT LOGS")
        print("-" * 40)
        print(logs)

        analysis = log_tool.search_logs(logs)

        print()
        print("LOG ANALYSIS")
        print("-" * 40)

        print(f"Total lines: {analysis['total_lines']}")

        if analysis["matched_patterns"]:

            print("Detected patterns:")

            for pattern in analysis["matched_patterns"]:
                print(f"- {pattern}")

            print()
            print("Matching lines:")

            for match in analysis["matches"]:
                print(
                    f"[{match['pattern']}] "
                    f"{match['line']}"
                )

        else:
            print("No known error patterns detected.")


if __name__ == "__main__":
    main()
