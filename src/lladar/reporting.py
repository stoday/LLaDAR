"""Evidence-bounded reports: skills write prose, Python renders facts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .exceptions import EvaluationError
from .method_skill import SkillAgentFactory, invoke_skill, resolve_skill


DEFAULT_REPORT_MODEL = "gemini:gemini-3.7-flash"
BUILTIN_REPORT_SKILL_DIR = Path(__file__).resolve().parent / "skill_assets" / "report-evidence-summary"
REPORT_SYSTEM_PROMPT = """Load the selected report skill before using tools.
Use only the supplied evaluation facts. Submit overview, findings, and
limitations; the host renders all measurements and record evidence."""


class ReportWorkspace:
    def __init__(self) -> None:
        self.narrative: dict[str, str] | None = None

    def submit_report(self, narrative: dict[str, str]) -> dict[str, bool]:
        fields = {"overview", "findings", "limitations"}
        if not isinstance(narrative, dict) or set(narrative) != fields or any(not isinstance(narrative[key], str) or not narrative[key].strip() for key in fields):
            raise ValueError("report must contain non-empty overview, findings, and limitations")
        if any(re.search(r"\d", narrative[key]) for key in fields):
            raise ValueError("report prose must not introduce numeric claims")
        self.narrative = {key: narrative[key].strip() for key in fields}
        return {"accepted": True}


def create_report(eval_output: str | Path, output: str | Path, *, skill: str | Path | None = None,
                  model: str = DEFAULT_REPORT_MODEL, env_file: str | Path = ".env",
                  skill_agent_factory: SkillAgentFactory | None = None, force: bool = False) -> Path:
    source, target = Path(eval_output), Path(output)
    if target.exists() and not force:
        raise FileExistsError(f"output already exists: {target}")
    try:
        evaluation = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"could not read evaluation JSON: {source}") from error
    if isinstance(evaluation, dict) and evaluation.get("kind") == "situation_evaluation":
        return _situation_report(evaluation, target, skill=skill, model=model,
                                 env_file=env_file, skill_agent_factory=skill_agent_factory)
    required = {"source", "trials_source", "skill", "evaluator_model", "plan", "summary", "aggregates", "stability", "items"}
    if not isinstance(evaluation, dict) or set(evaluation) not in (required, required | {"evaluation_settings"}):
        raise EvaluationError("evaluation JSON does not match the current contract")
    selected_skill = resolve_skill(skill, BUILTIN_REPORT_SKILL_DIR)
    workspace = ReportWorkspace()
    evidence = invoke_skill(skill=selected_skill, tools={"submit_report": workspace.submit_report},
                            request={"stage": "report", "facts": {key: evaluation[key] for key in ("plan", "summary", "aggregates", "stability", "evaluation_settings") if key in evaluation}},
                            model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                            agent_factory=skill_agent_factory)
    if workspace.narrative is None:
        raise EvaluationError("report skill did not submit a report")
    lines = ["# LLaDAR evaluation report", "", workspace.narrative["overview"], "", "## Summary", "", "| Metric | Value |", "| --- | ---: |"]
    lines += [f"| {_escape(key)} | {_value(value)} |" for key, value in evaluation["summary"].items()]
    lines += _evaluation_settings_lines(evaluation)
    lines += ["", "## Results", "", workspace.narrative["findings"], ""]
    for name, aggregate in evaluation["aggregates"].items():
        lines += [f"### {_escape(name)}", "", _escape(aggregate["description"]), "", "| Metric | Value |", "| --- | ---: |"]
        lines += [f"| {_escape(key)} | {_value(value)} |" for key, value in aggregate.items() if key not in {"description", "distribution"}]
    has_correctness = any(d["name"] == "correct" and d["kind"] == "boolean"
                          for d in evaluation["plan"]["dimensions"]) or any(
        type(item.get("values", {}).get("correct")) is bool for item in evaluation["items"])
    type_rows = _question_type_rows(evaluation["items"]) if has_correctness else []
    if type_rows:
        lines += ["", "## By question type", "", "| Question type | Scheduled | Execution error | Evaluated | Invalid response format | Correct | Correct rate |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in type_rows:
            lines.append("| {question_type} | {scheduled} | {execution_error} | {evaluated} | {invalid_response_format} | {correct} | {correct_rate} |".format(**{key: _value(value) for key, value in row.items()}))
    probe_rows = _semantic_probe_rows(evaluation["items"]) if "mapping_outcome" in evaluation["aggregates"] else []
    if probe_rows:
        lines += ["", "## Semantic probes", "", "| Concept | Scheduled | Mapped | Synthesized | Unmapped |", "| --- | ---: | ---: | ---: | ---: |"]
        for row in probe_rows:
            lines.append("| {concept_id} | {scheduled} | {mapped} | {synthesized} | {unmapped} |".format(**{key: _value(value) for key, value in row.items()}))
    controlled_rows = _controlled_variant_rows(evaluation["items"]) if "mapping_outcome" in evaluation["aggregates"] else []
    if controlled_rows:
        lines += ["", "## Controlled variants", "", "| Dimension | Pair | Scheduled | Mapped | Same | Different | Indeterminate |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in controlled_rows:
            lines.append("| {varied_dimension} | {pair_id} | {scheduled} | {mapped} | {same} | {different} | {indeterminate} |".format(**{key: _value(value) for key, value in row.items()}))
    if evaluation["stability"]["records"]:
        lines += ["", "## Stability", "", "| Record | Trials | Correct | Incorrect | Execution error | Judge error | Correct rate | Fully correct | Outcome consistent |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for row in evaluation["stability"]["records"]:
        lines.append("| {record_index} | {scheduled_trials} | {correct} | {incorrect} | {execution_error} | {judge_error} | {correct_rate} | {fully_correct} | {outcome_consistent} |".format(**{key: _value(value) for key, value in row.items()}))
    lines += ["", "## Limitations", "", workspace.narrative["limitations"], "", "## Trial appendix", "", "| Record | Trial | Question | Expected answer | Actual response | Status | Judgment | Reason |", "| ---: | ---: | --- | --- | --- | --- | --- | --- |"]
    for item in evaluation["items"]:
        lines.append("| {record_index} | {trial} | {question} | {expected_answer} | {actual_response} | {status} | {values} | {reason} |".format(**{key: _cell(json.dumps(value, ensure_ascii=False, sort_keys=True) if key == "values" else value) for key, value in item.items()}))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return target


def _evaluation_settings_lines(evaluation: dict[str, Any]) -> list[str]:
    settings = evaluation.get("evaluation_settings")
    if settings is None:
        return []
    lines = ["", "## Evaluation settings", "", f"Mode: {_escape(settings['mode'])}"]
    if "criteria" in settings:
        lines += ["", "Criteria: " + _escape(settings["criteria"]),
                  "Criteria SHA-256: " + _escape(settings["criteria_sha256"])]
    if "skill" in settings:
        lines += ["", "Skill: " + _escape(settings["skill"]["path"]),
                  "Skill SHA-256: " + _escape(settings["skill"]["sha256"])]
    return lines


def _situation_report(evaluation: dict[str, Any], target: Path, *,
                      skill: str | Path | None, model: str, env_file: str | Path,
                      skill_agent_factory: SkillAgentFactory | None) -> Path:
    required = {"kind", "source", "situation_config", "situation_sha256",
                "evaluator_model", "method", "observe", "summary", "items"}
    if set(evaluation) not in (required, required | {"evaluation_settings"}) or not isinstance(evaluation["items"], list):
        raise EvaluationError("situation evaluation JSON does not match the current contract")
    narrative = None
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
                     agent_factory=skill_agent_factory)
        if workspace.narrative is None:
            raise EvaluationError("report skill did not submit a report")
        narrative = workspace.narrative
    lines = ["# LLaDAR situation evaluation", ""]
    if narrative:
        lines += [narrative["overview"], ""]
    lines += ["## Observation", "", _escape(evaluation["observe"]["text"]), "",
             "## Summary", "", "| Metric | Value |", "| --- | ---: |"]
    lines += [f"| {_escape(key)} | {_value(value)} |"
              for key, value in evaluation["summary"].items()]
    lines += _evaluation_settings_lines(evaluation)
    lines += ["", "## Trial evidence", "",
              "| Scenario | Status | Turns | Validity | Behavior | Evidence turns | Reason |",
              "| --- | --- | ---: | --- | --- | --- | --- |"]
    for item in evaluation["items"]:
        lines.append("| {scenario} | {status} | {turns} | {validity} | {behavior} | {evidence} | {reason} |".format(
            scenario=_cell(item["scenario_id"]), status=_cell(item["status"]),
            turns=len(item["turns"]), validity=_cell(item["validity"]),
            behavior=_cell(item["behavior"]),
            evidence=_cell(", ".join(item["evidence_turn_ids"])),
            reason=_cell(item["reason"])))
    if narrative:
        lines += ["", "## Findings", "", narrative["findings"]]
    lines += ["", "## Limits", "",
              "The rate uses only completed, valid, determinate trials. "
              "The saved transcripts and cited turn IDs support review of each judgment."]
    if narrative:
        lines += ["", narrative["limitations"]]
    lines += ["", f"Transcript: {_escape(evaluation['source'])}",
              f"Situation config: {_escape(evaluation['situation_config'])}"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
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
