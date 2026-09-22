from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lladar.api import create_test_dataset
from lladar.cli import build_parser, main
from lladar.evaluation import evaluate
from lladar.exceptions import DatasetValidationError
from lladar.records import read_records, validate_record
from lladar.reporting import create_report
from lladar.runner import run_agent


class SequenceProvider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_structured(self, prompt, *, model, temperature):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_record_contract_is_exactly_three_fields():
    record = {
        "question": "Q?",
        "expected_answer": "A",
        "actual_response": None,
    }
    assert validate_record(record) == record
    with pytest.raises(DatasetValidationError, match="unknown score"):
        validate_record({**record, "score": 1})
    with pytest.raises(DatasetValidationError, match="missing actual_response"):
        validate_record({"question": "Q?", "expected_answer": "A"})


def test_create_dataset_writes_simple_records(tmp_path: Path):
    knowledge = tmp_path / "knowledge.md"
    knowledge.write_text("Taipei is the capital of Taiwan.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    provider = SequenceProvider(
        {"question": "What is Taiwan's capital?", "expected_answer": "Taipei"}
    )

    records = create_test_dataset(
        knowledge,
        output=output,
        chunk_size=1000,
        provider=provider,
        verbose=False,
    )

    assert records == [
        {
            "question": "What is Taiwan's capital?",
            "expected_answer": "Taipei",
            "actual_response": None,
        }
    ]
    assert read_records(output) == records

def test_cli_omitted_dataset_output_uses_a_named_directory(tmp_path: Path, monkeypatch):
    knowledge = tmp_path / "knowledge.md"
    knowledge.write_text("Taipei is the capital of Taiwan.", encoding="utf-8")
    provider = SequenceProvider(
        {"question": "What is Taiwan's capital?", "expected_answer": "Taipei"}
    )
    monkeypatch.chdir(tmp_path)

    assert main(
        ["create", "test-dataset", "--knowledge", str(knowledge), "--chunk-size", "1000"],
        provider=provider,
    ) == 0

    outputs = list(tmp_path.glob("test-dataset-*/dataset.jsonl"))
    assert len(outputs) == 1
    assert read_records(outputs[0])[0]["actual_response"] is None


def test_test_dataset_help_exposes_defaults():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    create_commands = commands["create"]._subparsers._group_actions[0].choices
    help_text = create_commands["test-dataset"].format_help()

    compact_help = re.sub(r"\s+", " ", help_text)
    assert re.search(r"\(default: \./test-dataset-\s*YYYYMMDD-HHMMSS/dataset\.jsonl\)", help_text)
    assert "(default: 0)" in compact_help
    assert "(default: auto)" in compact_help
    assert "(default: 0.1)" in compact_help
    assert "(default: gemini:gemini-3.7-flash)" in compact_help
    assert "(default: .env)" in compact_help
    assert "(default: selected model profile)" in compact_help
    assert "(default: none)" in compact_help

def test_run_agent_only_fills_actual_response_and_records_errors(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    output = tmp_path / "responses.jsonl"
    write_jsonl(
        dataset,
        [
            {"question": "one", "expected_answer": "1", "actual_response": None},
            {"question": "two", "expected_answer": "2", "actual_response": None},
        ],
    )

    def answer(question: str) -> str:
        if question == "two":
            raise RuntimeError("offline")
        return "1"

    assert run_agent(dataset, output, answer=answer, verbose=False) == 1
    assert read_records(output) == [
        {"question": "one", "expected_answer": "1", "actual_response": "1"},
        {"question": "two", "expected_answer": "2", "actual_response": None},
    ]
    sidecar = json.loads((tmp_path / "responses.jsonl.run.json").read_text(encoding="utf-8"))
    assert sidecar["completed"] == 1
    assert sidecar["failed"] == 1
    assert "score" not in sidecar


def test_eval_agent_judges_and_python_aggregates(tmp_path: Path):
    responses = tmp_path / "responses.jsonl"
    output = tmp_path / "evaluation.json"
    write_jsonl(
        responses,
        [
            {"question": "Capital?", "expected_answer": "Taipei", "actual_response": "Taipei"},
            {"question": "Capital?", "expected_answer": "Taipei", "actual_response": "Kaohsiung"},
            {"question": "Capital?", "expected_answer": "Taipei", "actual_response": None},
        ],
    )
    plan = {
        "title": "Answer quality",
        "approach": "Compare semantic meaning.",
        "dimensions": [
            {"name": "correct", "description": "Semantically correct", "kind": "boolean"},
            {"name": "quality", "description": "Quality score", "kind": "numeric"},
            {"name": "error_type", "description": "Error label", "kind": "categorical"},
        ],
        "limitations": ["Small sample."],
    }
    provider = SequenceProvider(
        plan,
        {"values": {"correct": True, "quality": 1, "error_type": "none"}, "reason": "Matches."},
        {"values": {"correct": False, "quality": 0, "error_type": "wrong_city"}, "reason": "Differs."},
    )

    result = evaluate(
        responses,
        output=output,
        prompt="Assess factual correctness.",
        provider=provider,
    )

    assert result["summary"] == {
        "total": 3,
        "evaluated": 2,
        "missing_response": 1,
        "judge_error": 0,
        "coverage": pytest.approx(2 / 3),
    }
    assert result["aggregates"]["correct"]["true_rate"] == 0.5
    assert result["aggregates"]["quality"]["mean"] == 0.5
    assert result["aggregates"]["error_type"]["distribution"] == {
        "none": 1,
        "wrong_city": 1,
    }
    assert json.loads(output.read_text(encoding="utf-8"))["evaluation_prompt"] == "Assess factual correctness."


def test_report_keeps_numbers_in_deterministic_tables(tmp_path: Path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "source": "responses.jsonl",
                "evaluation_prompt": "Assess correctness.",
                "evaluator_model": "test-model",
                "plan": {
                    "title": "Correctness",
                    "approach": "Compare answers.",
                    "dimensions": [
                        {"name": "correct", "description": "Correct answer", "kind": "boolean"}
                    ],
                    "limitations": ["Small sample."],
                },
                "summary": {
                    "total": 1,
                    "evaluated": 1,
                    "missing_response": 0,
                    "judge_error": 0,
                    "coverage": 1.0,
                },
                "aggregates": {
                    "correct": {
                        "kind": "boolean",
                        "description": "Correct answer",
                        "count": 1,
                        "missing": 0,
                        "true": 1,
                        "false": 0,
                        "true_rate": 1.0,
                    }
                },
                "items": [
                    {
                        "index": 1,
                        "question": "Capital?",
                        "expected_answer": "Taipei",
                        "actual_response": "Taipei",
                        "status": "evaluated",
                        "values": {"correct": True},
                        "reason": "Matches.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provider = SequenceProvider(
        {"overview": "本次評估比較預期答案與回應。", "findings": "回應與答案相符。", "limitations": "樣本量有限。"}
    )
    report = tmp_path / "report.md"

    assert create_report(evaluation, report, provider=provider) == report
    rendered = report.read_text(encoding="utf-8")
    assert "| total | 1 |" in rendered
    assert "| 1 | Capital? | Taipei | Taipei | evaluated" in rendered
    assert "本次評估比較預期答案與回應。" in rendered


def test_cli_surface_has_only_four_workflows():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    assert set(commands) == {"create", "run-agent", "eval", "report"}
    create_commands = commands["create"]._subparsers._group_actions[0].choices
    assert set(create_commands) == {"test-dataset"}
    with pytest.raises(SystemExit):
        parser.parse_args(["experiment", "run"])
    with pytest.raises(SystemExit):
        parser.parse_args(["skill", "install"])
