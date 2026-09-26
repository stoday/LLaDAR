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
    required = {"source", "trials_source", "skill", "evaluator_model", "plan", "summary", "aggregates", "stability", "items"}
    if not isinstance(evaluation, dict) or set(evaluation) != required:
        raise EvaluationError("evaluation JSON does not match the current contract")
    selected_skill = resolve_skill(skill, BUILTIN_REPORT_SKILL_DIR)
    workspace = ReportWorkspace()
    evidence = invoke_skill(skill=selected_skill, tools={"submit_report": workspace.submit_report},
                            request={"stage": "report", "facts": {key: evaluation[key] for key in ("plan", "summary", "aggregates", "stability")}},
                            model=model, env_file=env_file, system_prompt=REPORT_SYSTEM_PROMPT,
                            agent_factory=skill_agent_factory)
    if workspace.narrative is None:
        raise EvaluationError("report skill did not submit a report")
    lines = ["# LLaDAR evaluation report", "", workspace.narrative["overview"], "", "## Summary", "", "| Metric | Value |", "| --- | ---: |"]
    lines += [f"| {_escape(key)} | {_value(value)} |" for key, value in evaluation["summary"].items()]
    lines += ["", "## Results", "", workspace.narrative["findings"], ""]
    for name, aggregate in evaluation["aggregates"].items():
        lines += [f"### {_escape(name)}", "", _escape(aggregate["description"]), "", "| Metric | Value |", "| --- | ---: |"]
        lines += [f"| {_escape(key)} | {_value(value)} |" for key, value in aggregate.items() if key not in {"description", "distribution"}]
    lines += ["", "## Stability", "", "| Record | Trials | Correct | Incorrect | Execution error | Judge error | Correct rate | Fully correct | Outcome consistent |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for row in evaluation["stability"]["records"]:
        lines.append("| {record_index} | {scheduled_trials} | {correct} | {incorrect} | {execution_error} | {judge_error} | {correct_rate} | {fully_correct} | {outcome_consistent} |".format(**{key: _value(value) for key, value in row.items()}))
    lines += ["", "## Limitations", "", workspace.narrative["limitations"], "", "## Trial appendix", "", "| Record | Trial | Question | Expected answer | Actual response | Status | Judgment | Reason |", "| ---: | ---: | --- | --- | --- | --- | --- | --- |"]
    for item in evaluation["items"]:
        lines.append("| {record_index} | {trial} | {question} | {expected_answer} | {actual_response} | {status} | {values} | {reason} |".format(**{key: _cell(json.dumps(value, ensure_ascii=False, sort_keys=True) if key == "values" else value) for key, value in item.items()}))
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
