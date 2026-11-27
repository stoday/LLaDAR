"""Public dataset-generation behavior for source-grounded controlled variants."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

import pytest

from coverage_test_support import SourceCoverageAgent, create_test_dataset
from lladar.cli import main
from lladar.controlled_variants import select_controlled_variant_topics
from lladar.exceptions import DatasetValidationError
from lladar.skill_generation import GenerationResult


SOURCE_FACTS = (
    "Starter plan includes 10 seats.",
    "Growth plan includes 25 seats.",
)


class GenericGraphSkillAgent:
    """A system-boundary fake for combined and staged dataset generation."""

    def __init__(self, *, skills, tools, **_options):
        self.skill = Path(skills[0])
        self.name = self.skill.name
        self.tools = tools

    dimension_exclusivity = "declared"

    def __call__(self, request):
        if "submit_dataset_batch" in self.tools:
            return self.submit_batch(request)
        combined = request["stage"] == "controlled_variant_planning"
        if combined:
            points = {point["statement"]: point["id"] for point in request["inventory"]["points"]}
        elif request["stage"] == "generation":
            page = self.tools["read_source"]("source_001")
            self.tools["submit_knowledge_points"]([
                {
                    "statement": fact,
                    "topic": "Subscription plans",
                    "evidence": [{"read_id": page["read_id"], "quote": fact}],
                }
                for fact in SOURCE_FACTS
            ])
            points = {point["statement"]: point["id"] for point in self.tools["list_knowledge_points"]()}
        else:
            raise AssertionError(f"unexpected stage: {request['stage']}")
        graph = {
            "nodes": [
                {"id": "entity_starter", "type": "entity", "label": "Starter", "origin": "source",
                 "evidence_refs": [points[SOURCE_FACTS[0]]]},
                {"id": "entity_growth", "type": "entity", "label": "Growth", "origin": "source",
                 "evidence_refs": [points[SOURCE_FACTS[1]]]},
                {"id": "attribute_seats", "type": "attribute", "label": "seats", "origin": "source",
                 "evidence_refs": [points[SOURCE_FACTS[0]], points[SOURCE_FACTS[1]]]},
                {"id": "concept_plan", "type": "concept", "label": "plan", "origin": "inferred",
                 "member_ids": ["entity_starter", "entity_growth"],
                 "evidence_refs": [points[SOURCE_FACTS[0]], points[SOURCE_FACTS[1]]]},
            ],
            "facts": [
                {"entity_id": "entity_starter", "label": "Starter", "value": "10", "unit": "seats",
                 "evidence_ref": points[SOURCE_FACTS[0]]},
                {"entity_id": "entity_growth", "label": "Growth", "value": "25", "unit": "seats",
                 "evidence_ref": points[SOURCE_FACTS[1]]},
            ],
            "edges": [
                {"from": "entity_starter", "relation": "has_value", "to": "attribute_seats",
                 "origin": "source", "evidence_refs": [points[SOURCE_FACTS[0]]]},
                {"from": "entity_growth", "relation": "has_value", "to": "attribute_seats",
                 "origin": "source", "evidence_refs": [points[SOURCE_FACTS[1]]]},
                {"from": "entity_starter", "relation": "is_a", "to": "concept_plan",
                 "origin": "inferred", "evidence_refs": [points[SOURCE_FACTS[0]], points[SOURCE_FACTS[1]]]},
                {"from": "entity_growth", "relation": "is_a", "to": "concept_plan",
                 "origin": "inferred", "evidence_refs": [points[SOURCE_FACTS[0]], points[SOURCE_FACTS[1]]]},
            ],
        }
        plans = [
            {
                "type": "direct_fact",
                "entity_id": "entity_starter",
                "question": "According to the source, how many seats does the Starter plan include?",
                "expected_answer": "10 seats",
            },
            {
                "type": "concept_mapping",
                "concept_id": "concept_plan",
                "question": "According to the source, how many seats does a plan include?",
                "expected_answer": "Starter: 10 seats; Growth: 25 seats",
            },
            {
                "type": "controlled_invariance",
                "pair_id": "cv_customer_context_concept_plan",
                "source_concept": "concept_plan",
                "source_support": "group_unspecified",
                "answer_contract": "invariant",
                "varied_dimension": {
                    "id": "customer_context",
                    "label": "customer context",
                    "semantic_scope": "subscription context",
                    "mutual_exclusivity": self.dimension_exclusivity,
                    "coexists_with": [],
                },
                "control_value": "new customer",
                "question_template": "According to the source, how many seats does a plan include for a new customer?",
                "question": "According to the source, how many seats does a plan include for a new customer?",
                "expected_answer": "Starter: 10 seats; Growth: 25 seats",
            },
            {
                "type": "controlled_invariance",
                "pair_id": "cv_customer_context_concept_plan",
                "source_concept": "concept_plan",
                "source_support": "group_unspecified",
                "answer_contract": "invariant",
                "varied_dimension": {
                    "id": "customer_context",
                    "label": "customer context",
                    "semantic_scope": "subscription context",
                    "mutual_exclusivity": self.dimension_exclusivity,
                    "coexists_with": [],
                },
                "control_value": "returning customer",
                "question_template": "According to the source, how many seats does a plan include for a returning customer?",
                "question": "According to the source, how many seats does a plan include for a returning customer?",
                "expected_answer": "Starter: 10 seats; Growth: 25 seats",
            },
        ]
        if combined:
            self.tools["submit_controlled_variant_plan"](graph, plans[2:])
        else:
            self.tools["submit_semantic_graph"](graph)
            self.tools["submit_test_plans"](plans)
        return self.evidence()

    def submit_batch(self, request):
        source = request["source"]
        points = [{"id": str(index), "statement": fact, "topic": "Subscription plans",
                   "evidence": [{"source_id": source["source_id"], "quote": fact}], "answerability": "eligible"}
                  for index, fact in enumerate(SOURCE_FACTS)]
        questions = []
        if request["stage"] == "generation":
            questions = [{"record": {"knowledge_point_id": "0", "question": "How many seats does Starter include?",
                                     "expected_answer": "10 seats"},
                          "coverage_claims": [{"knowledge_point_id": "0", "covers_entire_point": True,
                                               "reason": "The answer gives the Starter seat count."}]}]
        self.tools["submit_dataset_batch"](points, questions, True)
        return self.evidence()

    def evidence(self):
        return {
            "loaded_skills": [self.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }


@pytest.mark.parametrize(("workflow_level", "interactive"), [(None, False), (None, True), (1, False), (2, False), (3, False)],
                         ids=["default-topics", "default-probes", "level-1", "level-2", "level-3"])
def test_create_dataset_materializes_a_generic_source_grounded_controlled_pair(
    tmp_path, monkeypatch, workflow_level, interactive,
):
    source = tmp_path / "plans.md"
    source.write_text("\n".join(SOURCE_FACTS), encoding="utf-8")
    skill = tmp_path / "generic-graph"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: generic-graph\ndescription: fixture\n---\nfixture", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    args = ["create", "test-dataset", "--knowledge", str(source), "--output", str(output),
            "--skill", str(skill), "--no-verbose"]
    if interactive:
        args.append("--controlled-variant-probes")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("lladar.cli.select_controlled_variant_topics",
                            lambda dimensions: select_controlled_variant_topics(dimensions, input_fn=lambda _: "1"))
    else:
        args.extend(["--controlled-variant-topics", "customer_context"])
    if workflow_level is not None:
        args.extend(["--workflow-level", str(workflow_level)])
    if workflow_level != 3:
        args.extend(["--count", "2"])

    def coverage_agent_factory(**options):
        if "submit_dataset_batch" in options["tools"]:
            return GenericGraphSkillAgent(**options)
        return SourceCoverageAgent(**options)

    agent_factory = GenericGraphSkillAgent
    if interactive:
        def interrupt_publication(*args, **kwargs):
            raise OSError("fixture publication interruption")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(GenerationResult, "publish", interrupt_publication)
            assert main(args, skill_agent_factory=agent_factory, coverage_agent_factory=coverage_agent_factory) != 0
        args.extend(["--resume", f"{output}.work"])
        monkeypatch.setattr("lladar.cli.select_controlled_variant_topics",
                            lambda _: pytest.fail("the saved selection should be reused"))

        def agent_factory(**_):
            pytest.fail("the completed generation and proposal should be reused")

    assert main(args, skill_agent_factory=agent_factory, coverage_agent_factory=coverage_agent_factory) == 0

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    graph = json.loads(Path(str(output) + ".graph.json").read_text(encoding="utf-8"))
    controlled = [line for line in sidecar["dataset"]["lines"] if line["plan_type"] == "controlled_invariance"]

    assert sidecar["schema_version"] == 5
    assert sidecar["workflow_level"] == (workflow_level or 1)
    plan_types = {line["plan_type"] for line in sidecar["dataset"]["lines"]}
    expected = {"controlled_invariance"}
    if workflow_level == 3:
        expected.update({"direct_fact", "concept_mapping"})
    else:
        assert sidecar["coverage"]["audit_status"] == ("sampled" if workflow_level == 2 else "generation_only")
        assert sum(item["stage"] == "controlled_variant_planning" for item in sidecar["executions"]) == 1
    assert plan_types == expected
    assert {node["id"] for node in graph["nodes"]} >= {"entity_starter", "entity_growth", "concept_plan"}
    assert len(controlled) == 2
    assert {line["pair_id"] for line in controlled} == {"cv_customer_context_concept_plan"}
    assert {line["control_value"] for line in controlled} == {"new customer", "returning customer"}
    assert all(line["origin"] == "synthetic_control" for line in controlled)
    assert all(line["answer_contract"] == "invariant" for line in controlled)
    assert all(set(record) == {"question", "expected_answer", "actual_response"} for record in records)


def test_create_dataset_rejects_a_controlled_pair_with_unknown_value_relationship(tmp_path):
    source = tmp_path / "plans.md"
    source.write_text("\n".join(SOURCE_FACTS), encoding="utf-8")
    skill = tmp_path / "generic-graph"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: generic-graph\ndescription: fixture\n---\nfixture", encoding="utf-8")

    class UnknownValueRelationshipAgent(GenericGraphSkillAgent):
        dimension_exclusivity = "unknown"

    with pytest.raises(DatasetValidationError, match="valid questions"):
        create_test_dataset(
            source,
            skill=skill,
            controlled_variant_topics=("customer_context",),
            skill_agent_factory=UnknownValueRelationshipAgent,
            verbose=False,
        )
