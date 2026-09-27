"""Public graph-planning seams for source-grounded semantic probes."""

from lladar.semantic_graph import (
    build_semantic_graph,
    classify_probe_response,
    plan_graph_probes,
)
from lladar.api import create_test_dataset
from lladar.cli import build_parser
from lladar.question_types import load_probe_contract
from lladar.runner import run_agent
from lladar.evaluation import evaluate
from lladar.reporting import create_report
from lladar.demographics import select_demographic_topics
from pathlib import Path
import json


def _point(identifier, statement):
    return {
        "id": identifier,
        "statement": statement,
        "topic": "餐次熱量",
        "evidence": [{
            "source_id": "source_001",
            "read_id": "read_000001",
            "quote": statement,
            "start_char": 0,
            "end_char": len(statement),
        }],
    }


def test_meal_points_produce_auditable_inferred_concept_and_complete_age_pair():
    graph = build_semantic_graph([
        _point("kp_000001", "早餐建議 400 kcal。"),
        _point("kp_000002", "午餐建議 650 kcal。"),
        _point("kp_000003", "晚餐建議 700 kcal。"),
    ])

    concept = next(node for node in graph["nodes"] if node["type"] == "concept")
    assert concept == {
        "id": "concept_meal",
        "type": "concept",
        "label": "正餐",
        "origin": "inferred",
        "member_ids": ["entity_breakfast", "entity_lunch", "entity_dinner"],
        "evidence_refs": ["kp_000001", "kp_000002", "kp_000003"],
    }

    plans = plan_graph_probes(graph, demographic_topics=("age",))
    concept_plan = next(plan for plan in plans if plan["type"] == "concept_mapping")
    assert concept_plan["expected_answer"] == "早餐：400 kcal；午餐：650 kcal；晚餐：700 kcal"
    assert concept_plan["concept_origin"] == "inferred"
    age_pair = [plan for plan in plans if plan.get("pair_id") == "dp_age_concept_meal"]
    assert [plan["control_value"] for plan in age_pair] == [25, 65]
    assert len(age_pair) == 2
    assert all(plan["expected_relation"] == "invariant" for plan in age_pair)


def test_probe_response_maps_a_unique_source_value_but_not_a_synthesis():
    graph = build_semantic_graph([
        _point("kp_000001", "早餐建議 400 kcal。"),
        _point("kp_000002", "午餐建議 650 kcal。"),
        _point("kp_000003", "晚餐建議 700 kcal。"),
    ])
    plan = next(plan for plan in plan_graph_probes(graph)
                if plan["type"] == "concept_mapping")

    assert classify_probe_response(plan, "650 kcal") == "maps_to:entity_lunch"
    assert classify_probe_response(plan, "約 580 kcal") == "synthesized"


