from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import lladar
from lladar.cli import main
from lladar.chunking import semantic_window_char_limit


PAIR = {
    "complete_question": "If the limit is 10, what is the limit?",
    "complete_answer": "10",
    "underspecified_question": "What is the limit?",
    "missing_information": "Which limit applies.",
    "invalid_assumptions": ["The limit is 10."],
    "acceptable_behaviors": ["ask_clarification"],
}


class AutoProvider:
    def __init__(self, *, invalid_segments: bool = False):
        self.invalid_segments = invalid_segments
        self.prompts: list[str] = []

    def generate_structured(self, prompt, *, model, temperature):
        self.prompts.append(prompt)
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
                        "unit_ids": ["u0"],
                        "knowledge_facts": ["Plan A has a 10-user limit."],
                    },
                    {
                        "unit_ids": ["u1"],
                        "knowledge_facts": ["Plan B has a 20-user limit."],
                    },
                ]
            }
        return PAIR


def test_auto_chunking_generates_from_exact_semantic_segments(tmp_path: Path):
    knowledge = tmp_path / "plans.txt"
    source = "Plan A has a limit of 10 users. Plan B has a limit of 20 users."
    knowledge.write_text(source, encoding="utf-8")

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        overlap=0.9,
        provider=AutoProvider(),
        strict=True,
    )

    assert [item["source_text"] for item in dataset] == [
        "Plan A has a limit of 10 users.",
        "Plan B has a limit of 20 users.",
    ]
    assert dataset[0]["metadata"] == {
        "strategy": "ambiguity",
        "model": "gemini:gemini-2.5-flash",
        "temperature": 0.0,
        "chunk_method": "semantic_auto",
        "source_start": 0,
        "source_end": 31,
        "knowledge_facts": ["Plan A has a 10-user limit."],
    }


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
    assert dataset[0]["metadata"]["chunk_method"] == "character_fallback"


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
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


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
        num_pairs=1,
        cache=True,
        cache_dir=cache_dir,
        provider=AutoProvider(),
        strict=True,
    )

    class PairOnlyProvider:
        def generate_structured(self, prompt, *, model, temperature):
            assert "semantic knowledge segmenter" not in prompt
            return PAIR

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        num_pairs=2,
        cache=True,
        cache_dir=cache_dir,
        provider=PairOnlyProvider(),
        strict=True,
    )

    assert len(dataset) == 4
    assert list((cache_dir / "semantic_segments").glob("*.json"))


def test_auto_window_uses_eighty_percent_of_model_output_limit():
    assert semantic_window_char_limit("gemini:gemini-2.5-flash") == 52_428


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
            return PAIR

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        model=model,
        provider=OverlapProvider(),
        strict=True,
    )

    assert len(dataset) == 1
    assert dataset[0]["metadata"]["source_start"] == fact_start
    assert dataset[0]["metadata"]["source_end"] == fact_start + len(fact)

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
