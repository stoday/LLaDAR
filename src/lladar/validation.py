from __future__ import annotations

from typing import Any

from .exceptions import DatasetValidationError


REQUIRED_TEXT_FIELDS = (
    "complete_question",
    "complete_answer",
    "underspecified_question",
    "missing_information",
)
REQUIRED_BEHAVIORS = {
    "ask_clarification",
    "list_possibilities",
    "state_insufficient_information",
}


def validate_generated_pair(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("provider output must be an object")
    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise DatasetValidationError(f"{field} must be a non-empty string")
    assumptions = value.get("invalid_assumptions")
    if not isinstance(assumptions, list) or not assumptions or not all(
        isinstance(item, str) and item.strip() for item in assumptions
    ):
        raise DatasetValidationError("invalid_assumptions must contain text")
    behaviors = value.get("acceptable_behaviors")
    if not isinstance(behaviors, list) or not behaviors:
        raise DatasetValidationError("acceptable_behaviors must contain values")
    unknown = set(behaviors) - REQUIRED_BEHAVIORS
    if unknown:
        raise DatasetValidationError(f"unknown acceptable behaviors: {sorted(unknown)}")
    return value