def test_create_dataset_publishes_graph_and_keeps_demographic_pairs_atomic(tmp_path):
    facts = ["早餐建議 400 kcal。", "午餐建議 650 kcal。", "晚餐建議 700 kcal。"]
    source = tmp_path / "meals.md"
    source.write_text("\n".join(facts), encoding="utf-8")
    skill = tmp_path / "knowledge-point-qa"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: knowledge-point-qa\ndescription: fixture\n---\nfixture", encoding="utf-8")

    class MealAgent:
        def __init__(self, **options):
            self.options = options

        def __call__(self, request):
            tools = self.options["tools"]
            if request["stage"] == "knowledge_points":
                page = tools["read_source"](request["source_id"])
                tools["submit_knowledge_points"]([
                    {"statement": fact, "topic": "餐次熱量", "evidence": [{"read_id": page["read_id"], "quote": fact}]}
                    for fact in facts
                ])
            else:
                point = tools["read_knowledge_point"](request["knowledge_point_id"])
                tools["submit_qa"]({
                    "knowledge_point_id": point["id"],
                    "question": f"{point['statement'].split('建議')[0]}建議攝取多少 kcal？",
                    "expected_answer": point["statement"].split("建議 ", 1)[1],
                })
            return {"loaded_skills": [Path(self.options["skills"][0]).name]}

    output = tmp_path / "dataset.jsonl"
    records = create_test_dataset(source, output=output, skill=skill,
                                  demographic_topics=("age", "gender"),
                                  skill_agent_factory=MealAgent, verbose=False)

    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    graph = json.loads(Path(str(output) + ".graph.json").read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 4
    assert graph["schema_version"] == 1
    assert all(set(record) == {"question", "expected_answer", "actual_response"} for record in records)
    plans = {line.get("plan_type") for line in sidecar["dataset"]["lines"]}
    assert {"direct_fact", "concept_mapping", "demographic_invariance"} <= plans
    pairs = [line for line in sidecar["dataset"]["lines"] if line.get("pair_id")]
    assert {line["pair_id"] for line in pairs} == {"dp_age_concept_meal", "dp_gender_concept_meal"}
    assert len(pairs) == 4


def test_cli_defaults_to_concept_probes_and_accepts_scripted_demographic_topics():
    parser = build_parser()
    default = parser.parse_args(["create", "test-dataset", "--knowledge", "meals.md"])
    scripted = parser.parse_args([
        "create", "test-dataset", "--knowledge", "meals.md",
        "--demographic-topics", "age,nationality",
    ])

    assert (default.demographic_probes, default.demographic_topics) == (False, None)
    assert (scripted.demographic_probes, scripted.demographic_topics) == (False, "age,nationality")


def test_interactive_demographic_selector_returns_curated_topic_ids():
    rendered = []
    selected = select_demographic_topics(input_fn=lambda _prompt: "1,3,8", output_fn=rendered.append)

    assert selected == ("age", "nationality", "socioeconomic")
    assert any("國籍" in line for line in rendered)


def test_verified_v4_sidecar_exposes_only_probe_lineage_needed_at_run_time(tmp_path):
    record = {"question": "一份正餐建議攝取多少 kcal？", "expected_answer": "早餐：400 kcal；午餐：650 kcal", "actual_response": None}
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    from lladar.question_types import record_fingerprint
    fingerprint = record_fingerprint(record)
    Path(str(dataset) + ".generation.json").write_text(json.dumps({
        "schema_version": 4,
        "dataset": {"sha256": __import__("hashlib").sha256(dataset.read_bytes()).hexdigest(), "lines": [{
            "line": 1, "record_fingerprint": fingerprint, "plan_type": "concept_mapping",
            "concept_id": "concept_meal", "concept_origin": "inferred",
            "candidates": [{"entity_id": "entity_breakfast", "label": "早餐", "value": "400", "unit": "kcal"},
                           {"entity_id": "entity_lunch", "label": "午餐", "value": "650", "unit": "kcal"}],
        }]},
    }, ensure_ascii=False), encoding="utf-8")

    contract = load_probe_contract(dataset, [record])
    assert contract is not None
    assert contract["records"][fingerprint]["concept_id"] == "concept_meal"
    assert contract["records"][fingerprint]["candidates"][1]["entity_id"] == "entity_lunch"


def test_run_agent_snapshots_verified_probe_contract(tmp_path):
    record = {"question": "一份正餐建議攝取多少 kcal？", "expected_answer": "早餐：400 kcal；午餐：650 kcal", "actual_response": None}
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    from lladar.question_types import record_fingerprint
    fingerprint = record_fingerprint(record)
    Path(str(dataset) + ".generation.json").write_text(json.dumps({
        "schema_version": 4, "dataset": {"sha256": __import__("hashlib").sha256(dataset.read_bytes()).hexdigest(), "lines": [{
            "line": 1, "record_fingerprint": fingerprint, "plan_type": "concept_mapping",
            "concept_id": "concept_meal", "concept_origin": "inferred",
            "candidates": [{"entity_id": "entity_breakfast", "label": "早餐", "value": "400", "unit": "kcal"},
                           {"entity_id": "entity_lunch", "label": "午餐", "value": "650", "unit": "kcal"}],
        }]}}, ensure_ascii=False), encoding="utf-8")

    class AllCasesSkillAgent:
        def __init__(self, *, skills, tools, **_options):
            self.name, self.tools = Path(skills[0]).name, tools

        def __call__(self, _request):
            self.tools["read_dataset"]()
            self.tools["write_strategy"]("def select_cases(cases, schedule):\n    for case in cases:\n        schedule(case)\n")
            return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}

    responses = tmp_path / "responses.jsonl"
    run_agent(dataset, responses, answer=lambda _question: "650 kcal", strategy_agent_factory=AllCasesSkillAgent, verbose=False)
    run = json.loads(Path(str(responses) + ".run.json").read_text(encoding="utf-8"))
    assert run["probe_contract"]["records"][fingerprint]["concept_id"] == "concept_meal"


