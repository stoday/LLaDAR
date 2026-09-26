"""Two-stage dataset generation through a local skill and scoped tools."""

from __future__ import annotations

import random
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

from .exceptions import DatasetValidationError, KnowledgeLoadError, ProviderError
from .loaders import load_knowledge
from .records import validate_record
from .progress import ProgressReporter


def normalize(value: str) -> str:
    return " ".join(value.split())


def preflight_output(output: Path, *, force: bool) -> None:
    for path in (output, Path(str(output) + ".generation.json")):
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

    def publish(self, output: Path, *, force: bool) -> None:
        for record in self.records:
            validate_record(record)
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.records)
        self.provenance["dataset"]["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        sidecar = json.dumps(self.provenance, ensure_ascii=False, indent=2) + "\n"
        preflight_output(output, force=force)
        targets = [output, Path(str(output) + ".generation.json")]
        staged, backups, published, reservations = {}, {}, [], []
        lock = output.with_name(f".{output.name}.generation.lock")
        with lock.open("x"):
            pass
        try:
            preflight_output(output, force=force)
            for target, text in zip(targets, (content, sidecar)):
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
            for target in reversed(targets):
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
            for target in targets:
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
    """Own source positions and accepted candidates; agents submit through tools."""

    def __init__(self, sources: list[tuple[Path, str]], page_chars: int = 12000):
        self.sources = {
            f"source_{index:03d}": {"path": str(path.resolve()), "text": text}
            for index, (path, text) in enumerate(sources, 1)
        }
        self.page_chars = page_chars
        self.reads: dict[str, dict[str, Any]] = {}
        self.points: dict[str, dict[str, Any]] = {}
        self.point_identities: dict[tuple, str] = {}
        self.qa: dict[str, dict[str, Any]] = {}
        self.current_source: str | None = None
        self.current_point: str | None = None
        self.tool_epoch = 0
        self.pending_rejections = {source_id: 0 for source_id in self.sources}
        self.tool_lock = RLock()
        self.events: list[dict] = []
        self.stats = {"point_candidates": 0, "rejected_points": 0, "deduplicated_points": 0,
                      "qa_candidates": 0, "rejected_qa": 0, "repeated_qa_submissions": 0}

    def list_sources(self) -> list[dict[str, Any]]:
        return [{"source_id": key, "name": value["path"], "length": len(value["text"])}
                for key, value in self.sources.items()]

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
        if source_id != self.current_source or source_id not in self.sources:
            raise ValueError("source is not assigned to this extraction")
        text = self.sources[source_id]["text"]
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
        if self.current_source is None:
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
        self.pending_rejections[self.current_source] = max(
            0, self.pending_rejections[self.current_source] - len(accepted)) + len(rejected)
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
            if (page is None or page["source_id"] != self.current_source
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
        if knowledge_point_id != self.current_point or knowledge_point_id not in self.points:
            raise ValueError("knowledge point is not assigned to this QA stage")
        return deepcopy(self.points[knowledge_point_id])

    def submit_qa(self, record: dict) -> dict:
        if not isinstance(record, dict) or set(record) != {"knowledge_point_id", "question", "expected_answer"}:
            raise ValueError("QA requires exactly knowledge_point_id, question, expected_answer")
        point_id = record["knowledge_point_id"]
        self.read_knowledge_point(point_id)
        row = validate_record({"question": record["question"], "expected_answer": record["expected_answer"],
                               "actual_response": None})
        if point_id in self.qa:
            if self.qa[point_id] == row:
                self.stats["repeated_qa_submissions"] += 1
                return {"accepted": True, "duplicate": True}
            raise ValueError("this knowledge point already has an accepted QA")
        self.qa[point_id] = row
        return {"accepted": True}

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
        names = ("list_sources", "read_source", "submit_knowledge_points") if stage == "knowledge_points" else (
            "read_knowledge_point", "submit_qa")
        return {name: scoped(getattr(self, name)) for name in names}


def generate_with_skill(knowledge, *, skill: Path, agent_factory: Callable | None,
                        count: int, seed: int, model: str, env_file: str | Path,
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
    workspace = GenerationWorkspace(sources, page_chars=min(12000, max(1, profile.max_input_tokens // 4)))
    reporter = ProgressReporter(verbose)
    reporter.configuration({"skill": skill, "model": model, "sources": len(sources), "count": count, "seed": seed})
    skill_files = {"SKILL.md": hashlib.sha256((skill / "SKILL.md").read_bytes()).hexdigest()}
    executions = []
    if agent_factory is None:
        from .skill_agent import AkashaSkillAgent
        agent_factory = AkashaSkillAgent

    def execute(request: dict, finished: Callable[[], bool]) -> dict:
        errors = []
        for attempt in range(1, 4):
            event_start = len(workspace.events)
            reporter.emit(request["stage"].upper(),
                          f"item={request.get('source_id', request.get('knowledge_point_id'))} attempt={attempt}/3")
            # Initialization errors are fatal, unlike one model work item failing.
            agent = agent_factory(skills=[str(skill)], tools=workspace.tools(request["stage"]),
                                  model=model, env_file=str(env_file), temperature=temperature,
                                  max_input_tokens=profile.max_input_tokens,
                                  max_output_tokens=profile.max_output_tokens, verbose=verbose)
            work = {**request, "attempt": attempt}
            if request["stage"] == "knowledge_points":
                work.update(workspace.coverage(request["source_id"]))
                work["unresolved_rejections"] = workspace.pending_rejections[request["source_id"]]
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
                continue
            if skill.name not in result.get("loaded_skills", []):
                raise DatasetValidationError("agent did not load the selected skill")
            for name, digest in result.get("skill_files", {}).items():
                if name in skill_files and skill_files[name] != digest:
                    raise DatasetValidationError(f"skill file changed during generation: {name}")
                skill_files[name] = digest
            executions.append({**request, "attempt": attempt, **result,
                               "host_tool_events": workspace.events[event_start:]})
            if finished():
                return {"status": "complete", "attempts": attempt, "errors": errors}
            errors.append({"attempt": attempt, "error_type": "IncompleteWork"})
            reporter.emit("RETRY", f"stage={request['stage']} attempt={attempt}/3 error=IncompleteWork")
        return {"status": "failed", "attempts": 3, "errors": errors}

    for source_id in workspace.sources:
        workspace.current_source = source_id
        workspace.sources[source_id]["work"] = execute(
            {"stage": "knowledge_points", "source_id": source_id},
            lambda: not workspace.coverage(source_id)["unread_ranges"] and not workspace.pending_rejections[source_id])
    workspace.current_source = None
    point_ids = list(workspace.points)
    random.Random(seed).shuffle(point_ids)
    records = []
    lines = []
    seen_qa = {}
    for point_id in point_ids:
        if count and len(records) >= count:
            workspace.points[point_id].update(status="not_attempted_count_limit", attempts=0, errors=[])
            continue
        workspace.current_point = point_id
        workspace.points[point_id].update(execute(
            {"stage": "qa", "knowledge_point_id": point_id}, lambda: point_id in workspace.qa))
        if point_id in workspace.qa:
            row = workspace.qa[point_id]
            identity = (normalize(row["question"]), normalize(row["expected_answer"]))
            if identity in seen_qa:
                lines[seen_qa[identity]]["knowledge_point_ids"].append(point_id)
                lines[seen_qa[identity]]["qa_ids"].append(point_id.replace("kp_", "qa_", 1))
            else:
                seen_qa[identity] = len(records)
                records.append(row)
                qa_id = point_id.replace("kp_", "qa_", 1)
                lines.append({"line": len(records), "qa_id": qa_id, "qa_ids": [qa_id],
                              "knowledge_point_ids": [point_id]})
    if not records:
        raise DatasetValidationError("no valid question records were generated")
    reporter.done(len(records))
    return GenerationResult(records, {
        "schema_version": 1, "status": "partial" if any(
            point.get("status") == "failed" for point in workspace.points.values()) or any(
            source["work"]["status"] == "failed" for source in workspace.sources.values()) else "complete",
        "skill": {"name": skill.name, "path": str(skill), "files": skill_files},
        "settings": {"model": model, "temperature": temperature, "count": count, "seed": seed,
                     "max_attempts": 3, "read_page_chars": workspace.page_chars,
                     "max_input_tokens": profile.max_input_tokens, "max_output_tokens": profile.max_output_tokens},
        "sources": [{"id": key, "path": source["path"], **source["work"], **workspace.coverage(key),
                     "unresolved_rejections": workspace.pending_rejections[key],
                     "sha256": hashlib.sha256(source["text"].encode("utf-8")).hexdigest()}
                    for key, source in workspace.sources.items()],
        "knowledge_points": list(workspace.points.values()),
        "qa": [{"id": key.replace("kp_", "qa_", 1), "knowledge_point_id": key, **value}
               for key, value in workspace.qa.items()],
        "reads": list(workspace.reads.values()), "executions": executions,
        "dataset": {"lines": lines},
        "stats": {**workspace.stats,
                  "failed_sources": sum(s["work"]["status"] == "failed" for s in workspace.sources.values()),
                  "failed_points": sum(p.get("status") == "failed" for p in workspace.points.values()),
                  "valid_qa_points": len(workspace.qa), "accepted_points": len(workspace.points),
                  "attempted_points": sum(p.get("attempts", 0) > 0 for p in workspace.points.values()),
                  "output_records": len(records), "output_points": sum(len(line["knowledge_point_ids"]) for line in lines),
                  "deduplicated_qa": len(workspace.qa) - len(records),
                  "not_attempted_count_limit": sum(p.get("status") == "not_attempted_count_limit"
                                                   for p in workspace.points.values())},
    })
