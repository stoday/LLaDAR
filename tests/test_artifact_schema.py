from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lladar.artifact_schema import (
    ArtifactSchemaError,
    artifact_contract,
    artifact_version,
    validate_artifact,
)
from lladar.policies import load_generation_policies


def test_one_packaged_contract_defines_every_public_artifact():
    definitions = artifact_contract()["$defs"]
    assert artifact_version() == 2
    assert {
        "TestDatasetConfig",
        "GenerationPolicy",
        "DatasetRecord",
        "ObservedAnswerRecord",
        "EvaluationReport",
        "EvaluationItem",
    } <= definitions.keys()


def test_config_and_policy_use_the_same_artifact_version():
    config = {"schema_version": 2, "test_dataset": {"knowledge": ["./knowledge"]}}
    validate_artifact("TestDatasetConfig", config)
    validate_artifact("GenerationPolicy", load_generation_policies(None)[0])

    old = deepcopy(config)
    old["schema_version"] = 1
    with pytest.raises(ArtifactSchemaError, match="schema_version"):
        validate_artifact("TestDatasetConfig", old)


def test_dataset_and_answer_shapes_are_enforced_at_the_record_level():
    skipped = {
        "schema_version": 2,
        "id": "candidate-1",
        "status": "skipped",
        "source": {"file": "knowledge.md", "chunk_id": "chunk-1", "text": "A fact."},
        "reason_code": "no_key_information",
        "reason": "No removable information.",
        "attempts": 1,
    }
    answer = {
        "schema_version": 2,
        "id": "group-1",
        "group_id": "group-1",
        "kind": "original",
        "question": "Which plan applies?",
        "status": "ok",
        "answer": "",
    }
    validate_artifact("DatasetRecord", skipped)
    validate_artifact("ObservedAnswerRecord", answer)

    answer["unexpected"] = "value"
    with pytest.raises(ArtifactSchemaError, match="unexpected"):
        validate_artifact("ObservedAnswerRecord", answer)


def test_installed_skill_preflight_reads_the_packaged_contract(tmp_path: Path):
    script = (
        Path(__file__).parents[1]
        / "src/lladar/skill_assets/lladar-agent-evaluation/scripts/validate_qa_answers.py"
    )
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    record = {
        "schema_version": 2,
        "id": "candidate-1",
        "status": "skipped",
        "source": {"file": "knowledge.md", "chunk_id": "chunk-1", "text": "A fact."},
        "reason_code": "no_key_information",
        "reason": "No removable information.",
        "attempts": 1,
    }
    dataset.write_text(json.dumps(record) + "\n", encoding="utf-8")
    answers.write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), str(dataset), str(answers)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "0 schema-v2 case(s)" in result.stdout

    record["schema_version"] = 1
    dataset.write_text(json.dumps(record) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), str(dataset), str(answers)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "schema_version" in result.stdout
