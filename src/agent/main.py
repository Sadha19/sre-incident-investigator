
from src.agent.agent import SREAgent


def main():
    agent = SREAgent()

    incident = input("Describe the incident: ").strip()

    if not incident:
        print("Please provide an incident description.")
        return

    report = agent.investigate(incident)
    print("\n========== SRE INCIDENT REPORT ==========\n")
    print(report)


if __name__ == "__main__":
    main()
