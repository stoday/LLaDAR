"""Public dataset-generation behavior for source-grounded controlled variants."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from lladar.api import create_test_dataset
from lladar.exceptions import DatasetValidationError


SOURCE_FACTS = (
    "Starter plan includes 10 seats.",
    "Growth plan includes 25 seats.",
)


class GenericGraphSkillAgent:
    """A system-boundary fake for the two LLM stages exposed to the generator."""

    def __init__(self, *, skills, tools, **_options):
        self.skill = Path(skills[0])
        self.name = self.skill.name
        self.tools = tools

    dimension_exclusivity = "declared"

    def __call__(self, request):
        if request["stage"] == "generation":
            page = self.tools["read_source"]("source_001")
            self.tools["submit_knowledge_points"]([
                {
                    "statement": fact,
                    "topic": "Subscription plans",
                    "evidence": [{"read_id": page["read_id"], "quote": fact}],
                }
                for fact in SOURCE_FACTS
            ])
        if request["stage"] == "generation":
            points = {point["statement"]: point["id"] for point in self.tools["list_knowledge_points"]()}
            self.tools["submit_semantic_graph"]({
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
            })
        if request["stage"] == "generation":
            self.tools["submit_test_plans"]([
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
            ])
        else:
            raise AssertionError(f"unexpected stage: {request['stage']}")
        return {
            "loaded_skills": [self.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }


def test_create_dataset_materializes_a_generic_source_grounded_controlled_pair(tmp_path):
    source = tmp_path / "plans.md"
    source.write_text("\n".join(SOURCE_FACTS), encoding="utf-8")
    skill = tmp_path / "generic-graph"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: generic-graph\ndescription: fixture\n---\nfixture", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    records = create_test_dataset(
        source,
        output=output,
        skill=skill,
        controlled_variant_topics=("customer_context",),
        skill_agent_factory=GenericGraphSkillAgent,
        verbose=False,
    )

    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    graph = json.loads(Path(str(output) + ".graph.json").read_text(encoding="utf-8"))
    controlled = [line for line in sidecar["dataset"]["lines"] if line["plan_type"] == "controlled_invariance"]

    assert sidecar["schema_version"] == 5
    assert {line["plan_type"] for line in sidecar["dataset"]["lines"]} == {
        "direct_fact", "concept_mapping", "controlled_invariance",
    }
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
