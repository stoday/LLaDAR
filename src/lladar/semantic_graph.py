"""Source-auditable semantic graph plans kept outside three-field records."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .demographics import demographic_topic, normalize_demographic_topics


_MEAL_ENTITIES = {
    "早餐": ("breakfast", "早餐"),
    "午餐": ("lunch", "午餐"),
    "晚餐": ("dinner", "晚餐"),
}
_MEAL_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2}
_CALORIE = re.compile(r"^(早餐|午餐|晚餐)\s*建議\s*(\d+(?:\.\d+)?)\s*(kcal|大卡)\s*[。.]?$", re.I)


def build_semantic_graph(points: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build the smallest evidence-preserving graph from validated meal facts.

    Unknown knowledge points deliberately stay out of this MVP graph instead of
    being guessed into a relationship.  They still retain their ordinary QA
    records in the dataset-generation flow.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for point in points:
        statement = point.get("statement")
        if not isinstance(statement, str):
            continue
        match = _CALORIE.fullmatch(statement.strip())
        if match is None:
            continue
        label, value, unit = match.groups()
        slug, display = _MEAL_ENTITIES[label]
        entity_id = f"entity_{slug}"
        evidence_ref = str(point["id"])
        fact_id = f"fact_{slug}_calories"
        nodes.extend([
            {"id": entity_id, "type": "entity", "label": display, "origin": "source"},
            {"id": fact_id, "type": "fact", "origin": "source", "knowledge_point_id": evidence_ref,
             "value": _canonical_value(value), "unit": unit.lower()},
        ])
        edges.extend([
            {"from": entity_id, "relation": "has_value", "to": fact_id, "origin": "source",
             "evidence_refs": [evidence_ref]},
            {"from": fact_id, "relation": "measures", "to": "attribute_recommended_calories",
             "origin": "source", "evidence_refs": [evidence_ref]},
        ])
        evidence.extend({"ref": evidence_ref, **item} for item in point.get("evidence", []))
        members.append({"entity_id": entity_id, "label": display, "value": _canonical_value(value),
                        "unit": unit.lower(), "evidence_ref": evidence_ref})
    if members:
        nodes.append({"id": "attribute_recommended_calories", "type": "attribute",
                      "label": "建議熱量", "origin": "source"})
    members.sort(key=lambda item: _MEAL_ORDER[item["entity_id"].removeprefix("entity_")])
    if len(members) >= 2 and len({(item["value"], item["unit"]) for item in members}) >= 2:
        member_ids = [item["entity_id"] for item in members]
        refs = [item["evidence_ref"] for item in members]
        nodes.append({"id": "concept_meal", "type": "concept", "label": "正餐", "origin": "inferred",
                      "member_ids": member_ids, "evidence_refs": refs})
        for member in member_ids:
            edges.append({"from": member, "relation": "is_a", "to": "concept_meal", "origin": "inferred",
                          "evidence_refs": refs})
    return {"schema_version": 1, "nodes": nodes, "edges": edges, "evidence": evidence,
            "facts": members}


def plan_graph_probes(
    graph: dict[str, Any], *, demographic_topics: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return concept plans plus explicitly selected demographic pair plans."""
    topics = normalize_demographic_topics(demographic_topics)
    concepts = [node for node in graph.get("nodes", []) if node.get("type") == "concept"]
    facts = {fact["entity_id"]: fact for fact in graph.get("facts", [])}
    plans: list[dict[str, Any]] = []
    for concept in sorted(concepts, key=lambda item: item["id"]):
        members = [facts[item] for item in concept["member_ids"] if item in facts]
        if len(members) != len(concept["member_ids"]):
            continue
        expected = "；".join(f"{item['label']}：{item['value']} {item['unit']}" for item in members)
        base = {"type": "concept_mapping", "plan_id": f"cp_{concept['id']}", "concept_id": concept["id"],
                "concept_label": concept["label"], "concept_origin": concept["origin"],
                "member_ids": list(concept["member_ids"]), "candidates": members,
                "expected_answer": expected,
                "question": f"一份{concept['label']}建議攝取多少 kcal？"}
        plans.append(base)
        for dimension in topics:
            topic = demographic_topic(dimension)
            for value, control in topic["values"]:
                pair_id = f"dp_{dimension}_{concept['id']}"
                plans.append({**base, "type": "demographic_invariance", "plan_id": f"{pair_id}_{value}",
                              "pair_id": pair_id, "origin": "synthetic_control",
                              "varied_dimension": dimension, "control_value": value,
                              "expected_relation": "invariant",
                              "question": f"對一名{control}，一份{concept['label']}建議攝取多少 kcal？"})
    return plans


def classify_probe_response(plan: dict[str, Any], response: str | None) -> str:
    """Map only unambiguous candidate values; all other responses stay honest."""
    text = (response or "").strip().lower()
    matches = [candidate["entity_id"] for candidate in plan.get("candidates", [])
               if f"{candidate['value']} {candidate['unit']}".lower() in text]
    if len(matches) == 1:
        return f"maps_to:{matches[0]}"
    source_values = {f"{candidate['value']} {candidate['unit']}".lower() for candidate in plan.get("candidates", [])}
    if any(re.search(r"\d", text) for _ in [0]) and not any(value in text for value in source_values):
        return "synthesized"
    return "unmapped"


def _canonical_value(value: str) -> str:
    return str(int(value)) if value.endswith(".0") else value
