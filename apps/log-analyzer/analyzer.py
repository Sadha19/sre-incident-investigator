from collections import Counter
from incident import Incident

class LogAnalyzer:

    def __init__(self, logs):
        self.logs = logs

    def count_levels(self):

        return Counter(
            log["level"]
            for log in self.logs
        )

    def get_errors(self):

        return [
            log
            for log in self.logs
            if log["level"] in ["ERROR", "CRITICAL"]
        ]

    
    def find_database_issues(self):

        keywords = [
            "database",
            "postgresql",
            "connection refused",
            "connection pool",
            "db connection"
        ]

        issues = []

        for log in self.logs:

            message = log["message"].lower()

            if any(
                keyword in message
                for keyword in keywords
            ):
                issues.append(log)

        return issues

    def determine_severity(self):

        levels = [
            log["level"]
            for log in self.logs
        ]

        if "CRITICAL" in levels:
            return "CRITICAL"

        if "ERROR" in levels:
            return "HIGH"

        if "WARNING" in levels:
            return "MEDIUM"

        return "LOW"

    def find_root_cause(self):

        database_issues = self.find_database_issues()

        # Highest priority: connection refused
        for issue in database_issues:

            message = issue["message"].lower()

            if "connection refused" in message:
                return (
                    "PostgreSQL server may be unavailable "
                    "or refusing connections."
                )

        # Second priority: database connection failure
        for issue in database_issues:

            message = issue["message"].lower()

            if (
                "failed to connect" in message
                or "could not be established" in message
            ):
                return (
                    "Application is unable to establish "
                    "a connection with PostgreSQL."
                )

        # Third priority: connection pool
        for issue in database_issues:

            message = issue["message"].lower()

            if "connection pool" in message:
                return (
                    "Database connection pool may be "
                    "under high utilization."
                )

        return "Unable to determine root cause."

    def create_incident(self):

        database_issues = self.find_database_issues()

        severity = self.determine_severity()

        evidence = []

        for log in database_issues:

            if log["level"] in ["ERROR", "CRITICAL"]:

                evidence.append(log["message"])

        root_cause = self.find_root_cause()

        recommended_actions = [
            "Check PostgreSQL server status",
            "Check PostgreSQL service or pod",
            "Verify database network connectivity",
            "Check application database configuration",
            "Check database connection pool utilization"
        ]

        incident = Incident(
            incident_id="INC-001",
            severity=severity,
            category="DATABASE",
            component="PostgreSQL",
            evidence=evidence,
            root_cause=root_cause,
            recommended_actions=recommended_actions
        )

        return incident
    

    def group_errors(self):

        groups = {
            "database_connection": [],
            "application_health": [],
            "other": []
        }

        for log in self.logs:

            message = log["message"].lower()

            # Database-related errors
            if any(keyword in message for keyword in [
                "postgresql",
                "database",
                "connection refused",
                "retry connection"
            ]):
                groups["database_connection"].append(log)

            # Application health issues
            elif "health check" in message:
                groups["application_health"].append(log)

            # Other errors
            elif log["level"] in ["ERROR", "CRITICAL"]:
                groups["other"].append(log)

        return groups
    
    def generate_report(self):

        counts = self.count_levels()

        errors = self.get_errors()

        database_issues = self.find_database_issues()

        groups = self.group_errors()

        severity = self.determine_severity()

        root_cause = self.find_root_cause()

        print()
        print("=" * 60)
        print("              SRE LOG ANALYSIS REPORT")
        print("=" * 60)

        print()
        print(f"Total Log Lines : {len(self.logs)}")

        print()
        print("Log Levels")
        print("-" * 30)

        print(f"INFO            : {counts.get('INFO', 0)}")
        print(f"WARNING         : {counts.get('WARNING', 0)}")
        print(f"ERROR           : {counts.get('ERROR', 0)}")
        print(f"CRITICAL        : {counts.get('CRITICAL', 0)}")

        print()
        print("Detected Errors")
        print("-" * 30)

        if errors:

            for index, error in enumerate(errors, 1):

                print(
                    f"{index}. "
                    f"[{error['level']}] "
                    f"{error['message']}"
                )

        else:

            print("No errors detected.")

        print()
        print("Database Issues")
        print("-" * 30)

        if database_issues:

            for issue in database_issues:

                print(
                    f"- {issue['message']}"
                )

        else:

            print("No database issues detected.")

        print()
        print(f"Incident Severity : {severity}")

        print()
        print("Possible Root Cause")
        print("-" * 30)

        print(root_cause)

        print()
        print("=" * 60)
        print()
        print("Incident Groups")
        print("-" * 30)

        for group_name, incidents in groups.items():

            if incidents:

                print(
                    f"{group_name}: "
                    f"{len(incidents)} related log entries"
                )
