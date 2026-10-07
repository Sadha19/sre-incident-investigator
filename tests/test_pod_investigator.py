from src.sre_agent.investigators.pod_investigator import PodHealthInvestigator


def main():
    namespace = "sre-lab"

    investigator = PodHealthInvestigator()

    print()
    print("=" * 60)
    print("             POD HEALTH INVESTIGATION")
    print("=" * 60)

    print()
    print(f"Namespace: {namespace}")

    pods = investigator.k8s.get_pods(namespace)

    for pod in pods:
        pod_name = pod["name"]

        print()
        print("-" * 60)
        print(f"Investigating: {pod_name}")
        print("-" * 60)

        diagnosis = investigator.investigate_pod(
            pod_name=pod_name,
            namespace=namespace
        )

        investigator.print_report(diagnosis)


if __name__ == "__main__":
    main()
