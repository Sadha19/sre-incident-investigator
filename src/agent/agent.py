
import json

from openai import OpenAI

from src.config import load_settings
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import InvestigationTools


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_pods",
            "description": "List Pods and their status in a namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "sre-lab"}
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "Read recent logs from a Pod.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string"},
                    "namespace": {"type": "string", "default": "sre-lab"},
                    "tail_lines": {"type": "integer", "default": 100},
                },
                "required": ["pod_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "List Kubernetes events in a namespace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "default": "sre-lab"}
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_prometheus",
            "description": "Query Prometheus using a PromQL expression.",
            "parameters": {
                "type": "object",
                "properties": {"promql": {"type": "string"}},
                "required": ["promql"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deployment",
            "description": "Inspect a Kubernetes Deployment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deployment_name": {"type": "string"},
                    "namespace": {"type": "string", "default": "sre-lab"},
                },
                "required": ["deployment_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service",
            "description": "Inspect a Kubernetes Service and its selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "namespace": {"type": "string", "default": "sre-lab"},
                },
                "required": ["service_name"],
                "additionalProperties": False,
            },
        },
    },
]


class SREAgent:
    def __init__(self):
        settings = load_settings()
        settings.require_llm()
        self.model = settings.llm_model

        client_args = {
            "api_key": settings.llm_api_key,
            "timeout": settings.llm_timeout_s,
        }
        if settings.llm_base_url:
            client_args["base_url"] = settings.llm_base_url

        self.client = OpenAI(**client_args)
        self.tools = InvestigationTools()

        self.tool_functions = {
            "get_pods": self.tools.get_pods,
            "get_pod_logs": self.tools.get_pod_logs,
            "get_events": self.tools.get_events,
            "query_prometheus": self.tools.query_prometheus,
            "get_deployment": self.tools.get_deployment,
            "get_service": self.tools.get_service,
        }

    def investigate(self, incident, max_tool_rounds=6):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Investigate this incident:\n{incident}",
            },
        ]

        for _ in range(max_tool_rounds):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0,
            )

            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                return message.content or "The model returned no report."

            for tool_call in message.tool_calls:
                name = tool_call.function.name

                try:
                    arguments = json.loads(tool_call.function.arguments)

                    if name not in self.tool_functions:
                        raise ValueError(f"Unsupported tool: {name}")

                    result = self.tool_functions[name](**arguments)
                    result_text = json.dumps(result, default=str)

                    # Keep tool output bounded before sending it back to the LLM.
                    result_text = result_text[:20000]

                except Exception as exc:
                    result_text = json.dumps({
                        "error": f"{type(exc).__name__}: {exc}"
                    })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

        return (
            "Investigation stopped after reaching the tool-call limit. "
            "Please narrow the incident or increase max_tool_rounds."
        )
