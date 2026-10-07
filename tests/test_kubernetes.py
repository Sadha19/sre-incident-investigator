from src.sre_agent.tools.kubernetes_tool import KubernetesTool


def main():

    k8s = KubernetesTool()

    namespace = "sre-lab"

    print()
    print("========================================")
    print("           KUBERNETES STATUS")
    print("========================================")

    print()
    print(f"Namespace: {namespace}")

    print()
    print("Pods")
    print("-" * 40)

    pods = k8s.get_pods(namespace)

    for pod in pods:
        print(
            f"{pod['name']:<35}"
            f"{pod['status']}"
        )

    print()
    print("Deployments")
    print("-" * 40)

    deployments = k8s.get_deployments(namespace)

    for deployment in deployments:
        print(
            f"{deployment['name']:<25}"
            f"{deployment['ready']}/"
            f"{deployment['desired']}"
        )

    print()
    print("Services")
    print("-" * 40)

    services = k8s.get_services(namespace)

    for service in services:
        print(
            f"{service['name']:<25}"
            f"{service['type']}"
        )

    print()
    print("Nodes")
    print("-" * 40)

    nodes = k8s.get_nodes()

    for node in nodes:
        print(
            f"{node['name']:<35}"
            f"Ready={node['ready']}"
        )


if __name__ == "__main__":
    main()
