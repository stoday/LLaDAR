"""The versioned, untrusted input contract for the planning module."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class PlanError(ValueError):
    def __init__(self, code: str, message: str, *, stage: str = "schema",
                 field: str = "", cause: str = "", next_action: str = "Correct the indicated field.",
                 recoverable: bool = True):
        super().__init__(f"{code}: {message}")
        self.diagnostic = {
            "code": code, "stage": stage, "message": message,
            "field_path": field.split(".") if field else [],
            "cause": cause or message, "next_action": next_action,
            "recoverable": recoverable,
        }


# JSON-schema-shaped data serves both the parser and the model's contract.
def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
NULL_TEXT = {"type": ["string", "null"]}
POSITIVE = {"type": "integer", "minimum": 1}
PLAN_SCHEMA = _object({
    "schema_version": {"type": "integer", "enum": [2]},
    "workflow": TEXT,
    "status": {"type": "string", "enum": ["draft"]},
    "user_goal": TEXT, "observable_behavior": TEXT,
    "observation": _object({"kind": {"type": "string", "enum": ["text", "external_state"]}}),
    "inputs": _object({"knowledge": TEXTS, "dataset": NULL_TEXT, "responses": NULL_TEXT,
                       "evaluation": NULL_TEXT, "situation_config": NULL_TEXT}),
    "target": _object({"kind": TEXT, "path": NULL_TEXT, "url": NULL_TEXT,
                       "api_guide": NULL_TEXT, "service_url": NULL_TEXT}),
    "method": _object({"kind": TEXT, "count": POSITIVE, "instructions": TEXT,
                       "stop_criteria": TEXT, "max_turns": POSITIVE, "num_scenarios": POSITIVE}),
    "evaluation": _object({"kind": TEXT, "criteria": NULL_TEXT, "skill": NULL_TEXT}),
    "stages": {"type": "array", "items": {"type": "string", "enum": ["create", "run-agent", "eval", "report"]}},
    "assumptions": TEXTS, "limitations": TEXTS, "open_questions": TEXTS,
    "evidence": {"type": "array", "items": _object({"path": TEXT, "description": TEXT}, ["path"])},
    "diagnostics": {"type": "array", "maxItems": 0},
}, ["schema_version", "workflow", "status", "user_goal", "observable_behavior", "inputs", "method", "stages"])


def check_schema(value: Any, schema: dict, field: str = "", *, partial: bool = False):
    if not schema:
        return
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "integer": type(value) is int, "null": value is None}
    if types and not any(matches[t] for t in types):
        raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"{field or 'response'} must be {' or '.join(types)}", field=field)
    if "enum" in schema and value not in schema["enum"]:
        raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Unsupported value at {field}: {value!r}", field=field)
    if isinstance(value, dict):
        properties = schema["properties"]
        for key in value:
            path = f"{field}.{key}" if field else key
            if key not in properties:
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Unknown field: {path}", field=path)
            check_schema(value[key], properties[key], path, partial=partial)
        if not partial:
            for key in schema.get("required", []):
                if key not in value:
                    path = f"{field}.{key}" if field else key
                    raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Missing schema field: {path}", field=path)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"{field} must be empty", field=field)
        for index, item in enumerate(value):
            check_schema(item, schema.get("items", {}), f"{field}.{index}", partial=partial)
    if type(value) is int and value < schema.get("minimum", value):
        raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"{field} must be positive", field=field)


def parse_spec(value: Any) -> dict:
    if isinstance(value, dict) and isinstance(value.get("commands"), list):
        for index, argv in enumerate(value["commands"]):
            if isinstance(argv, list) and any(isinstance(arg, str) and arg.startswith("--") for arg in argv):
                stage = argv[1] if len(argv) > 1 and isinstance(argv[1], str) else "unknown"
                flags = [arg for arg in argv if isinstance(arg, str) and arg.startswith("--")]
                raise PlanError("PLAN_COMPILATION_ERROR", f"Model-supplied argv rejected: stage={stage}, flags={flags}",
                                stage="compile", field=f"commands.{index}",
                                next_action="Return only structured PlanSpec fields; the host compiler selects supported flags.")
    check_schema(value, PLAN_SCHEMA)
    spec = {"target": {}, "evaluation": {}, "observation": {"kind": "text"},
            "assumptions": [], "limitations": [], "open_questions": [], "evidence": [],
            "diagnostics": [], **deepcopy(value)}
    spec["commands"] = []
    return spec
