import sys
import json

from parser import parse_log_file
from analyzer import LogAnalyzer
import json
import os


def save_incident(incident, output_dir="reports"):

    os.makedirs(output_dir, exist_ok=True)

    file_path = os.path.join(
        output_dir,
        f"{incident.incident_id}.json"
    )

    with open(file_path, "w", encoding="utf-8") as file:

        json.dump(
            incident.to_dict(),
            file,
            indent=4
        )

    return file_path

def main():

    if len(sys.argv) != 2:

        print(
            "Usage: "
            "python apps/log-analyzer/app.py <log-file>"
        )

        sys.exit(1)

    log_file = sys.argv[1]

    try:

        logs = parse_log_file(log_file)

        if not logs:

            print("No valid log entries found.")

            sys.exit(1)

        analyzer = LogAnalyzer(logs)
        incident = analyzer.create_incident()

        file_path = save_incident(incident)

        print()
        print(f"Incident saved to: {file_path}")

        print()
        print("=" * 60)
        print("              STRUCTURED INCIDENT")
        print("=" * 60)

        print(
            json.dumps(
                incident.to_dict(),
                indent=4
            )
        )

        analyzer.generate_report()

    except FileNotFoundError:

        print(
            f"ERROR: Log file not found: {log_file}"
        )

        sys.exit(1)

    except PermissionError:

        print(
            f"ERROR: Permission denied: {log_file}"
        )

        sys.exit(1)

    except Exception as error:

        print(
            f"Unexpected error: {error}"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
