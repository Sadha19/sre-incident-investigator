from src.sre_agent.investigators.event_investigator import EventInvestigator


def main():

    namespace = "sre-lab"

    investigator = EventInvestigator()

    print()
    print("=" * 60)
    print("             SRE EVENT INVESTIGATION")
    print("=" * 60)

    pods = investigator.k8s.get_pods(namespace)

    for pod in pods:

        pod_name = pod["name"]

        print()
        print(f"Investigating: {pod_name}")

        diagnosis = investigator.investigate_pod(
            pod_name=pod_name,
            namespace=namespace
        )

        investigator.print_report(diagnosis)


if __name__ == "__main__":
    main()
