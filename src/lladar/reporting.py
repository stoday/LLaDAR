"""Evidence-bounded reports: skills write prose, Python renders facts."""

from __future__ import annotations

from .language import tr, language_stage, current_language

import json
import re
from pathlib import Path
from typing import Any

from .exceptions import EvaluationError
from .model_profiles import resolve_model_profile
from .method_skill import SkillAgentFactory, invoke_skill, resolve_skill


DEFAULT_REPORT_MODEL = "gemini:gemini-3.7-flash"
BUILTIN_REPORT_SKILL_DIR = Path(__file__).resolve().parent / "skill_assets" / "report-evidence-summary"
REPORT_SYSTEM_PROMPT = """Load the selected report skill before using tools.
Use only the supplied evaluation facts. Submit overview, findings, and
limitations; the host renders all measurements and record evidence."""

from .work_session import current_session, stage_session, publish_bundle, publication_metadata
from .submissions import Submissions
from .progress import ProgressReporter


class ReportWorkspace:
    def __init__(self, evaluation=None, reporter=None):
        self.submissions = Submissions("report_sections")
        self.facts = evaluation or {}
        self.references = {"facts": self.facts, **self.facts}
        for item in self.facts.get("items", []):
            self.references[item.get("trial_id", f"trial_{item['record_index']}_{item['trial']}")] = item
        self.reporter = reporter

    @property
    def narrative(self):
        values = {key: self.submissions.get(key) for key in ("overview", "findings", "limitations")}
        if all(value.get("status") == "accepted" for value in values.values()):
            return {key: value["value"]["text"] for key, value in values.items()}
        return None

    def validate(self, payload):
        if not isinstance(payload["text"], str) or not payload["text"].strip():
            raise ValueError("text must be non-empty")
        refs = payload["source_refs"]
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in self.references for ref in refs):
            raise ValueError("source_refs must identify frozen facts or trials from this evaluation")
        if re.search(r"\d", payload["text"]):
            raise ValueError("report prose must not introduce numeric claims; use the deterministic tables")
        return payload

    def submit_section(self, section: str, text: str, source_refs: list[str], request_id: str | None = None) -> dict:
        if section not in {"overview", "findings", "limitations"}:
            return {"status": "needs_revision", "issues": [{"field": "section", "code": "unknown_section",
                    "message": "Use overview, findings or limitations", "repair_action": "replace_section"}]}
        result = self.submissions.submit(section, {"text": text, "source_refs": source_refs}, self.validate,
                                         request_id=request_id, frozen=True)
        if self.reporter:
            completed = sum(self.submissions.get(key).get("status") == "accepted"
                for key in ("overview", "findings", "limitations"))
            self.reporter.group(completed, 3, tr('Report sections'))
        return result

    def revise_section(self, item_id: str, request_id: str, revision: int, text: str, source_refs: list[str]) -> dict:
        if item_id not in {"overview", "findings", "limitations"}:
            return self.submissions.get("unknown")
        return self.submissions.submit(item_id, {"text": text, "source_refs": source_refs}, self.validate,
                                       request_id=request_id, revision=revision, frozen=True)

    def get_section(self, item_id: str) -> dict:
        return self.submissions.get(item_id)

    def read_report_fact(self, reference: str, start_char: int = 0) -> dict:
        if reference not in self.references:
            raise ValueError("unknown fact reference")
        text = json.dumps(self.references[reference], ensure_ascii=False, sort_keys=True)
        if type(start_char) is not int or not 0 <= start_char < len(text):
            raise ValueError("invalid fact start_char")
        end = min(len(text), start_char + 12000)
        return {"reference": reference, "start_char": start_char, "end_char": end,
                "text": text[start_char:end], "next_start": end if end < len(text) else None}

    def submit_report(self, narrative: dict[str, str]) -> dict:
        if not isinstance(narrative, dict) or set(narrative) != {"overview", "findings", "limitations"}:
            return {"status": "needs_revision", "issues": [{"field": "narrative", "code": "invalid_fields",
                "message": "Submit overview, findings and limitations", "repair_action": "revise_item"}]}
        results = {key: self.submit_section(key, text, ["facts"]) for key, text in narrative.items()}
        return {"accepted": True} if self.narrative is not None else {"status": "needs_revision", "accepted": False,
            "sections": results, "error": "report has unresolved sections"}


@language_stage
@stage_session("report")

