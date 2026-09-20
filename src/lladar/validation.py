from __future__ import annotations

from typing import Any, Sequence

from .exceptions import DatasetValidationError
from .artifact_schema import ArtifactSchemaError, artifact_contract, artifact_version, validate_artifact
from .policies import load_generation_policies, policy_index


QUALITY_CHECKS = (
    "original_standalone",
    "source_supported_answer",
    "key_information_supported",
    "same_task",
    "omission_material",
    "cue_applicable",
    "cue_non_determining",
    "change_record_accurate",
    "no_unresolved_references",
)

SKIP_REASON_CODES = set(
    artifact_contract()["$defs"]["DatasetSkipped"]["properties"]["reason_code"]["enum"]
)


def _text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{location} must be a non-empty string")
    return value


def _text_array(value: object, location: str, *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise DatasetValidationError(f"{location} must be an array of non-empty strings")
    if non_empty and not value:
        raise DatasetValidationError(f"{location} must contain at least one string")
    return value


def _exact_fields(value: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise DatasetValidationError(
            f"unknown {location} field: {sorted(unknown)[0]}"
        )


def _validate_source(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("source must be an object")
    _exact_fields(value, {"file", "chunk_id", "text", "locator"}, "source")
    for field in ("file", "chunk_id", "text"):
        _text(value.get(field), f"source.{field}")
    if "locator" in value:
        _text(value["locator"], "source.locator")
    return value


def validate_generated_group(
    value: Any,
    policies: Sequence[dict[str, Any]] | None = None,
    *,
    require_variant_ids: bool = False,
    check_policy_references: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("provider output must be an object")
    _exact_fields(value, {"key_information", "original", "variants"}, "generated-group")

    key_information = value.get("key_information")
    if not isinstance(key_information, dict):
        raise DatasetValidationError("key_information must be an object")
    _exact_fields(key_information, {"dimension", "text", "value"}, "key_information")
    key_dimension = _text(
        key_information.get("dimension"), "key_information.dimension"
    )
    _text(key_information.get("text"), "key_information.text")
    if "value" in key_information:
        _text(key_information["value"], "key_information.value")

    original = value.get("original")
    if not isinstance(original, dict):
        raise DatasetValidationError("original must be an object")
    _exact_fields(original, {"question", "answer"}, "original")
    _text(original.get("question"), "original.question")
    _text(original.get("answer"), "original.answer")

    variants = value.get("variants")
    if not isinstance(variants, list):
        raise DatasetValidationError("variants must be an array")
    omissions = [item for item in variants if isinstance(item, dict) and item.get("kind") == "information_omission"]
    peer_cues = [item for item in variants if isinstance(item, dict) and item.get("kind") == "peer_cue_addition"]
    if len(omissions) != 1:
        raise DatasetValidationError("variants must contain exactly one information_omission")
    if not 1 <= len(peer_cues) <= 4 or len(variants) != len(omissions) + len(peer_cues):
        raise DatasetValidationError("variants must contain between one and four peer_cue_addition items")

    selected_policies = (
        list(policies) if policies is not None else load_generation_policies(None)
    ) if check_policy_references else []
    dimensions = policy_index(selected_policies)
    allowed_cue_tags = {
        (policy["id"], policy["version"], dimension["id"]): set(
            policy.get("tags", []) + dimension.get("tags", [])
        )
        for policy in selected_policies
        for dimension in policy["dimensions"]
    }
    cue_keys: set[tuple[str, int, str]] = set()
    paired_groups: dict[tuple[str, int, str], list[dict[str, Any]]] = {}

    for index, variant in enumerate(variants):
        location = f"variants[{index}]"
        if not isinstance(variant, dict):
            raise DatasetValidationError(f"{location} must be an object")
        kind = variant.get("kind")
        allowed = {"id", "kind", "question", "answer", "change"}
        if kind == "peer_cue_addition":
            allowed.add("cue")
        _exact_fields(variant, allowed, "variant")
        if require_variant_ids:
            _text(variant.get("id"), f"{location}.id")
        elif "id" in variant:
            _text(variant["id"], f"{location}.id")
        if kind not in {"information_omission", "peer_cue_addition"}:
            raise DatasetValidationError(f"{location}.kind is invalid")
        _text(variant.get("question"), f"{location}.question")
        if variant.get("answer", object()) is not None:
            raise DatasetValidationError(f"{location}.answer must be null")
        change = variant.get("change")
        if not isinstance(change, dict):
            raise DatasetValidationError(f"{location}.change must be an object")
        _exact_fields(change, {"removed", "added"}, "change")
        removed = _text_array(change.get("removed"), f"{location}.change.removed", non_empty=True)
        added = _text_array(change.get("added"), f"{location}.change.added")

        if kind == "information_omission":
            if added:
                raise DatasetValidationError("information_omission change.added must be empty")
            continue
        if not added:
            raise DatasetValidationError("peer_cue_addition change.added must not be empty")
        if not removed:
            raise DatasetValidationError("peer_cue_addition change.removed must not be empty")

        cue = variant.get("cue")
        if not isinstance(cue, dict):
            raise DatasetValidationError(f"{location}.cue must be an object")
        _exact_fields(
            cue,
            {"policy_id", "policy_version", "dimension", "value", "set_id", "tags"},
            "cue",
        )
        policy_id = _text(cue.get("policy_id"), f"{location}.cue.policy_id")
        if type(cue.get("policy_version")) is not int or cue["policy_version"] <= 0:
            raise DatasetValidationError(f"{location}.cue.policy_version must be a positive integer")
        dimension_id = _text(cue.get("dimension"), f"{location}.cue.dimension")
        cue_value = _text(cue.get("value"), f"{location}.cue.value")
        if dimension_id == key_dimension:
            raise DatasetValidationError("peer cue cannot use the key-information dimension")
        cue_key = (policy_id, cue["policy_version"], dimension_id)
        dimension = dimensions.get(cue_key)
        if check_policy_references and dimension is None:
            raise DatasetValidationError("peer cue does not reference a selected policy dimension")
        if dimension is not None:
            allowed_values = {item["id"] for item in dimension["values"]}
            if cue_value not in allowed_values:
                raise DatasetValidationError(f"unknown policy value: {cue_value}")
        if "set_id" in cue and cue["set_id"] is not None:
            _text(cue["set_id"], f"{location}.cue.set_id")
        if "tags" in cue:
            tags = _text_array(cue["tags"], f"{location}.cue.tags")
            if len(tags) != len(set(tags)):
                raise DatasetValidationError("peer cue contains duplicate policy tags")
            if dimension is not None and not set(tags) <= allowed_cue_tags[cue_key]:
                raise DatasetValidationError("peer cue contains unknown or duplicate policy tags")
        cue_keys.add(cue_key)
        paired_groups.setdefault(cue_key, []).append(cue)

    if len(cue_keys) != 1:
        raise DatasetValidationError("all peer cues must use one policy dimension")
    for cue_key, cues in paired_groups.items():
        if not check_policy_references:
            continue
        dimension = dimensions[cue_key]
        if dimension["paired"]:
            if len(cues) < 2:
                raise DatasetValidationError("paired policy dimension requires at least two peer cues")
            set_ids = {cue.get("set_id") for cue in cues}
            if len(set_ids) != 1 or None in set_ids or "" in set_ids:
                raise DatasetValidationError("paired peer cues must share one non-empty set_id")
            if len({cue["value"] for cue in cues}) != len(cues):
                raise DatasetValidationError("paired peer cues must use distinct policy values")
    return value


def validate_quality_judgment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("quality judgment must be an object")
    _exact_fields(
        value,
        {"valid", "reason", "reason_code", "checks", "semantic_key"},
        "quality-judgment",
    )
    if type(value.get("valid")) is not bool:
        raise DatasetValidationError("quality judgment valid must be a boolean")
    _text(value.get("reason"), "quality judgment reason")
    checks = value.get("checks")
    if not isinstance(checks, dict):
        raise DatasetValidationError("quality judgment checks must be an object")
    if set(checks) != set(QUALITY_CHECKS):
        raise DatasetValidationError("quality judgment checks must contain exactly the required checks")
    for check in QUALITY_CHECKS:
        if type(checks[check]) is not bool:
            raise DatasetValidationError(f"quality judgment check must be boolean: {check}")
    reason_code = value.get("reason_code", "quality_validation_failed")
    if reason_code not in SKIP_REASON_CODES - {"duplicate"}:
        raise DatasetValidationError(f"invalid quality judgment reason_code: {reason_code}")
    if "semantic_key" in value:
        _text(value["semantic_key"], "quality judgment semantic_key")
    failed = [check for check in QUALITY_CHECKS if not checks[check]]
    normalized = dict(value)
    normalized["reason_code"] = reason_code
    if failed:
        normalized["valid"] = False
        normalized["reason"] = f"{value['reason']} Failed checks: {', '.join(failed)}."
    return normalized


def validate_dataset_item(
    value: Any,
    policies: Sequence[dict[str, Any]] | None = None,
    *,
    check_policy_references: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError("dataset item must be an object")
    if value.get("schema_version") != artifact_version():
        raise DatasetValidationError(
            "unsupported dataset schema_version; regenerate the dataset with schema version 2"
        )
    try:
        validate_artifact("DatasetRecord", value)
    except ArtifactSchemaError as error:
        raise DatasetValidationError(str(error)) from error
    _text(value.get("id"), "dataset item id")
    _validate_source(value.get("source"))
    status = value.get("status")
    if status == "ready":
        generated = {
            "key_information": value.get("key_information"),
            "original": value.get("original"),
            "variants": value.get("variants"),
        }
        validate_generated_group(
            generated,
            policies,
            require_variant_ids=True,
            check_policy_references=check_policy_references,
        )
        return value
    if status == "skipped":
        if value.get("reason_code") not in SKIP_REASON_CODES:
            raise DatasetValidationError("skipped item has an invalid reason_code")
        _text(value.get("reason"), "skipped item reason")
        if type(value.get("attempts")) is not int or not 1 <= value["attempts"] <= 3:
            raise DatasetValidationError("skipped item attempts must be between 1 and 3")
        if value["reason_code"] == "duplicate":
            _text(value.get("duplicate_of"), "duplicate_of")
        elif "duplicate_of" in value:
            raise DatasetValidationError("duplicate_of is only valid for duplicate skips")
        return value
    raise DatasetValidationError("dataset item status must be ready or skipped")
