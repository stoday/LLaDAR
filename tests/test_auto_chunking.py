from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import lladar
from lladar.cli import main
from lladar.chunking import SourceUnit, build_semantic_chunking_prompt, semantic_window_char_limit
from lladar.validation import QUALITY_CHECKS


GROUP = {
    "key_information": {"dimension": "limit", "text": "10-user", "value": "10"},
    "original": {"question": "What is the 10-user plan limit?", "answer": "10."},
    "variants": [
        {
            "kind": "information_omission",
            "question": "What is the plan limit?",
            "answer": None,
            "change": {"removed": ["10-user"], "added": []},
        },
        *[
            {
                "kind": "peer_cue_addition",
                "question": f"What is the plan limit for my {role}?",
                "answer": None,
                "change": {"removed": ["10-user"], "added": [f"my {role}"]},
                "cue": {
                    "policy_id": "general-social-context",
                    "policy_version": 1,
                    "dimension": "kinship_role",
                    "value": role,
                    "set_id": "family-1",
                    "tags": ["social_context"],
                },
            }
            for role in ("grandmother", "grandfather")
        ],
    ],
}


JUDGMENT = {
    "valid": True,
    "reason": "valid",
    "reason_code": "quality_validation_failed",
    "semantic_key": "plan limit|10|limit",
    "checks": {check: True for check in QUALITY_CHECKS},
}


class AutoProvider:
    def __init__(self, *, invalid_segments: bool = False):
        self.invalid_segments = invalid_segments
        self.prompts: list[str] = []

    def generate_structured(self, prompt, *, model, temperature):
        self.prompts.append(prompt)
        if "Validate one generated question group" in prompt:
            return JUDGMENT
        if "semantic knowledge segmenter" in prompt:
            if self.invalid_segments:
                return {
                    "segments": [
                        {
                            "unit_ids": ["missing-unit"],
                            "knowledge_facts": ["A fact"],
                        }
                    ]
                }
            return {
                "segments": [
                    {
                        "unit_ids": ["u0", "u1"],
                        "knowledge_facts": [
                            "Plan A has a 10-user limit and Plan B has a 20-user limit; the plan selects the limit."
                        ],
                    },
                ]
            }
        return GROUP


def test_auto_chunking_generates_from_exact_semantic_segments(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    source = "Plan A has a limit of 10 users. Plan B has a limit of 20 users."
    knowledge.write_text(source, encoding="utf-8")
    provider = AutoProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        overlap=0.9,
        provider=provider,
        strict=True,
    )

    assert [item["source"]["text"] for item in dataset] == [source]
    segmentation_prompt = next(
        prompt for prompt in provider.prompts if "semantic knowledge segmenter" in prompt
    )
    assert "answerable knowledge unit" in segmentation_prompt
    assert "not limited to decisions or recommendations" in segmentation_prompt
    assert dataset[0]["source"]["locator"] == f"characters 0-{len(source)}"


def test_semantic_prompt_keeps_related_facts_in_one_answerable_unit():
    prompt = build_semantic_chunking_prompt(
        [
            SourceUnit("u0", 0, 22, "Breakfast: 400-500 calories."),
            SourceUnit("u1", 23, 42, "Lunch: 500-700 calories."),
        ]
    )

    assert "key information" in prompt
    assert "smallest contiguous source span" in prompt.replace("\n", " ")
    assert "source-grounded answer" in prompt


def test_semantic_prompt_requests_every_non_overlapping_eligible_unit():
    prompt = build_semantic_chunking_prompt(
        [
            SourceUnit("u0", 0, 23, "When dining out, choose visible ingredients."),
            SourceUnit("u1", 24, 56, "When eating at home, choose prepared safe foods."),
        ]
    )

    assert "every non-overlapping eligible unit" in prompt
    assert "not limited to decisions or recommendations" in prompt


def test_auto_chunking_is_strict_or_falls_back_to_fixed_chunks(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")

    with pytest.raises(lladar.ChunkingError):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size="auto",
            provider=AutoProvider(invalid_segments=True),
            strict=True,
        )

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        provider=AutoProvider(invalid_segments=True),
    )
    assert dataset[0]["source"]["locator"].startswith("characters ")


