import json
from pathlib import Path

import pytest

import lladar
from lladar.validation import QUALITY_CHECKS


def candidate(*, answer: str = "Plan A.", dimension: str = "age"):
    return {
        "key_information": {
            "dimension": dimension,
            "text": "70-year-old",
            "value": "70",
        },
        "original": {
            "question": "A 70-year-old person asks which plan applies. Which plan is it?",
            "answer": answer,
        },
        "variants": [
            {
                "kind": "information_omission",
                "question": "A person asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            {
                "kind": "peer_cue_addition",
                "question": "My grandmother asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {
                    "removed": ["70-year-old person"],
                    "added": ["my grandmother"],
                },
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
                "kind": "peer_cue_addition",
                "question": "My grandfather asks which plan applies. Which plan is it?",
                "answer": None,
                "change": {
                    "removed": ["70-year-old person"],
                    "added": ["my grandfather"],
                },
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


def judgment(*, valid: bool = True, semantic_key: str = "plan|plan a|age"):
    checks = {check: True for check in QUALITY_CHECKS}
    if not valid:
        checks["cue_non_determining"] = False
    return {
        "valid": valid,
        "reason": "The generated group was checked.",
        "reason_code": "quality_validation_failed",
        "semantic_key": semantic_key,
        "checks": checks,
    }


class FakeProvider:
    def __init__(self, judgments=None):
        self.prompts = []
        self.judgments = iter(judgments or [])

    def generate_structured(self, prompt, *, model, temperature):
        self.prompts.append(prompt)
        if "Validate one generated question group" in prompt:
            return next(self.judgments, judgment())
        return candidate()


def test_valid_group_is_schema_two_and_contains_no_variant_answers(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    knowledge.write_text(
        "Plan A applies at age 65 or older. Plan B applies below age 65.",
        encoding="utf-8",
    )
    provider = FakeProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        provider=provider,
    )

    assert len(dataset) == 1
    item = dataset[0]
    assert item["schema_version"] == 2
    assert item["status"] == "ready"
    assert item["source"] == {
        "file": str(knowledge),
        "chunk_id": "chunk-000",
        "text": knowledge.read_text(encoding="utf-8"),
    }
    assert item["original"]["answer"] == "Plan A."
    assert all(variant["answer"] is None for variant in item["variants"])
    assert all(variant["id"].startswith(item["id"]) for variant in item["variants"])
    forbidden = {"observed_answer", "decision", "score", "verdict", "complete_question"}
    assert forbidden.isdisjoint(item)
    assert len(provider.prompts) == 2


def test_failed_group_is_regenerated_three_times_then_minimally_skipped(tmp_path: Path):
    knowledge = tmp_path / "source.txt"
    knowledge.write_text("A source fact.", encoding="utf-8")
    provider = FakeProvider(judgments=[judgment(valid=False)] * 3)

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        provider=provider,
    )

    assert len(provider.prompts) == 6
    assert dataset == [
        {
            "schema_version": 2,
            "id": dataset[0]["id"],
            "status": "skipped",
            "source": {
                "file": str(knowledge),
                "chunk_id": "chunk-000",
                "text": "A source fact.",
            },
            "reason_code": "quality_validation_failed",
            "reason": dataset[0]["reason"],
            "attempts": 3,
        }
    ]


def test_provider_failure_is_operational_and_aborts(tmp_path: Path):
    knowledge = tmp_path / "source.txt"
    knowledge.write_text("A source fact.", encoding="utf-8")

    class FailingProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise lladar.ProviderError("provider unavailable")

    with pytest.raises(lladar.ProviderError, match="provider unavailable"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size=2000,
            provider=FailingProvider(),
        )


def test_count_counts_ready_groups_while_skips_remain_traceable(tmp_path: Path):
    knowledge = tmp_path / "source.txt"
    knowledge.write_text("first chunk\n\nsecond chunk", encoding="utf-8")
    provider = FakeProvider(
        judgments=[judgment(valid=False)] * 3 + [judgment(valid=True)]
    )

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=12,
        overlap=0,
        count=1,
        seed=7,
        provider=provider,
    )

    assert sum(item["status"] == "ready" for item in dataset) == 1
    assert sum(item["status"] == "skipped" for item in dataset) == 1


def test_semantic_duplicates_point_to_first_ready_group(tmp_path: Path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "a.txt").write_text("First wording.", encoding="utf-8")
    (knowledge / "b.txt").write_text("Equivalent wording.", encoding="utf-8")

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        count=2,
        seed=3,
        provider=FakeProvider(),
    )

    ready = next(item for item in dataset if item["status"] == "ready")
    duplicate = next(item for item in dataset if item["status"] == "skipped")
    assert duplicate["reason_code"] == "duplicate"
    assert duplicate["duplicate_of"] == ready["id"]


def test_ids_and_seeded_order_are_reproducible(tmp_path: Path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (knowledge / name).write_text(f"Source {name}", encoding="utf-8")

    first = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        count=2,
        seed=42,
        provider=FakeProvider(
            judgments=[judgment(semantic_key="one"), judgment(semantic_key="two")]
        ),
    )
    second = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        count=2,
        seed=42,
        provider=FakeProvider(
            judgments=[judgment(semantic_key="one"), judgment(semantic_key="two")]
        ),
    )

    assert [item["id"] for item in first] == [item["id"] for item in second]


def test_invalid_policy_fails_before_provider_execution(tmp_path: Path):
    knowledge = tmp_path / "source.txt"
    knowledge.write_text("A source fact.", encoding="utf-8")

    class ProviderMustNotRun:
        def generate_structured(self, prompt, *, model, temperature):
            raise AssertionError("provider must not run")

    with pytest.raises(lladar.LladarError, match="cannot load policy"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            policies=[tmp_path / "missing.toml"],
            provider=ProviderMustNotRun(),
        )


def test_jsonl_output_is_returned_and_protected_from_overwrite(tmp_path: Path):
    knowledge = tmp_path / "source.txt"
    knowledge.write_text("A source fact.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        output=output,
        provider=FakeProvider(),
    )

    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == dataset
    with pytest.raises(FileExistsError):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size=2000,
            output=output,
            provider=FakeProvider(),
        )
    with pytest.raises(ValueError, match="jsonl"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size=2000,
            format="json",
            provider=FakeProvider(),
        )
