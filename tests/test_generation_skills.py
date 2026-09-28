"""Public generation contracts for the graph-and-plan skill workflow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lladar.api import create_test_dataset
from lladar.cli import build_parser, main
from lladar.exceptions import DatasetValidationError


FACTS = (
    "Service A keeps data for 30 days.",
    "Service B keeps data for 60 days.",
    "Support opens at 09:00.",
)
QUESTIONS = (
    "How long does A keep data?",
    "How long does B keep data?",
    "When does support open?",
)
ANSWERS = ("30 days.", "60 days.", "09:00.")


@pytest.fixture
def generation_inputs(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("\n".join(FACTS), encoding="utf-8")
    skill = tmp_path / "source-graph"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: source-graph\ndescription: fixture\n---\nUse the supplied tools.\n",
        encoding="utf-8",
    )
    return source, skill


class SourceGraphAgent:
    """External model adapter that uses only the generation tool interface."""

    def __init__(self, *, skills, tools, **_options):
        self.skill = Path(skills[0])
        self.name = self.skill.name
        self.tools = tools

    def __call__(self, request):
        if request["stage"] == "knowledge_points":
            page = self.tools["read_source"](request["source_id"])
            accepted = self.tools["submit_knowledge_points"]([
                {"statement": fact, "topic": "Service", "evidence": [{"read_id": page["read_id"], "quote": fact}]}
                for fact in FACTS
            ])
            assert len(accepted["accepted"]) == len(FACTS)
        elif request["stage"] == "semantic_graph":
            points = {point["statement"]: point["id"] for point in self.tools["list_knowledge_points"]()}
            self.tools["submit_semantic_graph"]({
                "nodes": [
                    {"id": "entity_service_a", "type": "entity", "label": "Service A", "origin": "source", "evidence_refs": [points[FACTS[0]]]},
                    {"id": "entity_service_b", "type": "entity", "label": "Service B", "origin": "source", "evidence_refs": [points[FACTS[1]]]},
                    {"id": "entity_support", "type": "entity", "label": "Support", "origin": "source", "evidence_refs": [points[FACTS[2]]]},
                ],
                "edges": [],
                "facts": [
                    {"entity_id": "entity_service_a", "label": "Service A", "value": "30", "unit": "days.", "evidence_ref": points[FACTS[0]]},
                    {"entity_id": "entity_service_b", "label": "Service B", "value": "60", "unit": "days.", "evidence_ref": points[FACTS[1]]},
                    {"entity_id": "entity_support", "label": "Support", "value": "09:00.", "unit": "", "evidence_ref": points[FACTS[2]]},
                ],
            })
        elif request["stage"] == "test_plans":
            self.tools["submit_test_plans"]([
                {"type": "direct_fact", "entity_id": "entity_service_a", "question": QUESTIONS[0], "expected_answer": ANSWERS[0]},
                {"type": "direct_fact", "entity_id": "entity_service_b", "question": QUESTIONS[1], "expected_answer": ANSWERS[1]},
                {"type": "direct_fact", "entity_id": "entity_support", "question": QUESTIONS[2], "expected_answer": ANSWERS[2]},
            ])
        else:
            raise AssertionError(f"unexpected stage: {request['stage']}")
        return {
            "loaded_skills": [self.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }


@pytest.mark.parametrize("option", [
    "--method", "--chunk-size", "--overlap", "--strict", "--prompt", "--prompt-file", "--demographic-topics",
])
def test_create_cli_rejects_retired_generation_options(option):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["create", "test-dataset", "--knowledge", "notes.md", option, "value"])


def test_create_dataset_uses_graph_and_plan_stages_for_direct_facts(generation_inputs, tmp_path):
    source, skill = generation_inputs
    output = tmp_path / "dataset.jsonl"

    records = create_test_dataset(source, skill=skill, output=output, skill_agent_factory=SourceGraphAgent, verbose=False)

    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert {record["question"] for record in records} == set(QUESTIONS)
    assert sidecar["schema_version"] == 5
    assert {line["plan_type"] for line in sidecar["dataset"]["lines"]} == {"direct_fact"}
    assert Path(str(output) + ".graph.json").is_file()
    assert sidecar["dataset"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_bundled_skill_is_default_and_uses_current_stage_instructions(generation_inputs, tmp_path, monkeypatch):
    source, _ = generation_inputs
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "dataset.jsonl"

    create_test_dataset(source, output=output, skill_agent_factory=SourceGraphAgent, verbose=False)

    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    bundled = Path(__import__("lladar").__file__).resolve().parent / "skill_assets" / "knowledge-point-qa"
    instructions = (bundled / "SKILL.md").read_text(encoding="utf-8")
    assert sidecar["skill"]["path"] == str(bundled)
    assert "semantic_graph" in instructions and "test_plans" in instructions


def test_rejected_source_candidate_can_be_corrected_before_graph_generation(generation_inputs):
    source, skill = generation_inputs

    class CorrectingAgent(SourceGraphAgent):
        def __call__(self, request):
            if request["stage"] == "knowledge_points":
                page = self.tools["read_source"](request["source_id"])
                rejected = self.tools["submit_knowledge_points"]([
                    {"statement": "Invented", "topic": "Service", "evidence": [{"read_id": page["read_id"], "quote": "missing"}]},
                ])
                assert rejected["accepted"] == []
            return super().__call__(request)

    records = create_test_dataset(source, skill=skill, skill_agent_factory=CorrectingAgent, verbose=False)
    assert len(records) == 3


def test_missing_or_invalid_skill_fails_before_model_work(generation_inputs, tmp_path, monkeypatch):
    source, _ = generation_inputs
    import lladar.api as api

    monkeypatch.setattr(api, "BUILTIN_SKILL_DIR", tmp_path / "missing")
    with pytest.raises(FileNotFoundError, match="SKILL.md"):
        create_test_dataset(source, skill_agent_factory=lambda **_: pytest.fail("agent must not start"), verbose=False)


def test_existing_output_is_rejected_before_model_work(generation_inputs, tmp_path):
    source, skill = generation_inputs
    output = tmp_path / "dataset.jsonl"
    output.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_test_dataset(source, skill=skill, output=output,
                            skill_agent_factory=lambda **_: pytest.fail("agent must not start"), verbose=False)
    assert output.read_text(encoding="utf-8") == "preserve"


def test_missing_graph_or_plan_never_publishes_an_output(generation_inputs, tmp_path):
    source, skill = generation_inputs
    output = tmp_path / "dataset.jsonl"
    output.write_text("previous", encoding="utf-8")

    class NoGraphAgent(SourceGraphAgent):
        def __call__(self, request):
            if request["stage"] == "semantic_graph":
                return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}
            return super().__call__(request)

    with pytest.raises(DatasetValidationError, match="semantic graph"):
        create_test_dataset(source, skill=skill, output=output, force=True,
                            skill_agent_factory=NoGraphAgent, verbose=False)
    assert output.read_text(encoding="utf-8") == "previous"


def test_generation_stage_tools_are_scoped_and_expire_between_stages(generation_inputs):
    source, skill = generation_inputs
    held = []

    class CapabilityAgent(SourceGraphAgent):
        def __call__(self, request):
            if request["stage"] == "knowledge_points":
                held.append(self.tools["read_source"])
                assert set(self.tools) == {"list_sources", "read_source", "submit_knowledge_points"}
            elif request["stage"] == "semantic_graph":
                with pytest.raises(ValueError, match="expired"):
                    held[0](request.get("source_id", "source_001"))
                assert set(self.tools) == {"list_knowledge_points", "submit_semantic_graph"}
            elif request["stage"] == "test_plans":
                assert set(self.tools) == {"read_semantic_graph", "submit_test_plans"}
            return super().__call__(request)

    assert len(create_test_dataset(source, skill=skill, skill_agent_factory=CapabilityAgent, verbose=False)) == 3


def test_verbose_generation_reports_graph_and_plan_stages(generation_inputs, capsys):
    source, skill = generation_inputs
    create_test_dataset(source, skill=skill, skill_agent_factory=SourceGraphAgent, verbose=True)
    captured = capsys.readouterr()
    assert "[KNOWLEDGE_POINTS]" in captured.err
    assert "[SEMANTIC_GRAPH]" in captured.err
    assert "[TEST_PLANS]" in captured.err


def test_create_cli_reports_graph_and_controlled_variant_summary(generation_inputs, tmp_path, capsys):
    source, skill = generation_inputs

    assert main([
        "create", "test-dataset", "--knowledge", str(source), "--skill", str(skill),
        "--output", str(tmp_path / "out"), "--no-verbose",
    ], skill_agent_factory=SourceGraphAgent) == 0

    summary = capsys.readouterr().out
    assert "concepts=0" in summary
    assert "controlled_dimensions=0" in summary
    assert "controlled_records=0" in summary
