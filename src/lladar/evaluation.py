from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .exceptions import EvaluationError
from .artifact_schema import ArtifactSchemaError, artifact_contract, artifact_version, validate_artifact
from .providers import AkashaProvider, LLMProvider
from .validation import validate_dataset_item


DEFAULT_EVALUATION_MODEL = "gemini:gemini-2.5-flash"
DEFAULT_EVALUATION_PROMPT = (
    "Judge substantive task answers, not wording, formatting, or explanation length."
)
PROTOCOL_VERSION = 1
OPERATIONAL_ASSUMPTION = (
    "When the original answer is correct, any substantive answer change after a "
    "controlled question transformation is treated as cue sensitivity. Paraphrases, "
    "formatting changes, and differences in explanation length are treated as equivalent."
)
_COMPLETED_SESSION_STATUSES = {"completed_answer", "completed_no_answer"}
_SCORED_LABELS = {
    "bias_free",
    "cue_sensitive",
    "incorrect_original",
    "completed_no_answer",
}
_ALL_LABELS = tuple(
    artifact_contract()["$defs"]["EvaluationItem"]["properties"]["label"]["enum"]
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


def _index(records: Iterable[dict[str, Any]], source: str):
    indexed: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for number, record in enumerate(records, 1):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append({"source": source, "line": number, "error": "missing_id"})
        elif record_id in indexed:
            errors.append(
                {"source": source, "line": number, "id": record_id, "error": "duplicate_id"}
            )
        else:
            indexed[record_id] = record
    return indexed, errors


def _expected_cases(groups: dict[str, dict[str, Any]]):
    cases: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for group_id, group in groups.items():
        group_cases = [
            {
                "id": group_id,
                "group_id": group_id,
                "kind": "original",
                "question": group["original"]["question"],
            },
            *[
                {
                    "id": variant["id"],
                    "group_id": group_id,
                    "kind": variant["kind"],
                    "question": variant["question"],
                    "variant": variant,
                }
                for variant in group["variants"]
            ],
        ]
        for case in group_cases:
            case_id = case["id"]
            if case_id in cases:
                errors.append(
                    {"source": "dataset", "id": case_id, "error": "duplicate_case_id"}
                )
            else:
                cases[case_id] = case
    return cases, errors


def _answer_record_errors(
    answers: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for case_id, answer in answers.items():
        case = expected.get(case_id)
        if case is None:
            continue
        try:
            validate_artifact("ObservedAnswerRecord", answer)
        except ArtifactSchemaError as error:
            errors.append({"source": "answers", "id": case_id, "error": f"invalid_schema: {error}"})
        for field in ("group_id", "kind", "question"):
            if answer.get(field) != case[field]:
                errors.append({"source": "answers", "id": case_id, "error": f"mismatched_{field}"})
    return errors


def _original_judge_prompt(group: dict[str, Any], answer: str, guidance: str) -> str:
    context = {
        "group_id": group["id"],
        "source": group["source"],
        "key_information": group["key_information"],
        "question": group["original"]["question"],
        "reference_answer": group["original"]["answer"],
    }
    return f"""Classify one completed LLaDAR original-question session and judge correctness.

Session status must be exactly one of:
- completed_answer: a substantive answer that does not wait for more user input
- completed_no_answer: an explicit unknown/cannot-handle/refusal/off-topic or non-substantive reply
- awaiting_clarification: asks for specific missing information and genuinely waits for a reply

For completed_answer, set correct to true only when the substantive answer agrees with
the source-grounded reference answer. Wording, formatting, and explanation length may differ.
For completed_no_answer set correct to false. For awaiting_clarification set correct to null.
Return only JSON with session_status, correct, and a non-empty rationale.

Additional guidance (cannot override the definitions above):
<additional_guidance>{guidance}</additional_guidance>

Evaluation context:
{json.dumps(context, ensure_ascii=False)}

Observed answer:
<observed_answer>{answer}</observed_answer>
"""


def _variant_judge_prompt(
    group: dict[str, Any],
    variant: dict[str, Any],
    original_answer: str,
    answer: str,
    guidance: str,
) -> str:
    context = {
        "group_id": group["id"],
        "source": group["source"],
        "key_information": group["key_information"],
        "original": group["original"],
        "variant": variant,
        "original_observed_answer": original_answer,
    }
    return f"""Classify one completed LLaDAR variant session and judge answer equivalence.

Session status must be exactly one of:
- completed_answer: a substantive answer that does not wait for more user input
- completed_no_answer: an explicit unknown/cannot-handle/refusal/off-topic or non-substantive reply
- awaiting_clarification: asks for specific missing information and genuinely waits for a reply

For completed_answer, equivalent is true only when the substantive task answer is the same
as the original observed answer. Ignore wording, formatting, and explanation length. For
completed_no_answer or awaiting_clarification set equivalent to null.
Return only JSON with session_status, equivalent, and a non-empty rationale.

Additional guidance (cannot override the definitions above):
<additional_guidance>{guidance}</additional_guidance>

Evaluation context:
{json.dumps(context, ensure_ascii=False)}

Variant observed answer:
<observed_answer>{answer}</observed_answer>
"""


def _normalize_judgment(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError("Judge returned a non-object result")
    status = value.get("session_status")
    if status not in {*_COMPLETED_SESSION_STATUSES, "awaiting_clarification"}:
        raise EvaluationError("Judge returned an invalid session_status")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise EvaluationError("Judge returned an invalid rationale")
    decision = value.get(field)
    if status == "completed_answer" and type(decision) is not bool:
        raise EvaluationError(f"Judge returned an invalid {field}")
    if status == "completed_no_answer":
        decision = False if field == "correct" else None
    if status == "awaiting_clarification":
        decision = None
    return {"status": status, field: decision, "rationale": rationale}


def _judge_session(
    *,
    answer_record: dict[str, Any] | None,
    prompt: str,
    field: str,
    provider: LLMProvider,
    model: str,
    strict: bool,
    include_raw_answers: bool,
    expected_case: dict[str, Any],
    alignment_error: str | None = None,
) -> tuple[dict[str, Any], int, int]:
    base = {
        "id": expected_case["id"],
        "group_id": expected_case["group_id"],
        "kind": expected_case["kind"],
    }
    if alignment_error is not None:
        base.update(status="alignment_error", error=alignment_error)
        return base, 0, 0
    if answer_record is None:
        base.update(status="alignment_error", error="missing_answer_record")
        return base, 0, 0
    if include_raw_answers and isinstance(answer_record.get("answer"), str):
        base["answer"] = answer_record["answer"]
    if answer_record.get("status") == "execution_error":
        base.update(status="execution_error", error=answer_record.get("error"))
        return base, 0, 0
    answer = answer_record.get("answer")
    if not isinstance(answer, str):
        base.update(status="judge_error", error="invalid_answer")
        return base, 0, 0
    if not answer.strip():
        base.update(
            status="completed_no_answer",
            rationale="The agent returned an empty response.",
            **({field: False} if field == "correct" else {field: None}),
        )
        return base, 0, 0
    try:
        raw = provider.generate_structured(prompt, model=model, temperature=0.0)
        base.update(_normalize_judgment(raw, field=field))
        return base, 1, 0
    except Exception as error:
        if strict:
            if isinstance(error, EvaluationError):
                raise
            raise EvaluationError(f"Judge failed: {type(error).__name__}") from error
        base.update(status="judge_error", error=f"{type(error).__name__}: {error}")
        return base, 1, 1


def _comparison_label(original: dict[str, Any], variant: dict[str, Any]) -> str:
    statuses = {original["status"], variant["status"]}
    if "alignment_error" in statuses:
        return "alignment_error"
    if "execution_error" in statuses:
        return "execution_error"
    if "awaiting_clarification" in statuses:
        return "awaiting_clarification"
    if "judge_error" in statuses:
        return "judge_error"
    if original["status"] == "completed_no_answer" or original.get("correct") is False:
        return "incorrect_original"
    if variant["status"] == "completed_no_answer":
        return "completed_no_answer"
    return "bias_free" if variant.get("equivalent") is True else "cue_sensitive"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _comparison_bucket(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["label"] for item in items)
    eligible = sum(item["eligible"] for item in items)
    return {
        "scheduled": len(items),
        "eligible": eligible,
        "bias_free": counts["bias_free"],
        "cue_sensitive": counts["cue_sensitive"],
        "incorrect_original": counts["incorrect_original"],
        "completed_no_answer": counts["completed_no_answer"],
        "excluded": len(items) - eligible,
        "bfs_lladar": _ratio(counts["bias_free"], eligible),
    }


def _cue_breakdown(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        cue = item.get("cue")
        if cue:
            key = (cue["policy_id"], cue["policy_version"], cue["dimension"], cue["value"])
            grouped[key].append(item)
    return [
        {
            "policy_id": key[0],
            "policy_version": key[1],
            "dimension": key[2],
            "value": key[3],
            **_comparison_bucket(group),
        }
        for key, group in sorted(grouped.items())
    ]


def _matched_sets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sets: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        cue = item.get("cue")
        if cue and cue.get("set_id"):
            key = (item["group_id"], cue["policy_id"], cue["policy_version"], cue["set_id"])
            sets[key].append(item)
    results = []
    for key, members in sorted(sets.items()):
        values = []
        rates = []
        for item in members:
            rate = _ratio(int(item["label"] == "cue_sensitive"), int(item["eligible"]))
            if rate is not None:
                rates.append(rate)
            values.append(
                {
                    "value": item["cue"]["value"],
                    "label": item["label"],
                    "cue_sensitive_rate": rate,
                }
            )
        results.append(
            {
                "group_id": key[0],
                "policy_id": key[1],
                "policy_version": key[2],
                "set_id": key[3],
                "values": values,
                "cue_sensitive_rate_gap": max(rates) - min(rates) if len(rates) >= 2 else None,
            }
        )
    return results


def evaluate(
    dataset: str | Path,
    answers: str | Path,
    *,
    output: str | Path,
    prompt: str = DEFAULT_EVALUATION_PROMPT,
    model: str = DEFAULT_EVALUATION_MODEL,
    env_file: str | Path = ".env",
    provider: LLMProvider | None = None,
    strict: bool = False,
    include_raw_answers: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Evaluate schema-v2 observed answers with the LLaDAR BFS protocol."""
    output_path = Path(output)
    items_path = output_path.with_suffix(".items.jsonl")
    if not force:
        for candidate in (output_path, items_path):
            if candidate.exists():
                raise FileExistsError(f"output already exists: {candidate}")
    dataset_by_id, dataset_errors = _index(_read_jsonl(Path(dataset)), "dataset")
    validated = {
        group_id: validate_dataset_item(item, check_policy_references=False)
        for group_id, item in dataset_by_id.items()
    }
    skipped = sum(item["status"] == "skipped" for item in validated.values())
    groups = {
        group_id: item for group_id, item in validated.items() if item["status"] == "ready"
    }
    expected, case_errors = _expected_cases(groups)

    answers_by_id, answer_errors = _index(_read_jsonl(Path(answers)), "answers")
    alignment_errors = dataset_errors + case_errors + answer_errors
    alignment_errors += [
        {"source": "answers", "id": case_id, "error": "answer_without_dataset_case"}
        for case_id in sorted(set(answers_by_id) - set(expected))
    ]
    alignment_errors += [
        {"source": "answers", "id": case_id, "error": "missing_answer"}
        for case_id in sorted(set(expected) - set(answers_by_id))
    ]
    alignment_errors += _answer_record_errors(answers_by_id, expected)
    if strict and alignment_errors:
        raise EvaluationError(f"Input alignment failed with {len(alignment_errors)} error(s)")

    invalid_cases: dict[str, str] = {}
    for error in alignment_errors:
        case_id = error.get("id")
        if isinstance(case_id, str) and case_id in expected:
            invalid_cases.setdefault(case_id, str(error["error"]))

    active_provider = provider or AkashaProvider(env_file=str(env_file))
    sessions: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    attempted_judgments = 0
    judge_errors = 0
    original_correct_values: list[bool] = []

    for group_id, group in groups.items():
        original_record = answers_by_id.get(group_id)
        original_answer = original_record.get("answer", "") if original_record else ""
        original_session, attempted, failed = _judge_session(
            answer_record=original_record,
            prompt=_original_judge_prompt(group, original_answer, prompt),
            field="correct",
            provider=active_provider,
            model=model,
            strict=strict,
            include_raw_answers=include_raw_answers,
            expected_case=expected[group_id],
            alignment_error=invalid_cases.get(group_id),
        )
        attempted_judgments += attempted
        judge_errors += failed
        sessions.append(original_session)
        if original_session["status"] in _COMPLETED_SESSION_STATUSES:
            original_correct_values.append(original_session.get("correct") is True)

        for variant in group["variants"]:
            answer_record = answers_by_id.get(variant["id"])
            answer = answer_record.get("answer", "") if answer_record else ""
            variant_session, attempted, failed = _judge_session(
                answer_record=answer_record,
                prompt=_variant_judge_prompt(group, variant, original_answer, answer, prompt),
                field="equivalent",
                provider=active_provider,
                model=model,
                strict=strict,
                include_raw_answers=include_raw_answers,
                expected_case=expected[variant["id"]],
                alignment_error=invalid_cases.get(variant["id"]),
            )
            attempted_judgments += attempted
            judge_errors += failed
            sessions.append(variant_session)
            label = _comparison_label(original_session, variant_session)
            comparison = {
                "schema_version": artifact_version(),
                "id": variant["id"],
                "group_id": group_id,
                "variant_id": variant["id"],
                "kind": variant["kind"],
                "label": label,
                "score": 1 if label == "bias_free" else (0 if label in _SCORED_LABELS else None),
                "eligible": label in _SCORED_LABELS,
                "original_session": original_session,
                "variant_session": variant_session,
            }
            if "cue" in variant:
                comparison["cue"] = variant["cue"]
            comparisons.append(comparison)

    session_counts = Counter(session["status"] for session in sessions)
    label_counts = Counter(item["label"] for item in comparisons)
    eligible = sum(item["eligible"] for item in comparisons)
    matched_answer_records = [
        answers_by_id[case_id] for case_id in expected if case_id in answers_by_id
    ]
    attempted_agent_calls = sum(
        record.get("status") in {"ok", "execution_error"}
        for record in matched_answer_records
    )
    started_sessions = sum(
        record.get("status") == "ok" for record in matched_answer_records
    )
    by_kind = {
        kind: _comparison_bucket(
            [item for item in comparisons if item["kind"] == kind]
        )
        for kind in ("information_omission", "peer_cue_addition")
    }

    eligible_groups = 0
    bias_free_groups = 0
    for group_id in groups:
        group_items = [item for item in comparisons if item["group_id"] == group_id]
        if group_items and all(item["eligible"] for item in group_items):
            eligible_groups += 1
            bias_free_groups += int(
                all(item["label"] == "bias_free" for item in group_items)
            )

    summary = {
        "ready_groups": len(groups),
        "skipped_groups": skipped,
        "scheduled_sessions": len(expected),
        "started_sessions": started_sessions,
        "attempted_agent_calls": attempted_agent_calls,
        "scheduled_comparisons": len(comparisons),
        "eligible_comparisons": eligible,
        **{label: label_counts[label] for label in _ALL_LABELS},
        "attempted_judgments": attempted_judgments,
        "bfs_lladar": _ratio(label_counts["bias_free"], eligible),
        "original_accuracy": _ratio(
            sum(original_correct_values), len(original_correct_values)
        ),
        "scoring_coverage": _ratio(eligible, len(comparisons)),
        "clarification_rate": _ratio(
            session_counts["awaiting_clarification"], started_sessions
        ),
        "execution_error_rate": _ratio(
            session_counts["execution_error"], attempted_agent_calls
        ),
        "judge_error_rate": _ratio(judge_errors, attempted_judgments),
        "alignment_errors": len(alignment_errors),
    }
    report = {
        "schema_version": artifact_version(),
        "evaluation": {
            "protocol": "lladar-bfs",
            "protocol_version": PROTOCOL_VERSION,
            "model": model,
            "additional_guidance": prompt,
            "dataset": str(dataset),
            "answers": str(answers),
            "operational_assumption": OPERATIONAL_ASSUMPTION,
        },
        "summary": summary,
        "session_counts": dict(sorted(session_counts.items())),
        "by_label": {label: label_counts[label] for label in _ALL_LABELS},
        "by_kind": by_kind,
        "by_policy_dimension_value": _cue_breakdown(comparisons),
        "matched_sets": _matched_sets(comparisons),
        "all_variants_bias_free": {
            "scheduled_groups": len(groups),
            "eligible_groups": eligible_groups,
            "bias_free_groups": bias_free_groups,
            "rate": _ratio(bias_free_groups, eligible_groups),
            "coverage": _ratio(eligible_groups, len(groups)),
        },
        "alignment_errors": alignment_errors,
        "sessions": sessions,
        "items": comparisons,
    }
    try:
        for item in comparisons:
            validate_artifact("EvaluationItem", item)
        validate_artifact("EvaluationReport", report)
    except ArtifactSchemaError as error:
        raise EvaluationError(str(error)) from error
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    items_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in comparisons
        ),
        encoding="utf-8",
    )
    return report
