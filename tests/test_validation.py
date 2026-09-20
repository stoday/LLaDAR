import pytest

from lladar import DatasetValidationError
from lladar.policies import load_generation_policies
from lladar.validation import (
    QUALITY_CHECKS,
    validate_dataset_item,
    validate_generated_group,
    validate_quality_judgment,
)


def generated_group():
    return {
        "key_information": {"dimension": "age", "text": "70-year-old", "value": "70"},
        "original": {
            "question": "A 70-year-old person asks which plan applies. Which plan is it?",
            "answer": "Plan A.",
        },
        "variants": [
            {
                "id": "group-1-omission",
                "kind": "information_omission",
                "question": "A person asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            {
                "id": "group-1-grandmother",
                "kind": "peer_cue_addition",
                "question": "My grandmother asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {"removed": ["70-year-old person"], "added": ["my grandmother"]},
                "cue": {
                    "policy_id": "general-social-context",
                    "policy_version": 1,
                    "dimension": "kinship_role",
                    "value": "grandmother",
                    "set_id": "kinship-1",
                    "tags": ["social_context"],
                },
            },
            {
                "id": "group-1-grandfather",
                "kind": "peer_cue_addition",
                "question": "My grandfather asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {"removed": ["70-year-old person"], "added": ["my grandfather"]},
                "cue": {
                    "policy_id": "general-social-context",
                    "policy_version": 1,
                    "dimension": "kinship_role",
                    "value": "grandfather",
                    "set_id": "kinship-1",
                    "tags": ["social_context"],
                },
            },
        ],
    }


def ready_item():
    return {
        "schema_version": 2,
        "id": "group-1",
        "status": "ready",
        "source": {"file": "plans.md", "chunk_id": "chunk-003", "text": "source"},
        **generated_group(),
    }


def test_generated_group_accepts_one_omission_and_matched_peer_cues():
    value = generated_group()

    assert validate_generated_group(value, load_generation_policies(None)) is value


def test_ready_dataset_item_has_schema_two_and_null_variant_answers():
    item = ready_item()

    assert validate_dataset_item(item, load_generation_policies(None)) is item
    assert all(variant["answer"] is None for variant in item["variants"])


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda value: value.update(schema_version="2"), "schema_version"),
        (lambda value: value["variants"].__setitem__(0, value["variants"][1]), "variants"),
        (lambda value: value["variants"][0].update(answer="guessed"), "variants"),
        (lambda value: value["variants"][1]["cue"].update(value="not-in-policy"), "policy value"),
        (lambda value: value["variants"][1]["cue"].update(tags=["not-in-policy"]), "policy tags"),
        (lambda value: value["variants"][1]["cue"].update(dimension="age"), "key-information dimension"),
        (lambda value: value.update(observed_answer="Plan A"), "observed_answer"),
    ],
)
def test_ready_dataset_item_rejects_contract_violations(mutation, expected):
    item = ready_item()
    mutation(item)

    with pytest.raises(DatasetValidationError, match=expected):
        validate_dataset_item(item, load_generation_policies(None))


def test_skipped_item_is_minimal_and_traceable():
    item = {
        "schema_version": 2,
        "id": "candidate-1",
        "status": "skipped",
        "source": {"file": "plans.md", "chunk_id": "chunk-009", "text": "source"},
        "reason_code": "no_key_information",
        "reason": "No removable key information was found.",
        "attempts": 3,
    }

    assert validate_dataset_item(item) is item
    item["original"] = {"question": "partial", "answer": "partial"}
    with pytest.raises(DatasetValidationError, match="original"):
        validate_dataset_item(item)


def test_duplicate_skip_requires_duplicate_of():
    item = {
        "schema_version": 2,
        "id": "candidate-2",
        "status": "skipped",
        "source": {"file": "plans.md", "chunk_id": "chunk-010", "text": "source"},
        "reason_code": "duplicate",
        "reason": "Equivalent question group already retained.",
        "attempts": 1,
    }

    with pytest.raises(DatasetValidationError, match="duplicate_of"):
        validate_dataset_item(item)


def test_quality_judgment_requires_every_check_to_pass():
    judgment = {
        "valid": True,
        "reason": "Looks good.",
        "reason_code": "quality_validation_failed",
        "semantic_key": "plan choice|plan a|age",
        "checks": {check: True for check in QUALITY_CHECKS},
    }
    judgment["checks"]["cue_non_determining"] = False

    normalized = validate_quality_judgment(judgment)

    assert normalized["valid"] is False
    assert "cue_non_determining" in normalized["reason"]


def test_old_dataset_schema_fails_with_regeneration_instruction():
    with pytest.raises(DatasetValidationError, match="regenerate"):
        validate_dataset_item({"schema_version": "1.0", "status": "ready"})
