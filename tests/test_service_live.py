from src.sre_agent.investigators.service_investigator import (
    ServiceInvestigator,
)

investigator = ServiceInvestigator()

for service_name in [
    "frontend",
    "inventory-api",
    "payment-api",
]:
    result = investigator.investigate(
        service_name=service_name,
        namespace="sre-lab",
    )

    print(f"\nService: {result['service']}")
    print(f"Status: {result['status']}")
    print(f"Findings: {result['findings']}")
