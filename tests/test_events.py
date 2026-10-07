from src.sre_agent.tools.kubernetes_tool import KubernetesTool


def main():

    namespace = "sre-lab"

    k8s = KubernetesTool()

    pods = k8s.get_pods(namespace)

    print()
    print("=" * 60)
    print("             KUBERNETES POD EVENTS")
    print("=" * 60)

    for pod in pods:

        pod_name = pod["name"]

        print()
        print("-" * 60)
        print(f"Pod: {pod_name}")
        print("-" * 60)

        events = k8s.get_pod_events(
            pod_name=pod_name,
            namespace=namespace
        )

        if not events:
            print("No events found.")
            continue

        for event in events:

            print(f"Type      : {event['type']}")
            print(f"Reason    : {event['reason']}")
            print(f"Message   : {event['message']}")
            print(f"Count     : {event['count']}")
            print(f"Source    : {event['source']}")
            print(f"Last Time : {event['last_timestamp']}")
            print()


if __name__ == "__main__":
    main()
