from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .exceptions import EvaluationError, ProviderError
from .providers import AkashaProvider, LLMProvider, generate_structured
from .validation_retry import run_validated


DEFAULT_REPORT_MODEL = "gemini:gemini-3.7-flash"


def create_report(
    eval_output: str | Path,
    output: str | Path,
    *,
    model: str = DEFAULT_REPORT_MODEL,
    env_file: str | Path = ".env",
    provider: LLMProvider | None = None,
    force: bool = False,
) -> Path:
    """Render deterministic tables and an Agent-written evidence-bounded narrative."""
    source = Path(eval_output)
    target = Path(output)
    if target.exists() and not force:
        raise FileExistsError(f"output already exists: {target}")
    try:
        evaluation = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"could not read evaluation JSON: {source}") from error
    _validate_evaluation(evaluation)
    facts = {
        "evaluation_prompt": evaluation["evaluation_prompt"],
        "evaluator_model": evaluation["evaluator_model"],
        "plan": evaluation["plan"],
        "summary": evaluation["summary"],
        "aggregates": evaluation["aggregates"],
    }
    active_provider = provider or AkashaProvider(env_file=str(env_file))
    try:
        narrative = run_validated(
            _report_prompt(facts),
            lambda active_prompt: generate_structured(
                active_provider,
                active_prompt,
                model=model,
                temperature=0.0,
            ),
            _validate_narrative,
            retry_on=(ProviderError, EvaluationError, TypeError, KeyError, ValueError),
        )
    except Exception as error:
        raise EvaluationError(f"report Agent failed: {type(error).__name__}: {error}") from error

    markdown = _render(evaluation, narrative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8", newline="\n")
    return target


def _validate_evaluation(value: Any) -> None:
    required = {"source", "evaluation_prompt", "evaluator_model", "plan", "summary", "aggregates", "items"}
    if not isinstance(value, dict) or set(value) != required:
        raise EvaluationError("evaluation JSON does not match the current contract")
    if not isinstance(value["summary"], dict) or not isinstance(value["items"], list):
        raise EvaluationError("evaluation JSON has invalid summary or items")


def _report_prompt(facts: dict[str, Any]) -> str:
    return """Write an evidence-bounded Traditional Chinese interpretation of an Agent evaluation.

Return only a JSON object with exactly three non-empty string fields:
- overview
- findings
- limitations

Use only the supplied facts. Do not calculate, change, or restate numeric values;
Python will print all numbers in fixed tables. Do not use Arabic digits. Do not
claim good or bad performance when evaluated is zero. Clearly identify inferred
methods as inferences.

FACTS_JSON:
""" + json.dumps(facts, ensure_ascii=False)


def _validate_narrative(value: Any) -> dict[str, str]:
    fields = {"overview", "findings", "limitations"}
    if not isinstance(value, dict) or set(value) != fields:
        raise EvaluationError("report narrative must contain exactly overview, findings, and limitations")
    result: dict[str, str] = {}
    for field in fields:
        text = value[field]
        if not isinstance(text, str) or not text.strip():
            raise EvaluationError(f"report narrative omitted {field}")
        if re.search(r"\d", text):
            raise EvaluationError(f"report narrative used an unverified number in {field}")
        result[field] = text.strip()
    return result


def _render(evaluation: dict[str, Any], narrative: dict[str, str]) -> str:
    summary = evaluation["summary"]
    lines = [
        "# LLaDAR evaluation report",
        "",
        narrative["overview"],
        "",
        "## Evaluation method",
        "",
        f"- Evaluator model: `{_escape(evaluation['evaluator_model'])}`",
        f"- Plan: {_escape(evaluation['plan']['title'])}",
        f"- Approach: {_escape(evaluation['plan']['approach'])}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for name in ("total", "evaluated", "missing_response", "judge_error", "coverage"):
        lines.append(f"| {_escape(name)} | {_format_value(summary.get(name))} |")
    lines.extend(["", "## Results", "", narrative["findings"], ""])
    for name, aggregate in evaluation["aggregates"].items():
        lines.extend([
            f"### {_escape(name)}",
            "",
            _escape(aggregate["description"]),
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ])
        for key, value in aggregate.items():
            if key in {"description", "distribution"}:
                continue
            lines.append(f"| {_escape(key)} | {_format_value(value)} |")
        distribution = aggregate.get("distribution")
        if isinstance(distribution, dict):
            lines.extend(["", "| Category | Count |", "| --- | ---: |"])
            for category, count in distribution.items():
                lines.append(f"| {_escape(category)} | {count} |")
        lines.append("")
    lines.extend(["## Limitations", "", narrative["limitations"], ""])
    for limitation in evaluation["plan"].get("limitations", []):
        lines.append(f"- {_escape(limitation)}")
    lines.extend([
        "",
        "## Record appendix",
        "",
        "| # | Question | Expected answer | Actual response | Status | Judgment | Reason |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ])
    for item in evaluation["items"]:
        lines.append(
            "| {index} | {question} | {expected} | {actual} | {status} | {values} | {reason} |".format(
                index=item["index"],
                question=_cell(item["question"]),
                expected=_cell(item["expected_answer"]),
                actual=_cell(item["actual_response"]),
                status=_cell(item["status"]),
                values=_cell(json.dumps(item.get("values", {}), ensure_ascii=False, sort_keys=True)),
                reason=_cell(item.get("reason", "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _cell(value: Any) -> str:
    return _escape("" if value is None else value)


def _format_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return _escape(value)
