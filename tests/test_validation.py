import pytest

from lladar.exceptions import DatasetValidationError
from lladar.validation import validate_dataset_item, validate_quality_judgment


def test_validate_dataset_item_accepts_ready_item():
    item = {
        "status": "ready",
        "complete_question": "complete question",
        "complete_answer": "complete answer",
        "underspecified_question": "underspecified question",
        "missing_information": "missing information",
        "invalid_assumptions": ["unsupported answer"],
        "acceptable_behaviors": ["ask_clarification"],
    }

    assert validate_dataset_item(item) is item


def test_validate_dataset_item_accepts_skipped_item():
    item = {
        "status": "skipped",
        "id": "item-1",
        "source_file": "knowledge.md",
        "chunk_index": 0,
        "source_text": "source",
        "reason": "not suitable",
    }

    assert validate_dataset_item(item) is item


def test_validate_dataset_item_rejects_skipped_item_without_traceability():
    with pytest.raises(DatasetValidationError, match="source_file"):
        validate_dataset_item(
            {"status": "skipped", "id": "item-1", "reason": "not suitable"}
        )


def test_validate_quality_judgment_requires_all_strict_checks():
    with pytest.raises(DatasetValidationError, match="checks"):
        validate_quality_judgment({"valid": True, "reason": "valid"})