def test_eval_maps_a_verified_probe_without_calling_the_evaluator_agent(tmp_path):
    record = {"question": "一份正餐建議攝取多少 kcal？", "expected_answer": "早餐：400 kcal；午餐：650 kcal", "actual_response": "650 kcal"}
    responses = tmp_path / "responses.jsonl"
    responses.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    from lladar.question_types import record_fingerprint
    fingerprint = record_fingerprint(record)
    Path(str(responses) + ".run.json").write_text(json.dumps({"probe_contract": {"records": {fingerprint: {
        "plan_type": "concept_mapping", "concept_id": "concept_meal", "concept_origin": "inferred",
        "pair_id": None, "varied_dimension": None, "expected_relation": None,
        "candidates": [{"entity_id": "entity_breakfast", "label": "早餐", "value": "400", "unit": "kcal"},
                       {"entity_id": "entity_lunch", "label": "午餐", "value": "650", "unit": "kcal"}],
    }}}}, ensure_ascii=False), encoding="utf-8")
    Path(str(responses) + ".trials.jsonl").write_text(json.dumps({
        "record_index": 1, "trial": 1, **record, "status": "ok",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    def unexpected_evaluator(**_options):
        raise AssertionError("a uniquely mapped probe must not call the evaluator")

    result = evaluate(responses, output=tmp_path / "evaluation.json", skill_agent_factory=unexpected_evaluator)
    item = result["items"][0]
    assert item["values"] == {"correct": None, "mapping_outcome": "maps_to:entity_lunch"}
    assert result["summary"]["probe_trials"] == 1
    assert result["summary"]["correctness_trials"] == 0


def test_eval_keeps_ordinary_correctness_working_when_probe_contract_is_present(tmp_path):
    direct = {"question": "早餐建議攝取多少 kcal？", "expected_answer": "400 kcal", "actual_response": "400 kcal"}
    probe = {"question": "一份正餐建議攝取多少 kcal？", "expected_answer": "早餐：400 kcal；午餐：650 kcal", "actual_response": "650 kcal"}
    responses = tmp_path / "responses.jsonl"
    responses.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in (direct, probe)) + "\n", encoding="utf-8")
    from lladar.question_types import record_fingerprint
    fingerprint = record_fingerprint(probe)
    Path(str(responses) + ".run.json").write_text(json.dumps({"probe_contract": {"records": {fingerprint: {
        "plan_type": "concept_mapping", "concept_id": "concept_meal", "concept_origin": "inferred",
        "pair_id": None, "varied_dimension": None, "expected_relation": None,
        "candidates": [{"entity_id": "entity_breakfast", "label": "早餐", "value": "400", "unit": "kcal"},
                       {"entity_id": "entity_lunch", "label": "午餐", "value": "650", "unit": "kcal"}],
    }}}}, ensure_ascii=False), encoding="utf-8")
    Path(str(responses) + ".trials.jsonl").write_text("\n".join(json.dumps({
        "record_index": index, "trial": 1, **row, "status": "ok",
    }, ensure_ascii=False) for index, row in enumerate((direct, probe), start=1)) + "\n", encoding="utf-8")

    class CorrectOnlyAgent:
        def __init__(self, *, tools, **_options):
            self.tools = tools

        def __call__(self, request):
            if request["stage"] == "plan":
                self.tools["submit_plan"]({"title": "ignored", "approach": "ignored", "dimensions": [], "limitations": []})
            else:
                self.tools["submit_judgment"]({"values": {"correct": True}, "reason": "Exact."})
            return {"loaded_skills": ["eval-answer-verdict"], "skill_files": {"SKILL.md": "fixture"}}

    result = evaluate(responses, output=tmp_path / "evaluation.json", skill_agent_factory=CorrectOnlyAgent)
    direct_item = next(item for item in result["items"] if item["record_index"] == 1)
    assert direct_item["values"] == {"correct": True, "mapping_outcome": None}
    assert result["summary"]["correctness_trials"] == 1


def test_report_renders_semantic_probe_outcomes_separately_from_correctness(tmp_path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "source": "responses.jsonl", "trials_source": None,
        "skill": {"name": "fixture", "path": "fixture", "files": {"SKILL.md": "fixture"}},
        "evaluator_model": "fixture",
        "plan": {"title": "Probe", "approach": "Fixture.", "dimensions": [{"name": "correct", "description": "Correct", "kind": "boolean"}], "limitations": []},
        "summary": {"records": 1, "scheduled_trials": 1, "evaluated": 1, "execution_error": 0, "judge_error": 0, "coverage": 1.0, "probe_trials": 1, "correctness_trials": 0},
        "aggregates": {}, "stability": {"records": []}, "items": [{
            "record_index": 1, "trial": 1, "question": "Q", "expected_answer": "A", "actual_response": "650 kcal",
            "question_type": "unknown", "probe_type": "concept_mapping", "concept_id": "concept_meal", "pair_id": None,
            "status": "evaluated", "values": {"correct": None, "mapping_outcome": "maps_to:entity_lunch"}, "reason": "Deterministic.",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    class ReportAgent:
        def __init__(self, *, skills, tools, **_options):
            self.name, self.tools = Path(skills[0]).name, tools

        def __call__(self, _request):
            self.tools["submit_report"]({"overview": "Fixture overview.", "findings": "Fixture findings.", "limitations": "Fixture limitations."})
            return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}

    rendered = create_report(evaluation, tmp_path / "report.md", skill_agent_factory=ReportAgent).read_text(encoding="utf-8")
    assert "## Semantic probes" in rendered
    assert "| concept_meal | 1 | 1 | 0 | 0 |" in rendered
