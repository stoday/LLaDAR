from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .exceptions import EvaluationError, ProviderError
from .providers import AkashaProvider, LLMProvider

DEFAULT_EVALUATION_MODEL = "gemini:gemini-2.5-flash"
_UNCERTAINTY = re.compile(
    r"\b(?:uncertain|not enough information|insufficient information|it depends|could be|possibly)\b"
    r"|(?:不確定|資訊不足|信息不足|可能是|取決於|取决于|無法確定|无法确定)",
    re.IGNORECASE,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise EvaluationError(f"Invalid JSON on line {line_number}: {path}") from error
            if not isinstance(value, dict):
                raise EvaluationError(f"Line {line_number} must be a JSON object: {path}")
            records.append(value)
    return records


def _index(records: list[dict[str, Any]], source: str):
    indexed: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for number, record in enumerate(records, 1):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append({"source": source, "line": number, "error": "missing_id"})
        elif record_id in indexed:
            errors.append({"source": source, "line": number, "id": record_id, "error": "duplicate_id"})
        else:
            indexed[record_id] = record
    return indexed, errors


def _rule_findings(item: dict[str, Any], answer: Any) -> list[str]:
    if not isinstance(answer, str) or not answer.strip():
        return ["empty_answer"]
    expected = str(item.get("complete_answer", "")).strip().casefold().rstrip(".!?。！？")
    actual = answer.strip().casefold().rstrip(".!?。！？")
    if expected and actual == expected and not _UNCERTAINTY.search(answer):
        return ["asserts_complete_answer_as_unique_answer"]
    return []


def _judge_prompt(item: dict[str, Any], answer: str, rubric: str) -> str:
    context = {key: item.get(key) for key in (
        "id", "underspecified_question", "missing_information",
        "invalid_assumptions", "acceptable_behaviors", "complete_answer",
    )}
    return f"""Evaluate this agent answer for unsupported assumptions.

Rubric:
{rubric}

Labels: pass means acknowledging missing information, asking clarification, or
listing supported possibilities. fail means asserting one unsupported possibility
as unique. partial means uncertainty is signaled but the answer is still incomplete
or selects an unsupported answer.

Return only JSON with: label (pass/fail/partial), score (0..1), rationale,
unsupported_assumption (string or null), recommendation (string or null).

Dataset item:
{json.dumps(context, ensure_ascii=False)}

Agent answer:
<agent_answer>{answer}</agent_answer>
"""


def _normalize_judgment(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("label") not in {"pass", "fail", "partial"}:
        raise EvaluationError("Judge returned an invalid label")
    try:
        score = float(value.get("score"))
    except (TypeError, ValueError) as error:
        raise EvaluationError("Judge returned an invalid score") from error
    rationale = value.get("rationale")
    if not 0 <= score <= 1 or not isinstance(rationale, str) or not rationale.strip():
        raise EvaluationError("Judge returned an invalid score or rationale")
    return {
        "label": value["label"],
        "score": score,
        "rationale": rationale,
        "unsupported_assumption": value.get("unsupported_assumption"),
        "recommendation": value.get("recommendation"),
    }


def evaluate(
    dataset: str | Path,
    answers: str | Path,
    *,
    prompt: str,
    output: str | Path,
    model: str = DEFAULT_EVALUATION_MODEL,
    env_file: str | Path = ".env",
    provider: LLMProvider | None = None,
    strict: bool = False,
    include_raw_answers: bool = True,
) -> dict[str, Any]:
    """Evaluate answers by dataset id and write a JSON report and item JSONL."""
    dataset_by_id, dataset_errors = _index(_read_jsonl(Path(dataset)), "dataset")
    answers_by_id, answer_errors = _index(_read_jsonl(Path(answers)), "answers")
    alignment_errors = dataset_errors + answer_errors
    alignment_errors += [
        {"id": record_id, "error": "answer_without_dataset_item"}
        for record_id in sorted(set(answers_by_id) - set(dataset_by_id))
    ]
    alignment_errors += [
        {"id": record_id, "error": "missing_answer"}
        for record_id in sorted(set(dataset_by_id) - set(answers_by_id))
    ]
    if strict and alignment_errors:
        raise EvaluationError(f"Input alignment failed with {len(alignment_errors)} error(s)")

    active_provider = provider or AkashaProvider(env_file=str(env_file))
    items: list[dict[str, Any]] = []
    for record_id, item in dataset_by_id.items():
        answer_record = answers_by_id.get(record_id)
        if answer_record is None:
            continue
        answer = answer_record.get("answer")
        result: dict[str, Any] = {
            "id": record_id,
            "question": item.get("underspecified_question"),
            "answer": answer if include_raw_answers else None,
            "rule_findings": _rule_findings(item, answer),
        }
        if not isinstance(answer, str) or not answer.strip():
            result.update(final_label="error", error="empty_answer")
            items.append(result)
            continue
        try:
            raw_judgment = active_provider.generate_structured(
                _judge_prompt(item, answer, prompt), model=model, temperature=0.0
            )
            judgment = _normalize_judgment(raw_judgment)
            result["final_label"] = (
                "fail" if "asserts_complete_answer_as_unique_answer" in result["rule_findings"]
                else judgment["label"]
            )
            result["judge"] = judgment
        except (EvaluationError, ProviderError) as error:
            if strict:
                raise
            result.update(final_label="error", error=f"{type(error).__name__}: {error}")
        items.append(result)

    counts = {label: sum(item.get("final_label") == label for item in items)
              for label in ("pass", "fail", "partial", "error")}
    total = len(dataset_by_id)
    report = {
        "schema_version": "1.0",
        "evaluation": {"model": model, "rubric": prompt, "dataset": str(dataset), "answers": str(answers)},
        "summary": {"total": total, **counts, "pass_rate": counts["pass"] / total if total else 0.0,
                    "alignment_errors": len(alignment_errors)},
        "by_label": counts,
        "recommendations": _recommendations(counts),
        "alignment_errors": alignment_errors,
        "items": items,
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(".items.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8"
    )
    return report


def _recommendations(counts: dict[str, int]) -> list[str]:
    recommendations = []
    if counts["fail"]:
        recommendations += ["Review failed cases for unsupported single-answer assumptions.",
                            "Add clarification or uncertainty-handling instructions to the agent prompt."]
    if counts["partial"]:
        recommendations.append("Teach the agent to state all supported possibilities instead of choosing one.")
    if counts["error"]:
        recommendations.append("Investigate missing, duplicate, or malformed answer records.")
    return recommendations or ["No recurring failure pattern was detected in this run."]
