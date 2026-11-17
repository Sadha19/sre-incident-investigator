import json


class Incident:

    def __init__(
        self,
        incident_id,
        severity,
        category,
        component,
        evidence,
        root_cause,
        recommended_actions
    ):
        self.incident_id = incident_id
        self.severity = severity
        self.category = category
        self.component = component
        self.evidence = evidence
        self.root_cause = root_cause
        self.recommended_actions = recommended_actions

    def to_dict(self):

        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "category": self.category,
            "component": self.component,
            "evidence": self.evidence,
            "root_cause": self.root_cause,
            "recommended_actions": self.recommended_actions
        }

    def to_json(self):

        return json.dumps(
            self.to_dict(),
            indent=4
        )