def test_cli_accepts_auto_chunk_size(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    knowledge.write_text(
        "Plan A has a limit of 10 users. Plan B has a limit of 20 users.",
        encoding="utf-8",
    )
    output = tmp_path / "dataset.jsonl"

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--chunk-size",
            "auto",
            "--output",
            str(output),
            "--strict",
        ],
        provider=AutoProvider(),
    )

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_semantic_segmentation_cache_is_independent_from_pair_cache(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    knowledge.write_text(
        "Plan A has a limit of 10 users. Plan B has a limit of 20 users.",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        cache=True,
        cache_dir=cache_dir,
        provider=AutoProvider(),
        strict=True,
    )

    class PairOnlyProvider:
        def generate_structured(self, prompt, *, model, temperature):
            if "Validate one generated question group" in prompt:
                return JUDGMENT
            assert "semantic knowledge segmenter" not in prompt
            return GROUP

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        prompt="Use a different question style.",
        cache=True,
        cache_dir=cache_dir,
        provider=PairOnlyProvider(),
        strict=True,
    )

    assert len(dataset) == 1
    assert list((cache_dir / "semantic_segments").glob("*.json"))


def test_auto_window_uses_eighty_percent_of_model_output_limit():
    assert semantic_window_char_limit("gemini:gemini-3.7-flash") == 52_428


def test_auto_chunking_deduplicates_segments_from_overlapping_safe_windows(
    tmp_path: Path,
):
    model = "gemini:gemini-2.5-flash"
    window_size = semantic_window_char_limit(model)
    fact = "Plan A has a limit of 10 users."
    fact_start = window_size - 1000
    source = ("x" * (fact_start - 2)) + ". " + fact + " " + ("Y" * 6000)
    knowledge = tmp_path / "long.txt"
    knowledge.write_text(source, encoding="utf-8")

    class OverlapProvider:
        def generate_structured(self, prompt, *, model, temperature):
            if "Validate one generated question group" in prompt:
                return JUDGMENT
            if "semantic knowledge segmenter" in prompt:
                match = re.search(
                    r'<unit id="([^"]+)">' + re.escape(fact) + r"</unit>",
                    prompt,
                )
                return {
                    "segments": (
                        [
                            {
                                "unit_ids": [match.group(1)],
                                "knowledge_facts": ["Plan A has a 10-user limit."],
                            }
                        ]
                        if match
                        else []
                    )
                }
            return GROUP

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        model=model,
        provider=OverlapProvider(),
        strict=True,
    )

    assert len(dataset) == 1
    assert dataset[0]["source"]["locator"] == (
        f"characters {fact_start}-{fact_start + len(fact)}"
    )

def test_user_can_override_auto_window_token_budget(tmp_path: Path):
    knowledge = tmp_path / "long.txt"
    knowledge.write_text("A fact. " * 30, encoding="utf-8")

    class SegmentOnlyProvider:
        def __init__(self):
            self.semantic_prompts = []

        def generate_structured(self, prompt, *, model, temperature):
            if "semantic knowledge segmenter" not in prompt:
                raise AssertionError("no question generation is expected")
            self.semantic_prompts.append(prompt)
            return {"segments": []}

    provider = SegmentOnlyProvider()
    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        max_output_tokens=100,
        auto_window_ratio=0.5,
        provider=provider,
        strict=True,
    )

    assert dataset == []
    assert len(provider.semantic_prompts) >= 3

@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_input_tokens": 0}, "max_input_tokens must be greater than 0"),
        ({"max_output_tokens": -1}, "max_output_tokens must be greater than 0"),
        (
            {"auto_window_ratio": 1.1},
            "auto_window_ratio must satisfy 0 < value <= 1",
        ),
    ],
)
def test_user_gets_clear_errors_for_invalid_model_overrides(
    tmp_path: Path,
    overrides,
    message,
):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact.", encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(message)):
        lladar.create_test_dataset(
            knowledge=knowledge,
            provider=AutoProvider(),
            **overrides,
        )

def test_input_token_override_invalidates_semantic_cache(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    knowledge.write_text(
        "Plan A has a limit of 10 users. Plan B has a limit of 20 users.",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        max_input_tokens=100,
        cache=True,
        cache_dir=cache_dir,
        provider=AutoProvider(),
        strict=True,
    )

    second_provider = AutoProvider()
    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        max_input_tokens=200,
        cache=True,
        cache_dir=cache_dir,
        provider=second_provider,
        strict=True,
    )

    assert any(
        "semantic knowledge segmenter" in prompt
        for prompt in second_provider.prompts
    )
