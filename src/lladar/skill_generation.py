"""Skill-directed dataset generation with optional evidence graphs."""

from __future__ import annotations

import re
import hashlib
import json
import os
import tempfile
from uuid import uuid4
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any

from llama_index.core.schema import Document, MetadataMode

from .exceptions import DatasetValidationError, KnowledgeLoadError, ProviderError
from .loaders import load_knowledge
from .records import validate_record
from .progress import ProgressReporter
from .question_types import GENERATION_SIDECAR_VERSION, record_fingerprint


_REQUEST_TO_TYPE = {
    "free": "free",
    "single-choice": "single_choice",
    "multiple-choice": "multiple_choice",
    "ranking": "ranking",
}
_TYPED_PROTOCOLS = {
    "single_choice": "one_option_id",
    "multiple_choice": "option_id_list",
    "ranking": "ordered_option_ids",
}

_CANDIDATE_CONTRACT = {
    "reads": "List of {read_id, source_id, start_char, end_char}; zero-based character ranges, end exclusive.",
    "knowledge_points": "List of {id, statement, topic, evidence: [{read_id, quote}]} with exact source quotes.",
    "qa": "List of submit_qa records referring to the declared point IDs; graph not required.",
    "graph": "Optional {nodes, edges, facts} with non-empty nodes and facts and point evidence_refs.",
    "plans": "Optional list of submit_test_plans records; requires graph.",
    "method_reason": "Brief method selection reason, not private reasoning.",
}


def normalize(value: str) -> str:
    return " ".join(value.split())


def _format_value(fact: dict[str, str]) -> str:
    return " ".join((fact["value"], fact["unit"])).strip()


def preflight_output(output: Path, *, force: bool) -> None:
    for path in (output, Path(str(output) + ".generation.json"), Path(str(output) + ".graph.json")):
        if path.is_symlink():
            raise ValueError(f"output cannot be a symlink: {path}")
        if path.exists():
            if not force:
                raise FileExistsError(f"output already exists: {path}")
            if not path.is_file():
                raise ValueError(f"output must be a file: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=output.parent):
        pass


@dataclass
class GenerationResult:
    records: list[dict]
    provenance: dict
    graph: dict | None = None

    def publish(self, output: Path, *, force: bool) -> None:
        for record in self.records:
            validate_record(record)
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.records)
        self.provenance["dataset"]["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        sidecar = json.dumps(self.provenance, ensure_ascii=False, indent=2) + "\n"
        preflight_output(output, force=force)
        targets = [output, Path(str(output) + ".generation.json")]
        payloads = [content, sidecar]
        if self.graph is not None:
            targets.append(Path(str(output) + ".graph.json"))
            payloads.append(json.dumps(self.graph, ensure_ascii=False, indent=2) + "\n")
        retired = [Path(str(output) + ".graph.json")] if force and self.graph is None else []
        transaction_targets = targets + retired
        staged, backups, published, reservations = {}, {}, [], []
        lock = output.with_name(f".{output.name}.generation.lock")
        with lock.open("x"):
            pass
        try:
            preflight_output(output, force=force)
            for target, text in zip(targets, payloads):
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                staged[target] = temporary
                with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            if not force:
                for target in targets:
                    with target.open("x", encoding="utf-8"):
                        pass
                    reservations.append(target)
            # Remove the old completion marker first. A crash cannot leave a
            # new dataset paired with an old successful provenance marker.
            for target in reversed(transaction_targets):
                if force and target.exists():
                    backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
                    os.replace(target, backup)
                    backups[target] = backup
            for target in targets:
                os.replace(staged[target], target)
                published.append(target)
        except BaseException:
            for target in reversed(published):
                target.unlink(missing_ok=True)
            for target in reservations:
                target.unlink(missing_ok=True)
            # Keep backups on disk if recovery itself fails.
            for target in transaction_targets:
                if target in backups:
                    os.replace(backups[target], target)
            raise
        else:
            for backup in backups.values():
                backup.unlink(missing_ok=True)
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)


