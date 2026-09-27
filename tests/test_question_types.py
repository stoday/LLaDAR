from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from lladar.cli import build_parser
from lladar.evaluation import evaluate
from lladar.reporting import create_report
from lladar.runner import run_agent


def test_generation_sidecar_has_one_current_contract_version():
    from lladar.question_types import GENERATION_SIDECAR_VERSION
    import lladar.question_types as question_types

    source = Path(question_types.__file__).read_text(encoding="utf-8")
    assert GENERATION_SIDECAR_VERSION == 5
    assert source.count("GENERATION_SIDECAR_VERSION") == 3
    assert re.search(r"schema_version[^\n]*(?:2|3)", source) is None


def test_create_dataset_cli_exposes_question_type_with_compatible_default():
    parser = build_parser()

    default = parser.parse_args([
        "create", "test-dataset", "--knowledge", "knowledge.md",
    ])
    selected = parser.parse_args([
        "create", "test-dataset", "--knowledge", "knowledge.md",
        "--question-type", "multiple-choice",
    ])

    assert default.question_type == "free"
    assert selected.question_type == "multiple-choice"


def test_run_agent_snapshots_verified_v5_question_type_contract(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    record = {
        "question": "Which option is correct?\nA. First\nB. Second\nReply with one option ID.",
        "expected_answer": "B",
        "actual_response": None,
    }
    dataset.write_text(json.dumps(record) + "\n", encoding="utf-8")
    fingerprint = hashlib.sha256(
        json.dumps([record["question"], record["expected_answer"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    sidecar = {
        "schema_version": 5,
        "dataset": {
            "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "lines": [{
                "line": 1,
                "record_fingerprint": fingerprint,
                "question_type": "single_choice",
                "answer_protocol": "one_option_id",
                "options": [{"id": "A"}, {"id": "B"}],
                "correct_option_ids": ["B"],
            }],
        },
    }
    generation = Path(str(dataset) + ".generation.json")
    generation.write_text(json.dumps(sidecar), encoding="utf-8")

    class AllCasesSkillAgent:
        def __init__(self, *, skills, tools, **_options):
            self.name, self.tools = Path(skills[0]).name, tools

        def __call__(self, _request):
            self.tools["read_dataset"]()
            self.tools["write_strategy"](
                "def select_cases(cases, schedule):\n"
                "    for case in cases:\n"
                "        schedule(case)\n"
            )
            return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}

    responses = tmp_path / "responses.jsonl"
    assert run_agent(dataset, responses, answer=lambda _question: "B",
                     strategy_agent_factory=AllCasesSkillAgent, verbose=False) == 1

    run = json.loads(Path(str(responses) + ".run.json").read_text(encoding="utf-8"))
    contract = run["question_type_contract"]
    assert contract["dataset_sha256"] == sidecar["dataset"]["sha256"]
    assert contract["generation_sidecar_sha256"] == hashlib.sha256(generation.read_bytes()).hexdigest()
    assert contract["records"][fingerprint] == {
        "question_type": "single_choice",
        "answer_protocol": "one_option_id",
        "option_ids": ["A", "B"],
    }


def test_eval_deterministically_scores_typed_response_without_evaluator_agent(tmp_path):
    responses = tmp_path / "responses.jsonl"
    record = {
        "question": "Which option is correct?\nA. First\nB. Second\nReply with one option ID.",
        "expected_answer": "B",
        "actual_response": " b ",
    }
    responses.write_text(json.dumps(record) + "\n", encoding="utf-8")
    fingerprint = hashlib.sha256(
        json.dumps([record["question"], record["expected_answer"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    Path(str(responses) + ".run.json").write_text(json.dumps({
        "question_type_contract": {
            "dataset_sha256": "fixture-dataset",
            "generation_sidecar_sha256": "fixture-generation",
            "records": {fingerprint: {
                "question_type": "single_choice",
                "answer_protocol": "one_option_id",
                "option_ids": ["A", "B"],
            }},
        },
    }), encoding="utf-8")
    Path(str(responses) + ".trials.jsonl").write_text(json.dumps({
        "record_index": 1,
        "trial": 1,
        **record,
        "status": "ok",
    }) + "\n", encoding="utf-8")

    def unexpected_evaluator(**_options):
        raise AssertionError("typed response must not invoke an evaluator agent")

    result = evaluate(
        responses,
        output=tmp_path / "evaluation.json",
        skill_agent_factory=unexpected_evaluator,
    )

    item = result["items"][0]
    assert item["status"] == "evaluated"
    assert item["question_type"] == "single_choice"
    assert item["values"] == {"correct": True, "response_format_valid": True}
    assert item["reason"] == "Deterministic one_option_id comparison."


def test_report_renders_question_type_statistics(tmp_path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "source": "responses.jsonl", "trials_source": "responses.jsonl.trials.jsonl",
        "skill": {"name": "fixture", "path": "fixture", "files": {"SKILL.md": "fixture"}},
        "evaluator_model": "fixture", "plan": {"title": "Typed", "approach": "Fixture.",
        "dimensions": [{"name": "correct", "description": "Correct", "kind": "boolean"}], "limitations": []},
        "summary": {"records": 1, "scheduled_trials": 1, "evaluated": 1, "execution_error": 0, "judge_error": 0, "coverage": 1.0},
        "aggregates": {}, "stability": {"records": []}, "items": [{
            "record_index": 1, "trial": 1, "question": "Q", "expected_answer": "B", "actual_response": "B",
            "question_type": "single_choice", "status": "evaluated",
            "values": {"correct": True, "response_format_valid": True}, "reason": "Deterministic.",
        }],
    }), encoding="utf-8")

    class ReportAgent:
        def __init__(self, *, skills, tools, **_options):
            self.name, self.tools = Path(skills[0]).name, tools

        def __call__(self, _request):
            self.tools["submit_report"]({"overview": "Fixture overview.", "findings": "Fixture findings.", "limitations": "Fixture limitations."})
            return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}

    report = create_report(evaluation, tmp_path / "report.md", skill_agent_factory=ReportAgent)
    rendered = report.read_text(encoding="utf-8")
    assert "## By question type" in rendered
    assert "| single_choice | 1 | 0 | 1 | 0 | 1 | 1 |" in rendered
