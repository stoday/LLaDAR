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
QUALITY_CHECKS = (
    "standalone_question",
    "same_task",
    "source_supported_answer",
    "answer_determining_missing_fact",
    "multiple_supported_answers",
    "no_unresolved_references",
)


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


def validate_quality_judgment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("quality judgment must be an object")
    if not isinstance(value.get("valid"), bool):
        raise DatasetValidationError("quality judgment valid must be a boolean")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise DatasetValidationError("quality judgment reason must be non-empty")
    checks = value.get("checks")
    if not isinstance(checks, dict):
        raise DatasetValidationError("quality judgment checks must be an object")
    for check in QUALITY_CHECKS:
        if not isinstance(checks.get(check), bool):
            raise DatasetValidationError(f"quality judgment check must be boolean: {check}")
    failed = [check for check in QUALITY_CHECKS if not checks[check]]
    normalized = dict(value)
    if failed:
        normalized["valid"] = False
        normalized["reason"] = (
            f"{value['reason']} Failed checks: {', '.join(failed)}."
        )
    return normalized


def validate_dataset_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("dataset item must be an object")
    status = value.get("status")
    if status == "ready":
        return validate_generated_pair(value)
    if status == "skipped":
        for field in ("id", "source_file", "chunk_index", "source_text"):
            if field not in value or value[field] in (None, ""):
                raise DatasetValidationError(f"skipped item requires {field}")
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise DatasetValidationError("skipped item requires a non-empty reason")
        return value
    raise DatasetValidationError("dataset item status must be ready or skipped")