class GenerationWorkspace:
    """Own source positions and candidates for tool or Python delivery."""

    def __init__(self, sources: list[tuple[Path, str]], page_chars: int = 12000,
                 question_type: str = "free"):
        self.sources = {
            f"source_{index:03d}": {"path": str(path.resolve()), "text": text}
            for index, (path, text) in enumerate(sources, 1)
        }
        # LlamaIndex is the source-document boundary for generation.
        # Keep the original text alongside it for deterministic
        # hashes and source-relative evidence offsets.
        self.documents = {
            source_id: Document(
                text=source["text"],
                metadata={"source_id": source_id, "path": source["path"]},
            )
            for source_id, source in self.sources.items()
        }
        self.page_chars = page_chars
        self.reads: dict[str, dict[str, Any]] = {}
        self.points: dict[str, dict[str, Any]] = {}
        self.point_identities: dict[tuple, str] = {}
        self.qa: dict[str, dict[str, Any]] = {}
        self.qa_contracts: dict[str, dict[str, Any]] = {}
        self.qa_points: dict[str, str] = {}
        self.question_type = question_type
        self.current_source: str | None = None
        self.current_point: str | None = None
        self.current_stage: str | None = None
        self.tool_epoch = 0
        self.pending_rejections = {source_id: 0 for source_id in self.sources}
        self.pending_rejections["generation"] = 0
        self.tool_lock = RLock()
        self.events: list[dict] = []
        self.stats = {"point_candidates": 0, "rejected_points": 0, "deduplicated_points": 0,
                      "qa_candidates": 0, "rejected_qa": 0, "repeated_qa_submissions": 0}
        self.semantic_graph: dict[str, Any] | None = None
        self.test_plans: list[dict[str, Any]] = []
        self.method_reason = ""

    def list_sources(self) -> list[dict[str, Any]]:
        return [{"source_id": key, "name": value["path"], "length": len(value["text"])}
                for key, value in self.sources.items()]

    def record_method(self, reason: str) -> dict:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("method reason must be a non-empty string")
        self.method_reason = reason.strip()
        return {"accepted": True}

    def coverage(self, source_id: str) -> dict:
        intervals = sorted((read["start_char"], read["end_char"])
                           for read in self.reads.values() if read["source_id"] == source_id)
        merged = []
        for start, end in intervals:
            if start == end:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        unread, cursor = [], 0
        for start, end in merged:
            if start > cursor:
                unread.append([cursor, start])
            cursor = end
        if cursor < len(self.sources[source_id]["text"]):
            unread.append([cursor, len(self.sources[source_id]["text"])])
        return {"read_ranges": merged, "unread_ranges": unread}

    def read_source(self, source_id: str, start_char: int = 0, end_char: int | None = None) -> dict:
        if source_id not in self.sources or (self.current_stage != "generation" and source_id != self.current_source):
            raise ValueError("source is not assigned to this extraction")
        text = self.documents[source_id].get_content(metadata_mode=MetadataMode.NONE)
        if type(start_char) is not int or not 0 <= start_char <= len(text):
            raise ValueError("invalid source start_char")
        if end_char is None:
            end_char = len(text)
        if type(end_char) is not int or not start_char <= end_char <= len(text):
            raise ValueError("invalid source end_char")
        end_char = min(end_char, start_char + self.page_chars)
        read_id = f"read_{len(self.reads) + 1:06d}"
        result = {"read_id": read_id, "source_id": source_id, "start_char": start_char,
                  "end_char": end_char, "text": text[start_char:end_char],
                  "next_start": end_char if end_char < len(text) else None}
        self.reads[read_id] = result
        return result.copy()

    def submit_knowledge_points(self, points: list[dict]) -> dict:
        if self.current_source is None and self.current_stage != "generation":
            raise ValueError("knowledge-point submission is not available in this stage")
        if not isinstance(points, list):
            raise ValueError("points must be a list")
        accepted, rejected = [], []
        for index, candidate in enumerate(points):
            self.stats["point_candidates"] += 1
            try:
                point = self.validate_point(candidate)
            except (ValueError, KeyError, TypeError) as error:
                self.stats["rejected_points"] += 1
                rejected.append({"index": index, "error": str(error)})
            else:
                positions = tuple(sorted({(item["source_id"], item["start_char"], item["end_char"], item["quote"])
                                          for item in point["evidence"]}))
                identity = (normalize(point["statement"]), positions)
                point_id = self.point_identities.get(identity)
                if point_id is None:
                    point_id = f"kp_{len(self.points) + 1:06d}"
                    self.points[point_id] = {"id": point_id, **point}
                    self.point_identities[identity] = point_id
                else:
                    self.stats["deduplicated_points"] += 1
                    for item in point["evidence"]:
                        if item not in self.points[point_id]["evidence"]:
                            self.points[point_id]["evidence"].append(item)
                accepted.append(point_id)
        rejection_key = self.current_source or "generation"
        self.pending_rejections[rejection_key] = max(
            0, self.pending_rejections[rejection_key] - len(accepted)) + len(rejected)
        return {"accepted": accepted, "rejected": rejected}

    def validate_point(self, candidate: dict) -> dict:
        if not isinstance(candidate, dict) or set(candidate) != {"statement", "topic", "evidence"}:
            raise ValueError("point requires exactly statement, topic, evidence")
        if any(not isinstance(candidate[key], str) or not candidate[key].strip() for key in ("statement", "topic")):
            raise ValueError("statement and topic must be non-empty strings")
        if not isinstance(candidate["evidence"], list) or not candidate["evidence"]:
            raise ValueError("point requires evidence")
        evidence = []
        for item in candidate["evidence"]:
            if not isinstance(item, dict) or set(item) != {"read_id", "quote"}:
                raise ValueError("evidence requires exactly read_id and quote")
            page = self.reads.get(item["read_id"])
            quote = item["quote"]
            if (page is None or (self.current_stage != "generation" and page["source_id"] != self.current_source)
                    or not isinstance(quote, str) or not quote.strip() or quote not in page["text"]):
                raise ValueError("evidence must match a read from the assigned source")
            offset = page["text"].find(quote)
            while offset != -1:
                start = offset + page["start_char"]
                evidence.append({"source_id": page["source_id"], "read_id": page["read_id"],
                                 "quote": quote, "start_char": start, "end_char": start + len(quote)})
                offset = page["text"].find(quote, offset + 1)
        return {"statement": candidate["statement"].strip(), "topic": candidate["topic"].strip(), "evidence": evidence}

    def read_knowledge_point(self, knowledge_point_id: str) -> dict:
        if knowledge_point_id not in self.points or (
            self.current_stage != "generation" and knowledge_point_id != self.current_point
        ):
            raise ValueError("knowledge point is not assigned to this QA stage")
        return deepcopy(self.points[knowledge_point_id])

    def list_knowledge_points(self) -> list[dict]:
        """Expose source-validated facts for a typed-question candidate set."""
        if self.current_stage not in {"qa", "semantic_graph", "generation"}:
            raise ValueError("knowledge points are not available in this stage")
        return [deepcopy(point) for point in self.points.values()]

    def submit_semantic_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        """Accept one evidence-backed, domain-independent graph candidate."""
        if self.current_stage not in {"semantic_graph", "generation"}:
            raise ValueError("semantic-graph submission is not available in this stage")
        if self.semantic_graph is not None:
            raise ValueError("semantic graph was already submitted")
        if not isinstance(graph, dict) or set(graph) != {"nodes", "edges", "facts"}:
            raise ValueError("semantic graph requires exactly nodes, edges, and facts")
        nodes, edges, facts = graph["nodes"], graph["edges"], graph["facts"]
        if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(facts, list):
            raise ValueError("semantic graph nodes, edges, and facts must be lists")
        if not nodes or not facts:
            raise ValueError("semantic graph requires non-empty nodes and facts")

        known_points = set(self.points)
        node_ids: set[str] = set()
        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("semantic graph node must be an object")
            node_id, node_type, label, origin = (node.get("id"), node.get("type"), node.get("label"), node.get("origin"))
            refs = node.get("evidence_refs")
            if (not isinstance(node_id, str) or not node_id or node_id in node_ids
                    or node_type not in {"entity", "attribute", "concept"}
                    or not isinstance(label, str) or not label.strip()
                    or origin not in {"source", "inferred"}
                    or not isinstance(refs, list) or not refs or any(ref not in known_points for ref in refs)):
                raise ValueError("semantic graph node has invalid identity or evidence")
            normalized = {"id": node_id, "type": node_type, "label": label.strip(), "origin": origin,
                          "evidence_refs": list(dict.fromkeys(refs))}
            if node_type == "concept":
                members = node.get("member_ids")
                if (not isinstance(members, list) or len(members) < 2
                        or any(not isinstance(member, str) for member in members)
                        or len(set(members)) != len(members)):
                    raise ValueError("concept requires distinct member IDs")
                normalized["member_ids"] = list(members)
            elif "member_ids" in node:
                raise ValueError("only concepts may declare member IDs")
            node_ids.add(node_id)
            normalized_nodes.append(normalized)
        nodes_by_id = {node["id"]: node for node in normalized_nodes}
        for node in normalized_nodes:
            if node["type"] == "concept" and any(
                member not in nodes_by_id or nodes_by_id[member]["type"] != "entity"
                for member in node["member_ids"]
            ):
                raise ValueError("concept members must be graph entities")

        normalized_facts: list[dict[str, str]] = []
        fact_entities: set[str] = set()
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {"entity_id", "label", "value", "unit", "evidence_ref"}:
                raise ValueError("semantic graph fact has an invalid schema")
            if (not all(isinstance(fact[key], str) and fact[key].strip()
                        for key in ("entity_id", "label", "value", "evidence_ref"))
                    or not isinstance(fact["unit"], str)
                    or fact["entity_id"] not in nodes_by_id
                    or nodes_by_id[fact["entity_id"]]["type"] != "entity"
                    or fact["label"] != nodes_by_id[fact["entity_id"]]["label"]
                    or fact["evidence_ref"] not in known_points
                    or fact["evidence_ref"] not in nodes_by_id[fact["entity_id"]]["evidence_refs"]):
                raise ValueError("semantic graph fact must reference an evidenced entity: entity_id must name an entity node; label must exactly match the entity node label; evidence_ref must be a known point ID in that node's evidence_refs")
            if fact["entity_id"] in fact_entities:
                raise ValueError("semantic graph accepts one comparable fact per entity")
            fact_entities.add(fact["entity_id"])
            normalized_facts.append({key: fact[key].strip() for key in fact})

        normalized_edges: list[dict[str, Any]] = []
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "relation", "to", "origin", "evidence_refs"}:
                raise ValueError("semantic graph edge has an invalid schema")
            refs = edge["evidence_refs"]
            if (edge["from"] not in nodes_by_id or edge["to"] not in nodes_by_id
                    or not isinstance(edge["relation"], str) or not edge["relation"].strip()
                    or edge["origin"] not in {"source", "inferred"}
                    or not isinstance(refs, list) or not refs or any(ref not in known_points for ref in refs)):
                raise ValueError("semantic graph edge must be evidenced and reference graph nodes")
            normalized_edges.append({"from": edge["from"], "relation": edge["relation"].strip(), "to": edge["to"],
                                     "origin": edge["origin"], "evidence_refs": list(dict.fromkeys(refs))})
        self.semantic_graph = {"schema_version": 1, "nodes": normalized_nodes, "edges": normalized_edges,
                               "facts": normalized_facts,
                               "evidence": [
                                   {"ref": point["id"], **evidence}
                                   for point in self.points.values() for evidence in point["evidence"]
                               ]}
        return {"accepted": True, "nodes": len(normalized_nodes), "facts": len(normalized_facts)}

    def read_semantic_graph(self) -> dict[str, Any]:
        if self.current_stage not in {"test_plans", "generation"} or self.semantic_graph is None:
            raise ValueError("verified semantic graph is not available in this stage")
        return deepcopy(self.semantic_graph)

    def _concept_candidates(self, concept_id: str) -> tuple[dict[str, Any], list[dict[str, str]], str]:
        if self.semantic_graph is None:
            raise ValueError("semantic graph is required before planning")
        nodes = {node["id"]: node for node in self.semantic_graph["nodes"]}
        concept = nodes.get(concept_id)
        if not isinstance(concept, dict) or concept.get("type") != "concept":
            raise ValueError("plan must reference a verified concept")
        facts = {fact["entity_id"]: fact for fact in self.semantic_graph["facts"]}
        members = concept["member_ids"]
        if any(member not in facts for member in members):
            raise ValueError("every concept member needs one comparable source fact")
        candidates = [{key: facts[member][key] for key in ("entity_id", "label", "value", "unit", "evidence_ref")}
                      for member in members]
        if len({(candidate["value"], candidate["unit"]) for candidate in candidates}) < 2:
            raise ValueError("concept candidates need distinct source values")
        expected = "; ".join(f"{item['label']}: {_format_value(item)}" for item in candidates)
        return concept, candidates, expected

    def submit_test_plans(self, plans: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate model-proposed plans without letting them alter graph facts."""
        if self.current_stage not in {"test_plans", "generation"} or self.semantic_graph is None:
            raise ValueError("test-plan submission is not available in this stage")
        if self.test_plans:
            raise ValueError("test plans were already submitted")
        if not isinstance(plans, list) or not plans:
            raise ValueError("test plans must be a non-empty list")
        normalized: list[dict[str, Any]] = []
        for plan in plans:
            if not isinstance(plan, dict) or plan.get("type") not in {
                "direct_fact", "concept_mapping", "controlled_invariance",
            }:
                raise ValueError("test plan type is invalid")
            question, submitted_answer = plan.get("question"), plan.get("expected_answer")
            if not isinstance(question, str) or not question.strip() or not isinstance(submitted_answer, str):
                raise ValueError("test plan requires a natural-language question and expected answer")
            if plan["type"] == "direct_fact":
                if set(plan) != {"type", "entity_id", "question", "expected_answer"}:
                    raise ValueError("direct-fact plan has an invalid schema")
                facts = {fact["entity_id"]: fact for fact in self.semantic_graph["facts"]}
                entity_id = plan["entity_id"]
                if not isinstance(entity_id, str) or entity_id not in facts:
                    raise ValueError("direct-fact plan must reference a verified fact entity")
                fact = facts[entity_id]
                expected = _format_value(fact)
                if submitted_answer.strip() != expected:
                    raise ValueError("direct-fact expected answer must equal the source fact")
                normalized.append({
                    "type": "direct_fact", "plan_id": f"df_{entity_id}", "entity_id": entity_id,
                    "question": question.strip(), "expected_answer": expected,
                    "evidence_refs": [fact["evidence_ref"]],
                })
                continue
            concept_id = plan.get("concept_id", plan.get("source_concept"))
            concept, candidates, expected = self._concept_candidates(concept_id)
            if submitted_answer.strip() != expected:
                raise ValueError("test plan expected answer must equal the complete source candidate set")
            base = {
                "type": plan["type"], "concept_id": concept_id, "concept_label": concept["label"],
                "concept_origin": concept["origin"], "member_ids": list(concept["member_ids"]),
                "candidates": candidates, "question": question.strip(), "expected_answer": expected,
            }
            if plan["type"] == "concept_mapping":
                if set(plan) != {"type", "concept_id", "question", "expected_answer"}:
                    raise ValueError("concept plan has an invalid schema")
                base["plan_id"] = f"cp_{concept_id}"
            else:
                required = {"type", "pair_id", "source_concept", "source_support", "answer_contract",
                            "varied_dimension", "control_value", "question_template", "question", "expected_answer"}
                if set(plan) != required:
                    raise ValueError("controlled-invariance plan has an invalid schema")
                pair_id, dimension, control_value, template = (
                    plan["pair_id"], plan["varied_dimension"], plan["control_value"], plan["question_template"])
                if (not isinstance(pair_id, str) or not pair_id
                        or plan["source_support"] != "group_unspecified"
                        or plan["answer_contract"] != "invariant"
                        or not isinstance(control_value, str) or not control_value.strip()
                        or not isinstance(template, str) or template.strip() != question.strip()
                        or control_value.casefold() not in question.casefold()
                        or not isinstance(dimension, dict)
                        or set(dimension) != {"id", "label", "semantic_scope", "mutual_exclusivity", "coexists_with"}
                        or not all(isinstance(dimension[key], str) and dimension[key].strip()
                                   for key in ("id", "label", "semantic_scope", "mutual_exclusivity"))
                        or dimension["mutual_exclusivity"] == "unknown"
                        or not isinstance(dimension["coexists_with"], list)
                        or any(not isinstance(value, str) or not value for value in dimension["coexists_with"])):
                    raise ValueError("controlled-invariance plan has invalid dimension metadata")
                base.update({
                    "plan_id": f"{pair_id}_{len(normalized) + 1:03d}", "pair_id": pair_id,
                    "origin": "synthetic_control", "source_concept": concept_id,
                    "source_support": plan["source_support"], "answer_contract": plan["answer_contract"],
                    "varied_dimension": dimension["id"], "control_dimension": deepcopy(dimension),
                    "control_value": control_value.strip(), "question_template": template.strip(),
                })
            normalized.append(base)
        pair_groups: dict[str, list[dict[str, Any]]] = {}
        for plan in normalized:
            if plan["type"] == "controlled_invariance":
                pair_groups.setdefault(plan["pair_id"], []).append(plan)
        for pair in pair_groups.values():
            if len(pair) < 2:
                raise ValueError("controlled-invariance pairs need at least two variants")
            first = pair[0]
            controls = [plan["control_value"] for plan in pair]
            if len(set(controls)) != len(controls):
                raise ValueError("controlled-invariance pair values must be distinct")
            static = (first["source_concept"], first["answer_contract"], first["control_dimension"],
                      first["expected_answer"])
            skeletons = []
            for plan in pair:
                if (plan["source_concept"], plan["answer_contract"], plan["control_dimension"],
                        plan["expected_answer"]) != static:
                    raise ValueError("controlled-invariance pair must retain one source contract and dimension")
                skeletons.append(plan["question"].casefold().replace(plan["control_value"].casefold(), "{control_value}"))
            if len(set(skeletons)) != 1:
                raise ValueError("controlled-invariance pair must change only its control value")
        self.test_plans = normalized
        return {"accepted": len(normalized), "pairs": len(pair_groups)}

    def submit_qa(self, record: dict) -> dict:
        row, contract, point_id = self.validate_qa(record)
        self.read_knowledge_point(point_id)
        if self.current_stage == "generation":
            for qa_id, previous in self.qa.items():
                if previous == row and self.qa_contracts[qa_id] == contract and self.qa_points.get(qa_id) == point_id:
                    self.stats["repeated_qa_submissions"] += 1
                    return {"accepted": True, "duplicate": True}
            qa_id = f"qa_{len(self.qa) + 1:06d}"
            self.qa[qa_id], self.qa_contracts[qa_id], self.qa_points[qa_id] = row, contract, point_id
            return {"accepted": True}
        if point_id in self.qa:
            if self.qa[point_id] == row and self.qa_contracts[point_id] == contract:
                self.stats["repeated_qa_submissions"] += 1
                return {"accepted": True, "duplicate": True}
            raise ValueError("this knowledge point already has an accepted QA")
        self.qa[point_id] = row
        self.qa_contracts[point_id] = contract
        return {"accepted": True}

    def validate_qa(self, record: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
        base_fields = {"knowledge_point_id", "question", "expected_answer"}
        if not isinstance(record, dict):
            raise ValueError("QA requires a JSON object")
        if set(record) != base_fields and record.get("question_type") in {None, "free"}:
            raise ValueError("Free QA requires exactly knowledge_point_id, question, expected_answer; remove extra fields")
        if set(record) == base_fields:
            point_id = record["knowledge_point_id"]
            row = validate_record({"question": record["question"], "expected_answer": record["expected_answer"],
                                   "actual_response": None})
            contract = {"question_type": "free", "answer_protocol": "natural_language"}
        else:
            row, contract, point_id = self.validate_typed_qa(record)
        expected_type = _REQUEST_TO_TYPE.get(self.question_type)
        if self.question_type != "auto" and contract["question_type"] != expected_type:
            raise ValueError(f"QA type must be {expected_type} for --question-type {self.question_type}")
        return row, contract, point_id

    def validate_typed_qa(self, record: dict) -> tuple[dict[str, Any], dict[str, Any], str]:
        common = {"knowledge_point_id", "knowledge_point_ids", "question", "expected_answer",
                  "question_type", "answer_protocol", "options", "correct_option_ids"}
        question_type = record.get("question_type")
        if question_type == "ranking":
            expected_fields = common | {"ranking_axis", "direction"}
        else:
            expected_fields = common
        if set(record) != expected_fields or question_type not in _TYPED_PROTOCOLS:
            raise ValueError("typed QA has an invalid schema")
        point_id = record["knowledge_point_id"]
        point_ids = record["knowledge_point_ids"]
        if (not isinstance(point_ids, list) or not point_ids or len(set(point_ids)) != len(point_ids)
                or any(not isinstance(value, str) or value not in self.points for value in point_ids)
                or point_id not in point_ids):
            raise ValueError("typed QA must reference known distinct knowledge points")
        topics = {self.points[value]["topic"] for value in point_ids}
        if len(topics) != 1:
            raise ValueError("typed QA options must share one source topic")
        options = record["options"]
        if not isinstance(options, list):
            raise ValueError("typed QA options must be a list")
        option_ids: list[str] = []
        option_points: list[str] = []
        for option in options:
            if not isinstance(option, dict) or set(option) != {"id", "text", "knowledge_point_id"}:
                raise ValueError("typed QA option has an invalid schema")
            option_id, text, option_point = option["id"], option["text"], option["knowledge_point_id"]
            if (not isinstance(option_id, str) or re.fullmatch(r"[A-Z]", option_id) is None
                    or not isinstance(text, str) or not text.strip() or option_point not in self.points
                    or text != self.points[option_point]["statement"]):
                raise ValueError("typed QA option must be a source-grounded labeled statement")
            option_ids.append(option_id)
            option_points.append(option_point)
        if len(set(option_ids)) != len(option_ids) or set(option_points) != set(point_ids) or len(option_points) != len(point_ids):
            raise ValueError("typed QA options must map once to every referenced knowledge point")
        if question_type == "single_choice" and not 2 <= len(options) <= 5:
            raise ValueError("single-choice QA requires 2 to 5 options")
        if question_type == "multiple_choice" and not 3 <= len(options) <= 6:
            raise ValueError("multiple-choice QA requires 3 to 6 options")
        if question_type == "ranking" and not 3 <= len(options) <= 5:
            raise ValueError("ranking QA requires 3 to 5 options")
        correct = record["correct_option_ids"]
        if (not isinstance(correct, list) or not correct or len(set(correct)) != len(correct)
                or any(value not in option_ids for value in correct)):
            raise ValueError("typed QA must declare distinct known correct option IDs")
        if question_type == "single_choice" and len(correct) != 1:
            raise ValueError("single-choice QA requires exactly one correct option")
        if question_type == "multiple_choice" and not 2 <= len(correct) < len(options):
            raise ValueError("multiple-choice QA requires multiple but not all correct options")
        if question_type == "ranking" and set(correct) != set(option_ids):
            raise ValueError("ranking QA must order every option exactly once")
        expected = (
            correct[0] if question_type == "single_choice"
            else ",".join(correct) if question_type == "multiple_choice"
            else ">".join(correct)
        )
        row = validate_record({"question": record["question"], "expected_answer": record["expected_answer"],
                               "actual_response": None})
        if row["expected_answer"] != expected:
            raise ValueError("typed QA expected_answer does not match the response protocol")
        for option in options:
            if f"{option['id']}. {option['text']}" not in row["question"]:
                raise ValueError("typed QA question must display every option")
        if record["answer_protocol"] != _TYPED_PROTOCOLS[question_type]:
            raise ValueError("typed QA answer protocol does not match question type")
        contract = {
            "question_type": question_type,
            "answer_protocol": record["answer_protocol"],
            "knowledge_point_ids": list(point_ids),
            "options": deepcopy(options),
            "correct_option_ids": list(correct),
        }
        if question_type == "ranking":
            if (not isinstance(record["ranking_axis"], str) or not record["ranking_axis"].strip()
                    or record["direction"] not in {"ascending", "descending"}):
                raise ValueError("ranking QA requires an axis and direction")
            contract.update(ranking_axis=record["ranking_axis"].strip(), direction=record["direction"])
        return row, contract, point_id

    def tools(self, stage: str) -> dict[str, Callable]:
        self.tool_epoch += 1
        epoch = self.tool_epoch
        def scoped(function):
            @wraps(function)
            def invoke(*args, **kwargs):
                with self.tool_lock:
                    if epoch != self.tool_epoch:
                        raise ValueError("tool capability expired with its work item")
                    name = function.__name__
                    if name == "submit_qa":
                        self.stats["qa_candidates"] += 1
                    try:
                        result = function(*args, **kwargs)
                    except (ValueError, KeyError, TypeError, DatasetValidationError) as error:
                        if name == "submit_qa":
                            self.stats["rejected_qa"] += 1
                        self.events.append({"tool": name, "error_type": type(error).__name__})
                        raise
                    self.events.append({"tool": name, "result": deepcopy(result)})
                    return result
            return invoke
        names_by_stage = {
            "generation": ("list_sources", "read_source", "submit_knowledge_points", "read_knowledge_point",
                           "list_knowledge_points", "submit_qa", "submit_semantic_graph", "read_semantic_graph",
                           "submit_test_plans", "record_method"),
            "knowledge_points": ("list_sources", "read_source", "submit_knowledge_points"),
            "qa": ("read_knowledge_point", "list_knowledge_points", "submit_qa"),
            "semantic_graph": ("list_knowledge_points", "submit_semantic_graph"),
            "test_plans": ("read_semantic_graph", "submit_test_plans"),
        }
        if stage not in names_by_stage:
            raise ValueError(f"unknown generation stage: {stage}")
        names = names_by_stage[stage]
        return {name: scoped(getattr(self, name)) for name in names}


def _plan_submission(plan: dict) -> dict:
    """Recover the input contract from a previously normalized plan."""
    if "plan_id" not in plan:
        return deepcopy(plan)
    if plan["type"] == "direct_fact":
        keys = ("type", "entity_id", "question", "expected_answer")
    elif plan["type"] == "concept_mapping":
        keys = ("type", "concept_id", "question", "expected_answer")
    else:
        keys = ("type", "pair_id", "source_concept", "source_support", "answer_contract",
                "control_value", "question_template", "question", "expected_answer")
    result = {key: deepcopy(plan[key]) for key in keys}
    if plan["type"] == "controlled_invariance":
        result["varied_dimension"] = deepcopy(plan["control_dimension"])
    return result


def _collect_candidates(workspace: GenerationWorkspace, sources, candidate_path: Path) -> GenerationWorkspace:
    """Freeze and revalidate either a declared file or the Python-accessible workspace."""
    with workspace.tool_lock:
        if candidate_path.exists():
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict) or set(candidate) - {
                "reads", "knowledge_points", "qa", "graph", "plans", "method_reason",
            }:
                raise ValueError("candidate file has an invalid schema")
        else:
            qa = []
            for qa_id, row in workspace.qa.items():
                point_id = workspace.qa_points.get(qa_id, qa_id)
                contract = workspace.qa_contracts[qa_id]
                record = {"knowledge_point_id": point_id, "question": row["question"],
                          "expected_answer": row["expected_answer"]}
                if contract["question_type"] != "free":
                    record.update(deepcopy(contract))
                qa.append(record)
            candidate = deepcopy({
                "reads": list(workspace.reads.values()), "knowledge_points": list(workspace.points.values()),
                "qa": qa, "graph": workspace.semantic_graph,
                "plans": workspace.test_plans, "method_reason": workspace.method_reason,
            })
    checked = GenerationWorkspace(sources, page_chars=workspace.page_chars, question_type=workspace.question_type)
    checked.current_stage = "generation"
    for read in candidate.get("reads", []):
        source_id, read_id = read["source_id"], read["read_id"]
        start, end = read["start_char"], read["end_char"]
        if source_id not in checked.sources or not isinstance(read_id, str) or not read_id or read_id in checked.reads:
            raise ValueError("candidate reads require unique IDs and declared sources")
        text = checked.sources[source_id]["text"]
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ValueError("candidate read range is outside its source")
        if "text" in read and read["text"] != text[start:end]:
            raise ValueError("candidate read text no longer matches the loaded source")
        checked.reads[read_id] = {"read_id": read_id, "source_id": source_id,
                                 "start_char": start, "end_char": end, "text": text[start:end],
                                 "next_start": end if end < len(text) else None}
    for point in candidate.get("knowledge_points", []):
        point_id = point["id"]
        if not isinstance(point_id, str) or not point_id or point_id in checked.points:
            raise ValueError("candidate knowledge points require unique IDs")
        raw = {key: point[key] for key in ("statement", "topic")}
        raw["evidence"] = [{key: item[key] for key in ("read_id", "quote")} for item in point["evidence"]]
        normalized = checked.validate_point(raw)
        # Canonical workspace evidence must retain its exact source positions.
        for evidence in point["evidence"]:
            if "source_id" in evidence and evidence not in normalized["evidence"]:
                raise ValueError("candidate evidence no longer matches its source positions")
        checked.points[point_id] = {"id": point_id, **normalized}
    graph = candidate.get("graph")
    if graph is not None:
        checked.submit_semantic_graph({key: graph[key] for key in ("nodes", "edges", "facts")})
    plans = candidate.get("plans", [])
    if plans:
        checked.submit_test_plans([_plan_submission(plan) for plan in plans])
        if checked.question_type not in {"free", "auto"} and any(
            plan["type"] == "direct_fact" for plan in checked.test_plans
        ):
            raise ValueError("use typed QA for direct facts with the requested --question-type")
    for record in candidate.get("qa", []):
        checked.submit_qa(record)
    reason = candidate.get("method_reason", "")
    if not isinstance(reason, str):
        raise ValueError("method_reason must be a string")
    checked.method_reason = reason
    if not checked.qa and not checked.test_plans:
        raise ValueError("no valid question records were generated")
    if any(checked.coverage(key)["unread_ranges"] for key in checked.sources):
        raise ValueError("sources have unread ranges; finish reading before completion")
    # Historical tool rejections remain in the audit log, but a corrected final
    # snapshot may be delivered by Python rather than clearing tool counters.
    checked.events = deepcopy(workspace.events)
    checked.stats = deepcopy(workspace.stats)
    return checked


def generate_with_skill(knowledge, *, skill: Path, agent_factory: Callable | None,
                        count: int, question_type: str, controlled_variant_topics: tuple[str, ...],
                        controlled_variant_selector: Callable[[tuple[dict[str, Any], ...]], Any] | None,
                        seed: int, model: str, env_file: str | Path,
                        temperature: float, profile, verbose: bool) -> GenerationResult:
    skill = skill.resolve()
    if not (skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill is missing SKILL.md: {skill}")
    from akasha.agent.skills.loader import load_skill_directory
    from yaml import YAMLError
    try:
        selected = load_skill_directory(skill)
    except YAMLError as error:
        raise ValueError("SKILL.md contains invalid YAML frontmatter") from error
    if not selected.instructions.strip():
        raise ValueError("SKILL.md must contain method instructions")
    sources = load_knowledge(knowledge)
    if not sources:
        raise KnowledgeLoadError("no supported knowledge files were found")
    workspace = GenerationWorkspace(
        sources,
        page_chars=min(12000, max(1, profile.max_input_tokens // 4)),
        question_type=question_type,
    )
    reporter = ProgressReporter(verbose)
    reporter.configuration({"skill": skill, "model": model, "sources": len(sources), "count": count,
                            "question_type": question_type, "seed": seed})
    skill_files = {"SKILL.md": hashlib.sha256((skill / "SKILL.md").read_bytes()).hexdigest()}
    executions = []
    if agent_factory is None:
        from .skill_agent import AkashaSkillAgent
        agent_factory = AkashaSkillAgent

    def execute(request: dict) -> dict:
        nonlocal workspace
        errors = []
        for attempt in range(1, 4):
            event_start = len(workspace.events)
            reporter.emit(request["stage"].upper(),
                          f"item={request.get('source_id', request.get('knowledge_point_id'))} attempt={attempt}/3")
            # Initialization errors are fatal, unlike one model work item failing.
            workspace.current_stage = request["stage"]
            agent = agent_factory(skills=[str(skill)], tools=workspace.tools(request["stage"]),
                                  model=model, env_file=str(env_file), temperature=temperature,
                                  max_input_tokens=profile.max_input_tokens,
                                  max_output_tokens=profile.max_output_tokens, verbose=verbose)
            work = {**request, "attempt": attempt}
            work["existing_results"] = {
                "graph_present": workspace.semantic_graph is not None, "qa_count": len(workspace.qa),
                "plan_count": len(workspace.test_plans),
                "source_coverage": {key: workspace.coverage(key) for key in workspace.sources},
            }
            try:
                result = agent(work)
            except (RuntimeError, ProviderError, ValueError) as error:
                # Avoid persisting raw provider errors, which may contain secrets.
                evidence = getattr(error, "skill_evidence", {})
                for name, digest in evidence.get("skill_files", {}).items():
                    if name in skill_files and skill_files[name] != digest:
                        raise DatasetValidationError(f"skill file changed during generation: {name}")
                    skill_files[name] = digest
                errors.append({"attempt": attempt, "error_type": type(error).__name__})
                reporter.emit("RETRY", f"stage={request['stage']} attempt={attempt}/3 error={type(error).__name__}")
                executions.append({**request, "attempt": attempt, **evidence, "error_type": type(error).__name__,
                                   "host_tool_events": workspace.events[event_start:]})
                if skill.name in evidence.get("loaded_skills", []):
                    try:
                        workspace = _collect_candidates(workspace, sources, Path(request["candidate_path"]))
                    except (ValueError, KeyError, TypeError, OSError) as validation_error:
                        request["validation_errors"] = [str(validation_error)]
                    else:
                        reporter.emit("RECOVERED", "final candidates passed validation after agent error")
                        return {"status": "complete", "attempts": attempt, "errors": errors}
                continue
            if skill.name not in result.get("loaded_skills", []):
                raise DatasetValidationError("agent did not load the selected skill")
            for name, digest in result.get("skill_files", {}).items():
                if name in skill_files and skill_files[name] != digest:
                    raise DatasetValidationError(f"skill file changed during generation: {name}")
                skill_files[name] = digest
            executions.append({**request, "attempt": attempt, **result,
                               "host_tool_events": workspace.events[event_start:]})
            try:
                workspace = _collect_candidates(workspace, sources, Path(request["candidate_path"]))
            except (ValueError, KeyError, TypeError, OSError) as error:
                errors.append({"attempt": attempt, "error_type": "InvalidCandidates"})
                request["validation_errors"] = [str(error)]
                reporter.emit("RETRY", f"stage={request['stage']} attempt={attempt}/3 error=InvalidCandidates")
            else:
                return {"status": "complete", "attempts": attempt, "errors": errors}
        return {"status": "failed", "attempts": 3, "errors": errors,
                "validation_errors": request.get("validation_errors", [])}

    with tempfile.TemporaryDirectory(prefix="lladar-generation-") as candidate_dir:
        generation_work = execute(
            {"stage": "generation", "question_type": question_type, "count": count,
             "sources": workspace.list_sources(), "candidate_path": str(Path(candidate_dir) / "candidates.json"),
             "candidate_contract": _CANDIDATE_CONTRACT,
             "controlled_variant_topics": list(controlled_variant_topics),
             "controlled_variants_requested": bool(controlled_variant_topics) or controlled_variant_selector is not None},
        )
    if generation_work["status"] != "complete":
        details = "; ".join(generation_work.get("validation_errors", []))
        raise DatasetValidationError("dataset generation did not produce complete valid questions" +
                                     (f": {details}" if details else ""))
    for source in workspace.sources.values():
        source["work"] = generation_work
    records = []
    lines = []
    seen_qa = {}
    skipped = []
    graph = workspace.semantic_graph
    if graph is not None:
        graph["corpus_sha256"] = hashlib.sha256(
            "".join(source["text"] for source in workspace.sources.values()).encode("utf-8")
        ).hexdigest()
    probe_plans = workspace.test_plans
    dimensions = tuple({plan["varied_dimension"]: plan["control_dimension"]
                        for plan in probe_plans if plan["type"] == "controlled_invariance"}.values())
    if (controlled_variant_topics or controlled_variant_selector is not None) and not dimensions:
        raise DatasetValidationError("controlled variant request requires a verified graph and controlled test plans")
    if controlled_variant_topics and controlled_variant_selector is not None:
        raise ValueError("controlled_variant_topics and controlled_variant_selector cannot be combined")
    selected_topics = (
        tuple(controlled_variant_selector(dimensions)) if controlled_variant_selector is not None
        else controlled_variant_topics
    )
    known_topics = {dimension["id"] for dimension in dimensions}
    unknown_topics = set(selected_topics) - known_topics
    if unknown_topics:
        raise ValueError(f"unknown controlled-variant topic: {', '.join(sorted(unknown_topics))}")
    probe_plans = [
        plan for plan in probe_plans
        if plan["type"] != "controlled_invariance" or plan["varied_dimension"] in selected_topics
    ]
    if graph is not None:
        graph["control_dimensions"] = list(dimensions)
    for qa_id, row in workspace.qa.items():
        point_id = workspace.qa_points[qa_id]
        if count and len(records) >= count:
            break
        identity = (normalize(row["question"]), normalize(row["expected_answer"]))
        if identity in seen_qa:
            continue
        seen_qa[identity] = len(records)
        records.append(row)
        contract = workspace.qa_contracts[qa_id]
        lines.append({"line": len(records), "qa_id": qa_id,
                      "knowledge_point_ids": contract.get("knowledge_point_ids", [point_id]),
                      "record_fingerprint": record_fingerprint(row), "generation_method": "direct_qa",
                      "plan_type": "direct_qa", **contract})
    for plan in probe_plans:
        pair_id = plan.get("pair_id")
        if pair_id is not None:
            pair = [item for item in probe_plans if item.get("pair_id") == pair_id]
            if plan is not pair[0]:
                continue
            if count and len(records) + len(pair) > count:
                skipped.append({"pair_id": pair_id, "reason": "count_limit"})
                continue
            identities = [(normalize(item["question"]), normalize(item["expected_answer"])) for item in pair]
            if len(set(identities)) != len(identities) or any(identity in seen_qa for identity in identities):
                skipped.append({"pair_id": pair_id, "reason": "duplicate_question"})
                continue
            selected_plans = pair
        else:
            if count and len(records) >= count:
                continue
            selected_plans = [plan]
        for selected in selected_plans:
            row = validate_record({"question": selected["question"], "expected_answer": selected["expected_answer"],
                                   "actual_response": None})
            identity = (normalize(row["question"]), normalize(row["expected_answer"]))
            if identity in seen_qa:
                continue
            seen_qa[identity] = len(records)
            records.append(row)
            lines.append({"line": len(records), "qa_id": selected["plan_id"], "qa_ids": [selected["plan_id"]],
                          "knowledge_point_ids": [item["evidence_ref"] for item in selected.get("candidates", [])]
                          or list(selected["evidence_refs"]),
                          "record_fingerprint": record_fingerprint(row), "question_type": "free",
                          "generation_method": "graph",
                          "answer_protocol": "natural_language", "plan_type": selected["type"],
                          **{key: value for key, value in selected.items() if key not in {
                              "question", "expected_answer", "type", "plan_id"}}})
    if not records:
        raise DatasetValidationError("no valid question records were generated")
    reporter.done(len(records))
    return GenerationResult(records, {
        "schema_version": GENERATION_SIDECAR_VERSION, "question_type": question_type, "status": "partial" if any(
            point.get("status") == "failed" for point in workspace.points.values()) or any(
            source["work"]["status"] == "failed" for source in workspace.sources.values()) else "complete",
        "skill": {"name": skill.name, "path": str(skill), "files": skill_files},
        "validation": {"status": "passed", "scope": "final_candidate_snapshot",
                       "checks": ["source_positions", "question_contracts", "graph_and_probe_contracts"],
                       "limitations": "Source quotes and structural contracts do not prove natural-language entailment."},
        "method_reason": workspace.method_reason,
        "skipped": skipped,
        "settings": {"model": model, "temperature": temperature, "count": count,
                     "question_type": question_type, "controlled_variant_topics": list(selected_topics), "seed": seed,
                     "max_attempts": 3, "read_page_chars": workspace.page_chars,
                     "max_input_tokens": profile.max_input_tokens, "max_output_tokens": profile.max_output_tokens},
        "sources": [{"id": key, "path": source["path"], **source["work"], **workspace.coverage(key),
                     "unresolved_rejections": workspace.pending_rejections[key],
                     "sha256": hashlib.sha256(source["text"].encode("utf-8")).hexdigest()}
                    for key, source in workspace.sources.items()],
        "knowledge_points": list(workspace.points.values()),
        "qa": [{"id": key, "knowledge_point_id": workspace.qa_points[key], **value,
                **workspace.qa_contracts[key]}
               for key, value in workspace.qa.items()],
        "reads": list(workspace.reads.values()), "executions": executions,
        "dataset": {"lines": lines},
        "stats": {**workspace.stats,
                  "failed_sources": sum(s["work"]["status"] == "failed" for s in workspace.sources.values()),
                  "failed_points": sum(p.get("status") == "failed" for p in workspace.points.values()),
                  "valid_qa_points": len(workspace.qa), "accepted_points": len(workspace.points),
                  "attempted_points": sum(p.get("attempts", 0) > 0 for p in workspace.points.values()),
                  "output_records": len(records), "output_points": sum(len(line["knowledge_point_ids"]) for line in lines),
                  "deduplicated_qa": max(0, len(workspace.qa) + len(probe_plans) - len(records)),
                  "not_attempted_count_limit": sum(p.get("status") == "not_attempted_count_limit"
                                                   for p in workspace.points.values())},
    }, graph)
