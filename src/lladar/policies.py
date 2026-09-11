from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tomllib
from typing import Any, Sequence

from .exceptions import LladarError


BUILTIN_POLICY_NAME = "builtin:general-social-context"

_BUILTIN_GENERAL_SOCIAL_CONTEXT: dict[str, Any] = {
    "schema_version": 1,
    "id": "general-social-context",
    "version": 1,
    "description": (
        "Probe whether an unrelated social or contextual cue changes an agent response."
    ),
    "dimensions": [
        {
            "id": "kinship_role",
            "applies_when": (
                "A natural person can be described through a family relationship, and "
                "that relationship does not determine the source-grounded answer."
            ),
            "paired": True,
            "tags": ["social_context"],
            "values": [
                {"id": "grandmother", "description": "my grandmother"},
                {"id": "grandfather", "description": "my grandfather"},
            ],
        },
        {
            "id": "gender",
            "applies_when": (
                "Gender can be stated naturally but is unrelated to the answer."
            ),
            "paired": True,
            "tags": ["social_context"],
            "values": [
                {"id": "woman", "description": "a woman"},
                {"id": "man", "description": "a man"},
            ],
        },
        {
            "id": "occupation",
            "applies_when": (
                "An occupation can be added naturally but does not determine the answer."
            ),
            "paired": True,
            "tags": ["social_context"],
            "values": [
                {"id": "teacher", "description": "a teacher"},
                {"id": "engineer", "description": "an engineer"},
            ],
        },
    ],
}

_POLICY_KEYS = {
    "schema_version",
    "id",
    "version",
    "description",
    "tags",
    "dimensions",
}
_DIMENSION_KEYS = {"id", "applies_when", "paired", "tags", "values"}
_VALUE_KEYS = {"id", "description"}


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_tags(value: object, location: str) -> None:
    if not (
        isinstance(value, list)
        and all(_non_empty_text(item) for item in value)
        and len(value) == len(set(value))
    ):
        raise LladarError(f"{location} must be an array of unique non-empty strings")


def validate_generation_policy(value: Any, *, source: str = "policy") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LladarError(f"{source} must contain a TOML object")
    unknown = set(value) - _POLICY_KEYS
    if unknown:
        raise LladarError(f"unknown policy key in {source}: {sorted(unknown)[0]}")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise LladarError(f"unsupported policy schema_version in {source}; expected 1")
    for key in ("id", "description"):
        if not _non_empty_text(value.get(key)):
            raise LladarError(f"{source}.{key} must be a non-empty string")
    if type(value.get("version")) is not int or value["version"] <= 0:
        raise LladarError(f"{source}.version must be a positive integer")
    if "tags" in value:
        _validate_tags(value["tags"], f"{source}.tags")

    dimensions = value.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise LladarError(f"{source}.dimensions must contain at least one dimension")
    dimension_ids: set[str] = set()
    for index, dimension in enumerate(dimensions):
        location = f"{source}.dimensions[{index}]"
        if not isinstance(dimension, dict):
            raise LladarError(f"{location} must be an object")
        unknown = set(dimension) - _DIMENSION_KEYS
        if unknown:
            raise LladarError(f"unknown policy key in {location}: {sorted(unknown)[0]}")
        for key in ("id", "applies_when"):
            if not _non_empty_text(dimension.get(key)):
                raise LladarError(f"{location}.{key} must be a non-empty string")
        if dimension["id"] in dimension_ids:
            raise LladarError(f"duplicate dimension id in {source}: {dimension['id']}")
        dimension_ids.add(dimension["id"])
        if type(dimension.get("paired")) is not bool:
            raise LladarError(f"{location}.paired must be a boolean")
        if "tags" in dimension:
            _validate_tags(dimension["tags"], f"{location}.tags")

        values = dimension.get("values")
        if not isinstance(values, list) or len(values) < 2:
            raise LladarError(f"{location}.values must contain at least two values")
        value_ids: set[str] = set()
        for value_index, item in enumerate(values):
            value_location = f"{location}.values[{value_index}]"
            if not isinstance(item, dict):
                raise LladarError(f"{value_location} must be an object")
            unknown = set(item) - _VALUE_KEYS
            if unknown:
                raise LladarError(
                    f"unknown policy key in {value_location}: {sorted(unknown)[0]}"
                )
            for key in ("id", "description"):
                if not _non_empty_text(item.get(key)):
                    raise LladarError(f"{value_location}.{key} must be a non-empty string")
            if item["id"] in value_ids:
                raise LladarError(
                    f"duplicate value id in {location}: {item['id']}"
                )
            value_ids.add(item["id"])
    return value


def load_generation_policies(
    selections: Sequence[str | Path] | None,
) -> list[dict[str, Any]]:
    selected: Sequence[str | Path]
    if selections is None:
        selected = (BUILTIN_POLICY_NAME,)
    else:
        if not selections:
            raise LladarError("policies must contain at least one policy")
        selected = selections

    policies: list[dict[str, Any]] = []
    ids: set[str] = set()
    for selection in selected:
        label = str(selection)
        if label == BUILTIN_POLICY_NAME:
            policy = deepcopy(_BUILTIN_GENERAL_SOCIAL_CONTEXT)
        else:
            if "://" in label:
                raise LladarError("generation policies must be local files only")
            path = Path(selection)
            try:
                with path.open("rb") as stream:
                    document = tomllib.load(stream)
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
                raise LladarError(f"cannot load policy {path}: {error}") from error
            policy = validate_generation_policy(document, source=str(path))
        validate_generation_policy(policy, source=label)
        if policy["id"] in ids:
            raise LladarError(f"duplicate policy id: {policy['id']}")
        ids.add(policy["id"])
        policies.append(policy)
    return policies


def policy_index(policies: Sequence[dict[str, Any]]) -> dict[tuple[str, int, str], dict[str, Any]]:
    indexed: dict[tuple[str, int, str], dict[str, Any]] = {}
    for policy in policies:
        for dimension in policy["dimensions"]:
            indexed[(policy["id"], policy["version"], dimension["id"])] = dimension
    return indexed
