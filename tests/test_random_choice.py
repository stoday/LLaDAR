from pathlib import Path

import pytest

import lladar
from lladar.validation import QUALITY_CHECKS


def _group(number: int):
    return {
        "key_information": {"dimension": "limit", "text": f"limit {number}"},
        "original": {"question": f"What is limit {number}?", "answer": str(number)},
        "variants": [
            {
                "kind": "information_omission",
                "question": "What is the limit?",
                "answer": None,
                "change": {"removed": [f"limit {number}"], "added": []},
            },
            *[
                {
                    "kind": "peer_cue_addition",
                    "question": f"What is the limit for my {role}?",
                    "answer": None,
                    "change": {"removed": [f"limit {number}"], "added": [f"my {role}"]},
                    "cue": {
                        "policy_id": "general-social-context",
                        "policy_version": 1,
                        "dimension": "kinship_role",
                        "value": role,
                        "set_id": f"set-{number}",
                        "tags": ["social_context"],
                    },
                }
                for role in ("grandmother", "grandfather")
            ],
        ],
    }


class CountingProvider:
    def __init__(self):
        self.group_calls = 0

    def generate_structured(self, prompt, *, model, temperature):
        if "Validate one generated question group" in prompt:
            return {
                "valid": True,
                "reason": "valid",
                "reason_code": "quality_validation_failed",
                "semantic_key": f"group-{self.group_calls}",
                "checks": {check: True for check in QUALITY_CHECKS},
            }
        self.group_calls += 1
        return _group(self.group_calls)


def test_count_limits_generation_to_ready_groups(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\ncc\ndd\nee\n", encoding="utf-8")
    provider = CountingProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2,
        overlap=0,
        count=2,
        seed=11,
        provider=provider,
    )

    assert len(dataset) == 2
    assert provider.group_calls == 2
    assert len({item["id"] for item in dataset}) == 2


def test_count_larger_than_candidates_keeps_every_available_group(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\n", encoding="utf-8")
    provider = CountingProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2,
        overlap=0,
        count=999,
        seed=2,
        provider=provider,
    )

    assert len(dataset) == provider.group_calls
    assert len(dataset) > 0


def test_default_count_zero_processes_every_candidate_chunk(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\ncc\ndd\nee\n", encoding="utf-8")
    provider = CountingProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2,
        overlap=0,
        seed=11,
        provider=provider,
    )

    assert len(dataset) > 1
    assert len(dataset) == provider.group_calls


def test_count_and_seed_must_have_valid_types(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("source", encoding="utf-8")

    with pytest.raises(ValueError, match="count"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            count=-1,
            provider=CountingProvider(),
        )
    with pytest.raises(ValueError, match="seed"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            seed=1.5,
            provider=CountingProvider(),
        )