def create_report(eval_output: str | Path, output: str | Path, *, skill: str | Path | None = None,
                  model: str = DEFAULT_REPORT_MODEL, env_file: str | Path = ".env",
                  skill_agent_factory: SkillAgentFactory | None = None, force: bool = False,
                  review_agent_factory: SkillAgentFactory | None = None,
                  verbose: bool = True, resume: str | Path | None = None,
                  max_input_tokens: int | None = None, max_output_tokens: int | None = None, lang: str | None = None) -> Path:
    profile = resolve_model_profile(model,max_input_tokens=max_input_tokens,max_output_tokens=max_output_tokens)
    source, target = Path(eval_output), Path(output)
    if target.exists() and not force:
        raise FileExistsError(f"output already exists: {target}")
    try:
        evaluation = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"could not read evaluation JSON: {source}") from error
    if isinstance(evaluation, dict) and evaluation.get("kind") == "situation_evaluation":
        return _situation_report(evaluation, target, skill=skill, model=model,
                                 env_file=env_file, skill_agent_factory=skill_agent_factory, max_input_tokens=profile.max_input_tokens, max_output_tokens=profile.max_output_tokens, force=force)
    required = {"source", "trials_source", "skill", "evaluator_model", "plan", "summary", "aggregates", "stability", "items"}
    optional = {"session", "evaluation_settings", "max_input_tokens", "max_output_tokens"}
    if not isinstance(evaluation, dict) or not required <= set(evaluation) <= required | optional:
        raise EvaluationError("evaluation JSON does not match the current contract")
    selected_skill = resolve_skill(skill, BUILTIN_REPORT_SKILL_DIR)
    reporter = ProgressReporter(verbose)
    workspace = ReportWorkspace(evaluation, reporter)
    if workspace.narrative is None:
        with reporter.waiting(tr('Writing report'), label="REPORT"):
            evidence = invoke_skill(skill=selected_skill, tools={"submit_report": workspace.submit_report, "submit_section": workspace.submit_section,
                    "revise_section": workspace.revise_section, "get_section": workspace.get_section,
                    "read_report_fact": workspace.read_report_fact},
                                    request={"stage": "report", "completed_sections": [key for key in ("overview", "findings", "limitations")
                                        if workspace.submissions.get(key).get("status") == "accepted"],
                                        "facts": {key: evaluation[key] for key in ("plan", "summary", "aggregates", "evaluation_settings") if key in evaluation}},
                                    model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                                    agent_factory=skill_agent_factory, max_input_tokens=profile.max_input_tokens, max_output_tokens=profile.max_output_tokens)
    if workspace.narrative is None:
        raise EvaluationError("report skill did not submit a report")
    session = current_session()
    semantic_review = "not_run_injected_author"
    if skill_agent_factory is None or review_agent_factory is not None:
        semantic_review = "passed"
        for repair in range(4):
            revisions = {key: workspace.submissions.get(key)["revision"] for key in
                         ("overview", "findings", "limitations")}
            prior = session.data["state"].get("report_review") if session else None
            reviews = []
            def submit_section_review(judgments: list[dict]) -> dict:
                if not isinstance(judgments, list) or len(judgments) != 3 or {
                    item.get("section") for item in judgments if isinstance(item, dict)
                } != set(revisions) or any(set(item) != {"section", "approved", "reason"}
                    or type(item["approved"]) is not bool or not isinstance(item["reason"], str)
                    or not item["reason"].strip() for item in judgments):
                    raise ValueError("review must judge every required section with approved and evidence-bounded reason")
                reviews[:] = judgments
                if session:
                    session.put("report_review", {"revisions": revisions, "judgments": judgments})
                return {"accepted": True}
            if prior and prior["revisions"] == revisions and all(item["approved"] for item in prior["judgments"]):
                reviews = prior["judgments"]
            else:
                with reporter.waiting(tr('Checking report support'), label="REPORT"):
                    invoke_skill(skill=selected_skill, tools={"submit_section_review": submit_section_review,
                        "read_report_fact": workspace.read_report_fact},
                        request={"stage": "report_review", "sections": workspace.narrative,
                                 "facts": {key: evaluation[key] for key in ("plan", "summary", "aggregates")}},
                        model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                        agent_factory=review_agent_factory, max_input_tokens=profile.max_input_tokens, max_output_tokens=profile.max_output_tokens)
            if not reviews:
                raise EvaluationError("report review did not submit all section judgments")
            unsupported = [item for item in reviews if not item["approved"]]
            if not unsupported:
                break
            updated = dict(workspace.submissions.state)
            for review in unsupported:
                draft = dict(updated[review["section"]])
                draft.update(status="rejected" if draft["revision"] >= 4 else "needs_revision",
                    issues=[{"field": "text", "code": "unsupported_claim", "message": review["reason"],
                             "repair_action": "revise_item"}])
                updated[review["section"]] = draft
            if session:
                session.put("report_sections", updated)
            workspace.submissions.state = updated
            if repair == 3 or any(updated[item["section"]]["status"] == "rejected" for item in unsupported):
                raise EvaluationError("report review found unsupported sections; saved drafts require revision")
            invoke_skill(skill=selected_skill, tools={"submit_report": workspace.submit_report,
                "submit_section": workspace.submit_section, "revise_section": workspace.revise_section,
                "get_section": workspace.get_section, "read_report_fact": workspace.read_report_fact},
                request={"stage": "report", "completed_sections": [key for key in revisions
                    if updated[key]["status"] == "accepted"], "revision_issues": unsupported,
                    "facts": {key: evaluation[key] for key in ("plan", "summary", "aggregates")}},
                model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                agent_factory=skill_agent_factory, max_input_tokens=profile.max_input_tokens, max_output_tokens=profile.max_output_tokens)
            if workspace.narrative is None:
                raise EvaluationError("report sections remain incomplete after review")
    lines = [tr('# LLaDAR evaluation report'), "", workspace.narrative["overview"], "", tr('## Summary'), "", tr('| Metric | Value |'), "| --- | ---: |"]
    lines += [f"| {_escape(tr(key))} | {_value(value)} |" for key, value in evaluation["summary"].items()]
    lines += _evaluation_settings_lines(evaluation)
    lines += ["", tr('## Results'), "", workspace.narrative["findings"], ""]
    for name, aggregate in evaluation["aggregates"].items():
        lines += [f"### {_escape(name)}", "", _escape(aggregate["description"]), "", tr('| Metric | Value |'), "| --- | ---: |"]
        lines += [f"| {_escape(tr(key))} | {_value(value)} |" for key, value in aggregate.items() if key not in {"description", "distribution"}]
    has_correctness = any(d["name"] == "correct" and d["kind"] == "boolean"
                          for d in evaluation["plan"]["dimensions"]) or any(
        type(item.get("values", {}).get("correct")) is bool for item in evaluation["items"])
    type_rows = _question_type_rows(evaluation["items"]) if has_correctness else []
    if type_rows:
        lines += ["", tr('## By question type'), "", tr('| Question type | Scheduled | Execution error | Evaluated | Invalid response format | Correct | Correct rate |'), "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in type_rows:
            lines.append("| {question_type} | {scheduled} | {execution_error} | {evaluated} | {invalid_response_format} | {correct} | {correct_rate} |".format(**{key: _value(value) for key, value in row.items()}))
    probe_rows = _semantic_probe_rows(evaluation["items"]) if "mapping_outcome" in evaluation["aggregates"] else []
    if probe_rows:
        lines += ["", tr('## Semantic probes'), "", tr('| Concept | Scheduled | Mapped | Synthesized | Unmapped |'), "| --- | ---: | ---: | ---: | ---: |"]
        for row in probe_rows:
            lines.append("| {concept_id} | {scheduled} | {mapped} | {synthesized} | {unmapped} |".format(**{key: _value(value) for key, value in row.items()}))
    controlled_rows = _controlled_variant_rows(evaluation["items"]) if "mapping_outcome" in evaluation["aggregates"] else []
    if controlled_rows:
        lines += ["", tr('## Controlled variants'), "", tr('| Dimension | Pair | Scheduled | Mapped | Same | Different | Indeterminate |'), "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in controlled_rows:
            lines.append("| {varied_dimension} | {pair_id} | {scheduled} | {mapped} | {same} | {different} | {indeterminate} |".format(**{key: _value(value) for key, value in row.items()}))
    if evaluation["stability"]["records"]:
        lines += ["", tr('## Stability'), "", tr('| Record | Trials | Correct | Incorrect | Execution error | Judge error | Correct rate | Fully correct | Outcome consistent |'), "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for row in evaluation["stability"]["records"]:
        lines.append("| {record_index} | {scheduled_trials} | {correct} | {incorrect} | {execution_error} | {judge_error} | {correct_rate} | {fully_correct} | {outcome_consistent} |".format(**{key: _value(value) for key, value in row.items()}))
    lines += ["", tr('## Limitations'), "", workspace.narrative["limitations"], "", tr('## Trial appendix'), "", tr('| Record | Trial | Question | Expected answer | Actual response | Status | Judgment | Reason |'), "| ---: | ---: | --- | --- | --- | --- | --- | --- |"]
    for item in evaluation["items"]:
        lines.append("| {record_index} | {trial} | {question} | {expected_answer} | {actual_response} | {status} | {values} | {reason} |".format(**{key: _cell(json.dumps(value, ensure_ascii=False, sort_keys=True) if key == "values" else value) for key, value in item.items()}))
    target.parent.mkdir(parents=True, exist_ok=True)
    report_sidecar = Path(str(target) + ".report.json")
    publish_bundle({target: "\n".join(lines).rstrip() + "\n", report_sidecar: json.dumps({
        "schema_version": 1, "session": publication_metadata(), "status": "complete",
        "semantic_review": semantic_review,
        "sections": {key: workspace.submissions.get(key) for key in ("overview", "findings", "limitations")},
        "source": str(source.resolve())}, ensure_ascii=False, indent=2) + "\n"}, force=force, result=target)
    reporter.emit("WRITE", tr('Report: {p0}', p0=target.resolve()))
    reporter.done(3, metric="sections")
    return target


def _evaluation_settings_lines(evaluation: dict[str, Any]) -> list[str]:
    settings = evaluation.get("evaluation_settings")
    if settings is None:
        return []
    lines = ["", tr('## Evaluation settings'), "", tr('Mode') + f": {_escape(settings['mode'])}"]
    if "criteria" in settings:
        lines += ["", tr('Criteria') + ": " + _escape(settings["criteria"]),
                  tr('Criteria SHA-256') + ": " + _escape(settings["criteria_sha256"])]
    if "skill" in settings:
        lines += ["", tr('Skill') + ": " + _escape(settings["skill"]["path"]),
                  tr('Skill SHA-256') + ": " + _escape(settings["skill"]["sha256"])]
    return lines


def _situation_report(evaluation: dict[str, Any], target: Path, *,
                      skill: str | Path | None, model: str, env_file: str | Path,
                      skill_agent_factory: SkillAgentFactory | None,
                      max_input_tokens: int, max_output_tokens: int, force: bool = False) -> Path:
    required = {"kind", "source", "situation_config", "situation_sha256",
                "evaluator_model", "method", "observe", "summary", "items"}
    optional = {"session", "evaluation_settings", "max_input_tokens", "max_output_tokens"}
    if not required <= set(evaluation) <= required | optional or not isinstance(evaluation["items"], list):
        raise EvaluationError("situation evaluation JSON does not match the current contract")
    narrative = None
    report_sidecar = Path(str(target) + ".report.json")
    if report_sidecar.exists() and not force:
        raise FileExistsError(f"output already exists: {report_sidecar}")
    if skill is not None:
        selected_skill = resolve_skill(skill, BUILTIN_REPORT_SKILL_DIR)
        workspace = ReportWorkspace()
        invoke_skill(skill=selected_skill, tools={"submit_report": workspace.submit_report},
                     request={"stage": "report", "facts": {
                         "observe": evaluation["observe"], "summary": evaluation["summary"],
                         "method": evaluation["method"],
                         "evaluation_settings": evaluation.get("evaluation_settings"),
                         "trial_evidence": [
                             {key: item[key] for key in (
                                 "scenario_id", "status", "validity", "behavior",
                                 "evidence_turn_ids", "reason")}
                             for item in evaluation["items"]
                         ],
                     }},
                     model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                     agent_factory=skill_agent_factory,
                     max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens)
        if workspace.narrative is None:
            raise EvaluationError("report skill did not submit a report")
        narrative = workspace.narrative
    lines = [tr('# LLaDAR situation evaluation'), ""]
    if narrative:
        lines += [narrative["overview"], ""]
    lines += [tr('## Observation'), "", _escape(evaluation["observe"]["text"]), "",
             tr('## Summary'), "", tr('| Metric | Value |'), "| --- | ---: |"]
    lines += [f"| {_escape(tr(key))} | {_value(value)} |"
              for key, value in evaluation["summary"].items()]
    lines += _evaluation_settings_lines(evaluation)
    lines += ["", tr('## Trial evidence'), "",
              tr('| Scenario | Status | Turns | Validity | Behavior | Evidence turns | Reason |'),
              "| --- | --- | ---: | --- | --- | --- | --- |"]
    for item in evaluation["items"]:
        lines.append("| {scenario} | {status} | {turns} | {validity} | {behavior} | {evidence} | {reason} |".format(
            scenario=_cell(item["scenario_id"]), status=_cell(item["status"]),
            turns=len(item["turns"]), validity=_cell(item["validity"]),
            behavior=_cell(item["behavior"]),
            evidence=_cell(", ".join(item["evidence_turn_ids"])),
            reason=_cell(item["reason"])))
    if narrative:
        lines += ["", tr('## Findings'), "", narrative["findings"]]
    lines += ["", tr('## Limits'), "",
              tr("The rate uses only completed, valid, determinate trials. The saved transcripts and cited turn IDs support review of each judgment.")]
    if narrative:
        lines += ["", narrative["limitations"]]
    lines += ["", tr('Transcript') + f": {_escape(evaluation['source'])}",
              tr('Situation config') + f": {_escape(evaluation['situation_config'])}"]
    target.parent.mkdir(parents=True, exist_ok=True)
    publish_bundle({target: "\n".join(lines).rstrip() + "\n",
        report_sidecar: json.dumps({"schema_version": 1, "session": publication_metadata(),
            "language": current_language().metadata(), "status": "complete",
            "source": evaluation["source"], "sections": narrative}, ensure_ascii=False, indent=2) + "\n"},
        force=force, result=target)
    return target


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _cell(value: Any) -> str:
    return _escape("" if value is None else value)


def _value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return _escape(value)


def _question_type_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        question_type = item.get("question_type")
        if isinstance(question_type, str):
            groups.setdefault(question_type, []).append(item)
    rows = []
    for question_type, group in sorted(groups.items()):
        evaluated = [item for item in group if item.get("status") == "evaluated"]
        correct = sum(item.get("values", {}).get("correct") is True for item in evaluated)
        rows.append({
            "question_type": question_type,
            "scheduled": len(group),
            "execution_error": sum(item.get("status") == "execution_error" for item in group),
            "evaluated": len(evaluated),
            "invalid_response_format": sum(item.get("values", {}).get("response_format_valid") is False for item in evaluated),
            "correct": correct,
            "correct_rate": correct / len(evaluated) if evaluated else None,
        })
    return rows


def _semantic_probe_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("probe_type") in {"concept_mapping", "controlled_invariance"} and isinstance(item.get("concept_id"), str):
            groups.setdefault(item["concept_id"], []).append(item)
    rows = []
    for concept_id, group in sorted(groups.items()):
        outcomes = [item.get("values", {}).get("mapping_outcome") for item in group if item.get("status") == "evaluated"]
        rows.append({"concept_id": concept_id, "scheduled": len(group),
                     "mapped": sum(isinstance(value, str) and value.startswith("maps_to:") for value in outcomes),
                     "synthesized": outcomes.count("synthesized"),
                     "unmapped": sum(value in {"unmapped", "external_or_unsupported", None} for value in outcomes)})
    return rows


def _controlled_variant_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        if (item.get("probe_type") == "controlled_invariance"
                and isinstance(item.get("varied_dimension"), str)
                and isinstance(item.get("pair_id"), str)):
            groups.setdefault((item["varied_dimension"], item["pair_id"]), []).append(item)
    rows = []
    for (dimension, pair_id), group in sorted(groups.items()):
        outcomes = [
            item.get("values", {}).get("mapping_outcome")
            for item in group if item.get("status") == "evaluated"
        ]
        mapped = [outcome for outcome in outcomes if isinstance(outcome, str) and outcome.startswith("maps_to:")]
        complete = len(mapped) == len(group) and len(group) >= 2
        rows.append({
            "varied_dimension": dimension,
            "pair_id": pair_id,
            "scheduled": len(group),
            "mapped": len(mapped),
            "same": len(mapped) if complete and len(set(mapped)) == 1 else 0,
            "different": len(mapped) if complete and len(set(mapped)) > 1 else 0,
            "indeterminate": len(group) - len(mapped),
        })
    return rows
