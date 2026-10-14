"""Skill-directed dataset generation with optional evidence graphs."""

from __future__ import annotations

from .language import tr, language_prompt, current_language

import re
import hashlib
import json
import os
import tempfile
from uuid import uuid4
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
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


def preflight_output(output: Path, *, force: bool, extra_paths=()) -> None:
    for path in (output, Path(str(output) + ".generation.json"), Path(str(output) + ".graph.json"), *extra_paths):
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
    artifacts: dict[Path, str] = field(default_factory=dict)

    def publish(self, output: Path, *, force: bool) -> None:
        for record in self.records:
            validate_record(record)
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.records)
        self.provenance["dataset"]["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.provenance["session"] = publication_metadata()
        sidecar = json.dumps(self.provenance, ensure_ascii=False, indent=2) + "\n"
        preflight_output(output, force=force, extra_paths=self.artifacts)
        targets = [output]
        payloads = [content]
        if self.graph is not None:
            targets.append(Path(str(output) + ".graph.json"))
            payloads.append(json.dumps(self.graph, ensure_ascii=False, indent=2) + "\n")
        targets.extend(self.artifacts)
        payloads.extend(self.artifacts.values())
        targets.append(Path(str(output) + ".generation.json"))
        payloads.append(sidecar)
        retired = [Path(str(output) + ".graph.json")] if force and self.graph is None else []
        publish_bundle(dict(zip(targets, payloads)), force=force, result=self.records, retired=retired)

from .work_session import current_session, publish_bundle, publication_metadata


class GenerationWorkspace:
    """Own source positions and candidates for tool or Python delivery."""

    def __init__(self, sources: list[tuple[Path, str]], page_chars: int = 12000,
                 question_type: str = "free", reporter: ProgressReporter | None = None, restore: bool = True):
        self.reporter = reporter
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
        self.qa_sequence = 0
        self.qa_contracts: dict[str, dict[str, Any]] = {}
        self.qa_points: dict[str, str] = {}
        self.source_refs: dict[str, dict] = {}
        self.question_submissions: dict[str, dict] = {}
        self.answer_bases: dict[str, dict] = {}
        self.question_drafts: dict[str, dict] = {}
        self.question_requests: dict[str, dict] = {}
        self.assigned_ranges = {}
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
        session = current_session()
        if restore and session and "generation_workspace" in session.data["state"]:
            for key, value in session.data["state"]["generation_workspace"].items():
                setattr(self, key, deepcopy(value))

    def checkpoint(self):
        session = current_session()
        if session:
            keys = ("reads", "points", "qa", "qa_sequence", "qa_contracts", "qa_points", "source_refs",
                    "question_submissions", "answer_bases", "question_drafts", "question_requests",
                    "pending_rejections", "events", "stats", "semantic_graph", "test_plans", "method_reason", "assigned_ranges")
            session.put("generation_workspace", {key: deepcopy(getattr(self, key)) for key in keys})

    def list_sources(self) -> list[dict[str, Any]]:
        return [{"source_id": key, "name": value["path"], "length": len(value["text"]),
                 "assigned_start": self.assigned_ranges.get(key, [0, len(value["text"])])[0],
                 "assigned_end": self.assigned_ranges.get(key, [0, len(value["text"])])[1]}
                for key, value in self.sources.items() if not self.assigned_ranges or key in self.assigned_ranges]

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
        assigned_start, assigned_end = self.assigned_ranges.get(source_id, [0, len(text)])
        if self.assigned_ranges and source_id not in self.assigned_ranges:
            raise ValueError("source is not assigned to this generation batch")
        if start_char == 0 and assigned_start:
            start_char = assigned_start
        if not assigned_start <= start_char <= assigned_end:
            raise ValueError("source read is outside this batch; use the assigned absolute range")
        if type(start_char) is not int or not 0 <= start_char <= len(text):
            raise ValueError("invalid source start_char")
        if end_char is None:
            end_char = assigned_end
        end_char = min(end_char, assigned_end)
        if type(end_char) is not int or not start_char <= end_char <= len(text):
            raise ValueError("invalid source end_char")
        end_char = min(end_char, start_char + self.page_chars)
        read_id = f"read_{len(self.reads) + 1:06d}"
        result = {"read_id": read_id, "source_id": source_id, "start_char": start_char,
                  "end_char": end_char, "text": text[start_char:end_char],
                  "next_start": end_char if end_char < assigned_end else None}
        self.reads[read_id] = result
        if self.reporter is not None:
            completed = sum(end - start for key in self.sources
                            for start, end in self.coverage(key)["read_ranges"])
            total = sum(len(document.get_content(metadata_mode=MetadataMode.NONE))
                        for document in self.documents.values())
            self.reporter.group(completed, total, tr('Read sources'))
        identity = f"{source_id}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}:{start_char}:{end_char}"
        ref_id = "ref_" + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        self.source_refs[ref_id] = {"read_id": read_id, "source_id": source_id,
                                   "start_char": start_char, "end_char": end_char,
                                   "quote": text[start_char:end_char]}
        return {**result, "source_refs": [{"ref_id": ref_id, "source_id": source_id,
                                          "start_char": start_char, "end_char": end_char}]}

    def submit_question(self, request_id: str, question: str, expected_answer: str,
                        source_refs: list[str], answer_basis: str, rationale: str) -> dict:
        """Submit a question and its answer evidence together, without file delivery."""
        payload = dict(question=question, expected_answer=expected_answer, source_refs=source_refs,
                       answer_basis=answer_basis, rationale=rationale)
        if not isinstance(request_id, str) or not request_id.strip():
            return {"status": "needs_revision", "issues": [{"field": "request_id", "code": "required",
                     "message": "Provide a non-empty request ID."}]}
        previous = self.question_requests.get(request_id)
        if previous is not None:
            return deepcopy(previous["result"]) if previous["payload"] == payload else {
                "status": "conflict", "item_id": previous["result"]["item_id"],
                "issues": [{"field": "request_id", "code": "content_conflict",
                            "message": "This request ID already has different content."}]}
        item_id = "question_" + uuid4().hex
        self.question_drafts[item_id] = {"item_id": item_id, "revision": 0}
        return self._apply_question(item_id, request_id, payload)

    def get_question(self, item_id: str) -> dict:
        """Query a draft's current revision without resubmitting its content."""
        if item_id not in self.question_drafts:
            return {"status": "conflict", "item_id": item_id,
                    "issues": [{"field": "item_id", "code": "unknown_item", "message": "Unknown question ID."}]}
        return deepcopy(self.question_drafts[item_id])

    def get_question_request(self, request_id: str) -> dict:
        if not isinstance(request_id, str) or request_id not in self.question_requests:
            return {"status": "conflict", "issues": [{"field": "request_id", "code": "unknown_request",
                    "message": "No saved submission for this request", "repair_action": "submit_request"}]}
        return deepcopy(self.question_requests[request_id]["result"])

    def revise_question(self, item_id: str, request_id: str, revision: int,
                        question: str, expected_answer: str, source_refs: list[str],
                        answer_basis: str, rationale: str) -> dict:
        """Revise one draft using its current ID and revision."""
        if not isinstance(request_id, str) or not request_id.strip():
            return {"status": "needs_revision", "issues": [{"field": "request_id", "code": "required",
                     "message": "Provide a non-empty request ID."}]}
        payload = dict(question=question, expected_answer=expected_answer, source_refs=source_refs,
                       answer_basis=answer_basis, rationale=rationale)
        identity = {"item_id": item_id, "revision": revision, **payload}
        previous = self.question_requests.get(request_id)
        if previous is not None:
            return deepcopy(previous["result"]) if previous["payload"] == identity else {
                "status": "conflict", "item_id": item_id,
                "issues": [{"field": "request_id", "code": "content_conflict", "message": "Request content differs."}]}
        draft = self.question_drafts.get(item_id)
        if draft is None or type(revision) is not int or revision != draft["revision"]:
            return {"status": "conflict", "item_id": item_id,
                    "issues": [{"field": "revision", "code": "revision_conflict", "message": "Query the current draft revision."}]}
        if draft["revision"] >= 4 and draft["status"] == "accepted":
            return {"status": "conflict", "item_id": item_id, "revision": draft["revision"],
                    "issues": [{"field": "revision", "code": "repair_limit", "message": "Three revisions are already saved",
                                "repair_action": "keep_accepted_version"}]}
        if draft["status"] == "rejected":
            return {"status": "rejected", "item_id": item_id, "revision": draft["revision"],
                    "issues": deepcopy(draft["issues"])}
        result = self._apply_question(item_id, request_id, payload)
        self.question_requests[request_id]["payload"] = identity
        return result

    def _apply_question(self, item_id: str, request_id: str, payload: dict) -> dict:
        draft = self.question_drafts[item_id]
        previous_qa = draft.get("qa_id")
        result = self._submit_question_payload(request_id, **payload)
        draft.update(payload=deepcopy(payload), revision=draft["revision"] + 1,
                     status=result["status"], issues=deepcopy(result.get("issues", [])))
        if draft["status"] == "needs_revision" and draft["revision"] >= 4:
            draft["status"] = "rejected"
            result["status"] = "rejected"
        if result["status"] == "accepted":
            qa_id = result["item_id"]
            if previous_qa and previous_qa != qa_id:
                self.qa.pop(previous_qa, None)
                self.qa_points.pop(previous_qa, None)
                self.qa_contracts.pop(previous_qa, None)
                self.answer_bases.pop(previous_qa, None)
            draft["qa_id"] = qa_id
            self.answer_bases[qa_id]["question_id"] = item_id
        result = {**result, "item_id": item_id, "revision": draft["revision"]}
        self.question_requests[request_id] = {"payload": deepcopy(payload), "result": deepcopy(result)}
        return result

    def original_answer_mapping(self, expected_answer, source_refs):
        matches = []
        by_source = {}
        for ref in source_refs:
            reference = self.source_refs[ref] if isinstance(ref, str) else ref
            by_source.setdefault(reference["source_id"], []).append(reference)
        for source_id, references in by_source.items():
            cursor = 0
            for line in self.sources[source_id]["text"].splitlines(keepends=True):
                start, end = cursor, cursor + len(line)
                cursor = end
                if not any(ref["start_char"] < end and ref["end_char"] > start for ref in references):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                    continue
                for field, value in row["values"].items():
                    if isinstance(value, str) and value == expected_answer:
                        matches.append({"source_id": source_id, "data_row": row["data_row"], "field": field,
                                        "row_start_char": start, "row_end_char": end})
        return matches

    def _submit_question_payload(self, request_id: str, question: str, expected_answer: str,
                                 source_refs: list[str], answer_basis: str, rationale: str) -> dict:
        payload = dict(question=question, expected_answer=expected_answer, source_refs=source_refs,
                       answer_basis=answer_basis, rationale=rationale)
        if not isinstance(request_id, str) or not request_id.strip():
            return {"status": "needs_revision", "issues": [{"field": "request_id", "code": "required",
                     "message": "Provide a non-empty request ID."}]}
        previous = self.question_submissions.get(request_id)
        if previous is not None:
            return deepcopy(previous["result"]) if previous["payload"] == payload else {
                "status": "conflict", "item_id": previous["result"]["item_id"],
                "issues": [{"field": "request_id", "code": "content_conflict",
                            "message": "This request ID already has different content."}]}
        for field, value in (("question", question), ("expected_answer", expected_answer), ("rationale", rationale)):
            if not isinstance(value, str) or not value.strip():
                return {"status": "needs_revision", "issues": [{"field": field, "code": "required",
                         "message": f"Provide non-empty {field}."}]}
        if answer_basis not in {"source_answer", "source_annotation", "derived_criterion"}:
            return {"status": "needs_revision", "issues": [{"field": "answer_basis", "code": "unknown_basis",
                     "message": "Use source_answer, source_annotation or derived_criterion."}]}
        if not isinstance(source_refs, list) or not source_refs or any(
            not isinstance(ref, str) or ref not in self.source_refs for ref in source_refs
        ):
            return {"status": "needs_revision", "issues": [{"field": "source_refs", "code": "unknown_source",
                     "message": "Use reference IDs returned by read_source."}]}
        original_mapping = self.original_answer_mapping(expected_answer, source_refs) if answer_basis == "source_answer" else []
        if answer_basis == "source_answer" and not original_mapping:
            return {"status": "needs_revision", "issues": [{"field": "expected_answer", "code": "original_answer_mismatch",
                "message": "source_answer must exactly preserve a saved original answer field; provide its answer text or use an explicitly transformed/derived basis.",
                "repair_action": "replace_answer_or_basis"}]}
        point = self.validate_point({"statement": rationale, "topic": "Question evidence", "evidence": [
            {"read_id": self.source_refs[ref]["read_id"], "quote": self.source_refs[ref]["quote"]}
            for ref in dict.fromkeys(source_refs)]})
        point_id = f"kp_{len(self.points) + 1:06d}"
        record = {"knowledge_point_id": point_id, "question": question, "expected_answer": expected_answer}
        self.points[point_id] = {"id": point_id, **point}
        try:
            self.validate_qa(record)
        except (ValueError, DatasetValidationError) as error:
            self.points.pop(point_id)
            return {"status": "needs_revision", "issues": [{"field": "question", "code": "invalid_question",
                     "message": str(error)}]}
        self.submit_qa(record)
        qa_id = next(key for key, row in self.qa.items() if row["question"] == question
                     and row["expected_answer"] == expected_answer and self.qa_points[key] == point_id)
        self.answer_bases[qa_id] = {"answer_basis": answer_basis, "basis_rationale": rationale,
                                    "basis_point_ids": [point_id], "source_refs": list(source_refs),
                                    "source_answer_mapping": original_mapping}
        self.stats["qa_candidates"] += 1
        result = {"status": "accepted", "item_id": qa_id, "revision": 1, "issues": []}
        self.question_submissions[request_id] = {"payload": deepcopy(payload), "result": result}
        return deepcopy(result)

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
        if self.reporter is not None:
            for plan in normalized:
                self.reporter.record(plan, context=f"candidate plan={plan['plan_id']}")
        return {"accepted": len(normalized), "pairs": len(pair_groups)}

    def submit_qa(self, record: dict) -> dict:
        row, contract, point_id = self.validate_qa(record)
        self.read_knowledge_point(point_id)
        if self.current_stage == "generation":
            for qa_id, previous in self.qa.items():
                if previous == row and self.qa_contracts[qa_id] == contract and self.qa_points.get(qa_id) == point_id:
                    self.stats["repeated_qa_submissions"] += 1
                    return {"accepted": True, "duplicate": True}
            self.qa_sequence += 1
            qa_id = f"qa_{self.qa_sequence:06d}"
            self.qa[qa_id], self.qa_contracts[qa_id], self.qa_points[qa_id] = row, contract, point_id
            if self.reporter is not None:
                self.reporter.record(row, context=f"candidate qa={qa_id}")
            return {"accepted": True}
        if point_id in self.qa:
            if self.qa[point_id] == row and self.qa_contracts[point_id] == contract:
                self.stats["repeated_qa_submissions"] += 1
                return {"accepted": True, "duplicate": True}
            raise ValueError("this knowledge point already has an accepted QA")
        self.qa[point_id] = row
        self.qa_contracts[point_id] = contract
        if self.reporter is not None:
            self.reporter.record(row, context=f"candidate point={point_id}")
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
                    self.checkpoint()
                    return result
            return invoke
        names_by_stage = {
            "generation": ("list_sources", "read_source", "submit_knowledge_points", "read_knowledge_point",
                           "list_knowledge_points", "submit_qa", "submit_question", "revise_question", "get_question", "get_question_request",
                           "submit_semantic_graph", "read_semantic_graph",
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
    checked = GenerationWorkspace(sources, page_chars=workspace.page_chars, question_type=workspace.question_type, restore=False)
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
    required = workspace.assigned_ranges or {key: [0, len(source["text"])] for key, source in checked.sources.items()}
    for key, (start, end) in required.items():
        if any(unread_start < end and unread_end > start for unread_start, unread_end in checked.coverage(key)["unread_ranges"]):
            raise ValueError("sources have unread ranges; finish reading this assigned batch before completion")
    checked.assigned_ranges = deepcopy(workspace.assigned_ranges)
    # Historical tool rejections remain in the audit log, but a corrected final
    # snapshot may be delivered by Python rather than clearing tool counters.
    checked.events = deepcopy(workspace.events)
    checked.stats = deepcopy(workspace.stats)
    checked.source_refs = deepcopy(workspace.source_refs)
    checked.question_submissions = deepcopy(workspace.question_submissions)
    checked.question_drafts = deepcopy(workspace.question_drafts)
    checked.question_requests = deepcopy(workspace.question_requests)
    identities = {}
    sequence = workspace.qa_sequence
    for qa_id, row in checked.qa.items():
        match = next((key for key, original in workspace.qa.items() if row == original
                      and checked.qa_points[qa_id] == workspace.qa_points[key]), None)
        if match is None:
            sequence += 1
            match = f"qa_{sequence:06d}"
        identities[qa_id] = match
    for attribute in ("qa", "qa_points", "qa_contracts"):
        values = getattr(checked, attribute)
        setattr(checked, attribute, {identities[key]: value for key, value in values.items()})
    checked.qa_sequence = sequence
    for qa_id in checked.qa:
        if qa_id in workspace.answer_bases:
            checked.answer_bases[qa_id] = deepcopy(workspace.answer_bases[qa_id])
    return checked


def generate_with_skill(knowledge, *, skill: Path, agent_factory: Callable | None,
                        count: int, question_type: str, controlled_variant_topics: tuple[str, ...],
                        controlled_variant_selector: Callable[[tuple[dict[str, Any], ...]], Any] | None,
                        seed: int, model: str, env_file: str | Path,
                        temperature: float, profile, verbose: bool,
                        extra_tools_factory: Callable | None = None, request_context: dict | None = None,
                        candidate_validator: Callable | None = None,
                        runtime_options: dict | None = None, allow_candidate_files: bool = True) -> GenerationResult:
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
    reporter = ProgressReporter(verbose)
    workspace = GenerationWorkspace(
        sources,
        page_chars=min(12000, max(1, profile.max_input_tokens // 4)),
        question_type=question_type,
        reporter=reporter,
    )
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
                          tr('item={p0} attempt={p1}/3', p0=request.get('source_id', request.get('knowledge_point_id')), p1=attempt))
            # Initialization errors are fatal, unlike one model work item failing.
            workspace.current_stage = request["stage"]
            stage_tools = workspace.tools(request["stage"])
            if extra_tools_factory is not None:
                stage_tools.update(extra_tools_factory(workspace))
            from .skill_agent import SYSTEM_PROMPT
            runtime = dict(runtime_options or {})
            runtime["system_prompt"] = language_prompt(runtime.get("system_prompt", SYSTEM_PROMPT))
            agent = agent_factory(skills=[str(skill)], tools=stage_tools,
                                  model=model, env_file=str(env_file), temperature=temperature,
                                  max_input_tokens=profile.max_input_tokens,
                                  max_output_tokens=profile.max_output_tokens, verbose=verbose, **runtime)
            work = {**request, "attempt": attempt}
            work["existing_results"] = {
                "graph_present": workspace.semantic_graph is not None, "qa_count": len(workspace.qa),
                "questions": list(workspace.qa.values()),
                "plan_count": len(workspace.test_plans),
                "source_coverage": {key: workspace.coverage(key) for key in workspace.sources},
            }
            if not allow_candidate_files:
                work.pop("candidate_path", None)
                work.pop("candidate_contract", None)
                work["delivery"] = "Use host submission tools; no file delivery is required."
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
                reporter.emit("RETRY", tr('stage={p0} attempt={p1}/3 error={p2}', p0=request['stage'], p1=attempt, p2=type(error).__name__))
                executions.append({**request, "attempt": attempt, **evidence, "error_type": type(error).__name__,
                                   "host_tool_events": workspace.events[event_start:]})
                if skill.name in evidence.get("loaded_skills", []):
                    try:
                        workspace = _collect_candidates(workspace, sources, Path(request["candidate_path"]) if allow_candidate_files else Path(candidate_dir) / "host-only-snapshot")
                        if candidate_validator is not None:
                            candidate_validator(workspace)
                    except (ValueError, KeyError, TypeError, OSError) as validation_error:
                        request["validation_errors"] = [str(validation_error)]
                    else:
                        reporter.emit("RECOVERED", tr('final candidates passed validation after agent error'))
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
                workspace = _collect_candidates(workspace, sources, Path(request["candidate_path"]) if allow_candidate_files else Path(candidate_dir) / "host-only-snapshot")
                if candidate_validator is not None:
                    candidate_validator(workspace)
            except (ValueError, KeyError, TypeError, OSError) as error:
                errors.append({"attempt": attempt, "error_type": "InvalidCandidates"})
                request["validation_errors"] = [str(error)]
                reporter.emit("RETRY", tr('stage={p0} attempt={p1}/3 error=InvalidCandidates', p0=request['stage'], p1=attempt))
            else:
                return {"status": "complete", "attempts": attempt, "errors": errors}
        return {"status": "failed", "attempts": 3, "errors": errors,
                "validation_errors": request.get("validation_errors", [])}

    with tempfile.TemporaryDirectory(prefix="lladar-generation-") as candidate_dir:
        session = current_session()
        generation_work = session.data["state"].get("generation_work") if session else None
        if generation_work is None:
            batch_chars = max(1, profile.max_input_tokens // 4)
            total_chars = sum(len(source["text"]) for source in workspace.sources.values())
            assignments = []
            if allow_candidate_files or total_chars <= batch_chars:
                assignments = [{key: [0, len(source["text"])] for key, source in workspace.sources.items()}]
            else:
                for key, source in workspace.sources.items():
                    assignments.extend({key: [start, min(len(source["text"]), start + batch_chars)]}
                        for start in range(0, len(source["text"]), batch_chars))
            completed_batches = dict(session.data["state"].get("generation_batches", {})) if session else {}
            errors = []
            for batch_index, assigned in enumerate(assignments, 1):
                batch_id = str(batch_index)
                if batch_id in completed_batches:
                    continue
                workspace.assigned_ranges = assigned
                generation_work = execute({**(request_context or {}), "stage": "generation",
                    "question_type": question_type, "count": count, "batch": batch_index,
                    "batch_count": len(assignments), "sources": workspace.list_sources(),
                    "candidate_path": str(Path(candidate_dir) / "candidates.json"),
                    "candidate_contract": _CANDIDATE_CONTRACT,
                    "controlled_variant_topics": list(controlled_variant_topics),
                    "controlled_variants_requested": bool(controlled_variant_topics) or controlled_variant_selector is not None})
                errors.extend(generation_work["errors"])
                workspace.checkpoint()
                if generation_work["status"] != "complete":
                    break
                completed_batches[batch_id] = generation_work
                if session:
                    session.put("generation_batches", completed_batches)
                reporter.group(batch_index, len(assignments), tr('Generated batches'))
            else:
                generation_work = {"status": "complete", "attempts": sum(item["attempts"] for item in completed_batches.values()),
                                   "errors": errors, "batches": len(assignments)}
                workspace.assigned_ranges = {}
    workspace.checkpoint()
    if session and generation_work["status"] == "complete":
        session.put("generation_work", generation_work)
    if generation_work["status"] != "complete":
        details = "; ".join(generation_work.get("validation_errors", []))
        raise DatasetValidationError("dataset generation did not produce complete valid questions" +
                                     (f": {details}" if details else ""))
    for source in workspace.sources.values():
        source["work"] = generation_work
    records = []
    lines = []
    seen_qa = {}
    skipped = [{"item_id": draft["item_id"], "reason": "question_repair_exhausted"
                if draft["status"] == "rejected" else "question_unresolved",
                "issues": deepcopy(draft["issues"])} for draft in workspace.question_drafts.values()
               if draft["status"] != "accepted"]
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
    count_excluded = 0
    unresolved_qa = {draft.get("qa_id") for draft in workspace.question_drafts.values() if draft["status"] != "accepted"}
    active_qa = [(key, row) for key, row in workspace.qa.items() if key not in unresolved_qa]
    for qa_index, (qa_id, row) in enumerate(active_qa):
        point_id = workspace.qa_points[qa_id]
        if count and len(records) >= count:
            count_excluded += len(active_qa) - qa_index
            break
        identity = (normalize(row["question"]), normalize(row["expected_answer"]))
        if identity in seen_qa:
            continue
        seen_qa[identity] = len(records)
        records.append(row)
        reporter.record(row, context=f"generated record={len(records)}")
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
                count_excluded += len(pair)
                continue
            identities = [(normalize(item["question"]), normalize(item["expected_answer"])) for item in pair]
            if len(set(identities)) != len(identities) or any(identity in seen_qa for identity in identities):
                skipped.append({"pair_id": pair_id, "reason": "duplicate_question"})
                continue
            selected_plans = pair
        else:
            if count and len(records) >= count:
                count_excluded += 1
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
            reporter.record(row, context=f"generated record={len(records)}")
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
        "schema_version": GENERATION_SIDECAR_VERSION, "question_type": question_type, "status": "partial" if skipped or any(
            point.get("status") == "failed" for point in workspace.points.values()) or any(
            source["work"]["status"] == "failed" for source in workspace.sources.values()) else "complete",
        "skill": {"name": skill.name, "path": str(skill), "files": skill_files},
        "validation": {"status": "passed", "scope": "final_candidate_snapshot",
                       "checks": ["source_positions", "question_contracts", "graph_and_probe_contracts"],
                       "limitations": tr("Source quotes and structural contracts do not prove natural-language entailment.")},
        "method_reason": workspace.method_reason,
        "skipped": skipped,
        "settings": {**current_language().metadata(), "model": model, "temperature": temperature, "count": count,
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
                  "valid_candidates_before_selection": len(active_qa) + len(probe_plans),
                  "count_limit_excluded": count_excluded,
                  "deduplicated_qa": max(0, len(workspace.qa) + len(probe_plans) - len(records) - count_excluded),
                  "not_attempted_count_limit": sum(p.get("status") == "not_attempted_count_limit"
                                                   for p in workspace.points.values())},
    }, graph)
