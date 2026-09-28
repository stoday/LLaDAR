"""Public run, evaluation, and reporting lineage for controlled graph probes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lladar.evaluation import evaluate
from lladar.question_types import record_fingerprint
from lladar.reporting import create_report
from lladar.runner import run_agent


def _pair_records():
    expected = "Starter: 10 seats; Growth: 25 seats"
    return [
        {"question": "According to the source, how many seats does a plan include for a new customer?",
         "expected_answer": expected, "actual_response": None},
        {"question": "According to the source, how many seats does a plan include for a returning customer?",
         "expected_answer": expected, "actual_response": None},
    ]


def _write_v5_dataset(path: Path) -> list[dict]:
    records = _pair_records()
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    candidates = [
        {"entity_id": "entity_starter", "label": "Starter", "value": "10", "unit": "seats"},
        {"entity_id": "entity_growth", "label": "Growth", "value": "25", "unit": "seats"},
    ]
    lines = []
    for line_number, (record, control_value) in enumerate(zip(records, ("new customer", "returning customer")), start=1):
        lines.append({
            "line": line_number,
            "record_fingerprint": record_fingerprint(record),
            "plan_type": "controlled_invariance",
            "question_type": "free",
            "answer_protocol": "natural_language",
            "concept_id": "concept_plan",
            "concept_origin": "inferred",
            "candidates": candidates,
            "pair_id": "cv_customer_context_concept_plan",
            "varied_dimension": "customer_context",
            "control_dimension": {
                "id": "customer_context", "label": "customer context",
                "semantic_scope": "subscription context", "mutual_exclusivity": "declared",
                "coexists_with": [],
            },
            "control_value": control_value,
            "answer_contract": "invariant",
        })
    Path(str(path) + ".generation.json").write_text(json.dumps({
        "schema_version": 5,
        "dataset": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "lines": lines},
    }), encoding="utf-8")
    return records


class AllCasesStrategyAgent:
    def __init__(self, *, skills, tools, **_options):
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, _request):
        self.tools["read_dataset"]()
        self.tools["write_strategy"]("def select_cases(cases, schedule):\n    for case in cases:\n        schedule(case)\n")
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


class ReportAgent:
    def __init__(self, *, skills, tools, **_options):
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, _request):
        self.tools["submit_report"]({
            "overview": "Fixture overview.",
            "findings": "Fixture findings.",
            "limitations": "Fixture limitations.",
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


def test_controlled_variant_lineage_is_snapshotted_and_reported_outside_correctness(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    records = _write_v5_dataset(dataset)
    responses = tmp_path / "responses.jsonl"

    run_agent(
        dataset,
        responses,
        answer=lambda _question: "25 seats",
        strategy_agent_factory=AllCasesStrategyAgent,
        verbose=False,
    )
    run = json.loads(Path(str(responses) + ".run.json").read_text(encoding="utf-8"))
    assert {contract["answer_contract"] for contract in run["probe_contract"]["records"].values()} == {"invariant"}

    def unexpected_evaluator(**_options):
        raise AssertionError("a uniquely mapped probe must not call the evaluator")

    evaluation_path = tmp_path / "evaluation.json"
    evaluation = evaluate(responses, output=evaluation_path, skill_agent_factory=unexpected_evaluator)
    assert evaluation["summary"]["probe_trials"] == 2
    assert evaluation["summary"]["correctness_trials"] == 0
    assert {item["values"]["mapping_outcome"] for item in evaluation["items"]} == {"maps_to:entity_growth"}

    report = create_report(evaluation_path, tmp_path / "report.md", skill_agent_factory=ReportAgent).read_text(encoding="utf-8")
    assert "## Semantic probes" in report
    assert "| concept_plan | 2 | 2 | 0 | 0 |" in report
    assert "## Controlled variants" in report
    assert "| customer_context | cv_customer_context_concept_plan | 2 | 2 | 2 | 0 | 0 |" in report
    assert all(record["actual_response"] is None for record in records)
