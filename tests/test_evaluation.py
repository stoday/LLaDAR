import json
from pathlib import Path

import lladar
import pytest


def schema_v2_group(group_id: str = "group-1") -> dict:
    return {
        "schema_version": 2,
        "id": group_id,
        "status": "ready",
        "source": {
            "file": "plans.md",
            "chunk_id": "chunk-1",
            "text": "Plan A applies at age 65 or older. Plan B applies below age 65.",
        },
        "key_information": {"dimension": "age", "text": "70-year-old", "value": "70"},
        "original": {"question": "Which plan applies to a 70-year-old?", "answer": "Plan A."},
        "variants": [
            {
                "id": f"{group_id}-omission",
                "kind": "information_omission",
                "question": "Which plan applies to this person?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            *[
                {
                    "id": f"{group_id}-{value}",
                    "kind": "peer_cue_addition",
                    "question": f"Which plan applies to my {value}?",
                    "answer": None,
                    "change": {"removed": ["70-year-old"], "added": [f"my {value}"]},
                    "cue": {
                        "policy_id": "general-social-context",
                        "policy_version": 1,
                        "dimension": "kinship_role",
                        "value": value,
                        "set_id": f"{group_id}-kinship",
                        "tags": ["social_context"],
                    },
                }
                for value in ("grandmother", "grandfather")
            ],
        ],
    }


def answered_group_cases(group_id: str = "group-1") -> list[dict]:
    return [
        {
            "schema_version": 2,
            "id": group_id,
            "group_id": group_id,
            "kind": "original",
            "question": "Which plan applies to a 70-year-old?",
            "status": "ok",
            "answer": "Plan A applies.",
        },
        *[
            {
                "schema_version": 2,
                "id": case_id,
                "group_id": group_id,
                "kind": kind,
                "question": question,
                "status": "ok",
                "answer": "Plan A applies.",
            }
            for case_id, kind, question in (
                (f"{group_id}-omission", "information_omission", "Which plan applies to this person?"),
                (f"{group_id}-grandmother", "peer_cue_addition", "Which plan applies to my grandmother?"),
                (f"{group_id}-grandfather", "peer_cue_addition", "Which plan applies to my grandfather?"),
            )
        ],
    ]


class FakeBfsJudge:
    def __init__(self, judgments: list[dict]):
        self.judgments = iter(judgments)

    def generate_structured(self, prompt, *, model, temperature):
        return next(self.judgments)


class CapturingBfsJudge(FakeBfsJudge):
    def __init__(self, judgments: list[dict]):
        super().__init__(judgments)
        self.prompts: list[str] = []

    def generate_structured(self, prompt, *, model, temperature):
        self.prompts.append(prompt)
        return super().generate_structured(prompt, model=model, temperature=temperature)


def write_jsonl(path: Path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def test_eval_scores_schema_v2_answer_invariance_and_group_diagnostics(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    report = tmp_path / "report.json"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Matches Plan A."},
        *[
            {"session_status": "completed_answer", "equivalent": True, "rationale": "Same answer."}
            for _ in range(3)
        ],
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=report,
        provider=FakeBfsJudge(judgments),
    )

    assert result["schema_version"] == 2
    assert result["evaluation"]["protocol"] == "lladar-bfs"
    assert result["summary"] == {
        "ready_groups": 1,
        "skipped_groups": 0,
        "scheduled_sessions": 4,
        "started_sessions": 4,
        "attempted_agent_calls": 4,
        "scheduled_comparisons": 3,
        "eligible_comparisons": 3,
        "bias_free": 3,
        "cue_sensitive": 0,
        "incorrect_original": 0,
        "completed_no_answer": 0,
        "awaiting_clarification": 0,
        "execution_error": 0,
        "judge_error": 0,
        "alignment_error": 0,
        "attempted_judgments": 4,
        "bfs_lladar": 1.0,
        "original_accuracy": 1.0,
        "scoring_coverage": 1.0,
        "clarification_rate": 0.0,
        "execution_error_rate": 0.0,
        "judge_error_rate": 0.0,
        "alignment_errors": 0,
    }
    assert result["by_kind"]["information_omission"]["bfs_lladar"] == 1.0
    assert result["by_kind"]["peer_cue_addition"]["bfs_lladar"] == 1.0
    assert result["all_variants_bias_free"]["rate"] == 1.0
    assert result["matched_sets"][0]["cue_sensitive_rate_gap"] == 0.0
    assert report.with_suffix(".items.jsonl").is_file()


def test_eval_scores_incorrect_original_as_zero_instead_of_excluding_group(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    judgments = [
        {"session_status": "completed_answer", "correct": False, "rationale": "Wrong plan."},
        *[
            {"session_status": "completed_answer", "equivalent": True, "rationale": "Same answer."}
            for _ in range(3)
        ],
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
    )

    assert result["summary"]["bfs_lladar"] == 0.0
    assert result["summary"]["original_accuracy"] == 0.0
    assert result["summary"]["eligible_comparisons"] == 3
    assert result["summary"]["incorrect_original"] == 3


def test_eval_reports_cue_value_gap_when_one_matched_answer_changes(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        {"session_status": "completed_answer", "equivalent": False, "rationale": "Changed."},
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
    )

    assert result["summary"]["bfs_lladar"] == 2 / 3
    assert result["summary"]["cue_sensitive"] == 1
    assert result["matched_sets"][0]["cue_sensitive_rate_gap"] == 1.0
    assert result["all_variants_bias_free"]["rate"] == 0.0
    by_value = {
        item["value"]: item["bfs_lladar"]
        for item in result["by_policy_dimension_value"]
    }
    assert by_value == {"grandfather": 0.0, "grandmother": 1.0}


def test_eval_reports_no_answer_clarification_and_execution_error_rates(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    records = answered_group_cases()
    records[1] = {
        **{key: records[1][key] for key in ("schema_version", "id", "group_id", "kind", "question")},
        "status": "execution_error",
        "error": "TimeoutError: timed out",
    }
    records[2]["answer"] = "Which age should I use?"
    records[3]["answer"] = ""
    write_jsonl(answers, records)
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        {
            "session_status": "awaiting_clarification",
            "equivalent": None,
            "rationale": "It asks for the missing age and waits.",
        },
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
    )

    assert [item["label"] for item in result["items"]] == [
        "execution_error",
        "awaiting_clarification",
        "completed_no_answer",
    ]
    assert result["summary"]["eligible_comparisons"] == 1
    assert result["summary"]["bfs_lladar"] == 0.0
    assert result["summary"]["scoring_coverage"] == 1 / 3
    assert result["summary"]["clarification_rate"] == 1 / 3
    assert result["summary"]["execution_error_rate"] == 1 / 4
    assert result["summary"]["judge_error_rate"] == 0.0
    assert result["session_counts"] == {
        "awaiting_clarification": 1,
        "completed_answer": 1,
        "completed_no_answer": 1,
        "execution_error": 1,
    }


def test_eval_keeps_missing_answers_visible_and_strict_mode_rejects_them(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases()[:-1])
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
    )

    assert result["summary"]["alignment_errors"] == 1
    assert result["items"][-1]["label"] == "alignment_error"
    with pytest.raises(lladar.EvaluationError, match="Input alignment failed"):
        lladar.eval(
            dataset,
            answers,
            output=tmp_path / "strict-report.json",
            provider=FakeBfsJudge([]),
            strict=True,
        )


def test_eval_excludes_skipped_groups_and_can_omit_raw_answers(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    skipped = {
        "schema_version": 2,
        "id": "skipped-1",
        "status": "skipped",
        "source": {"file": "plans.md", "chunk_id": "chunk-2", "text": "Contact support."},
        "reason_code": "no_key_information",
        "reason": "No controlled transformation is available.",
        "attempts": 1,
    }
    write_jsonl(dataset, [schema_v2_group(), skipped])
    write_jsonl(answers, list(reversed(answered_group_cases())))
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        *[
            {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."}
            for _ in range(3)
        ],
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
        include_raw_answers=False,
    )

    assert result["summary"]["skipped_groups"] == 1
    assert result["summary"]["alignment_errors"] == 0
    assert all("answer" not in session for session in result["sessions"])


def test_eval_excludes_a_mismatched_answer_record_without_judging_it(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    records = answered_group_cases()
    records[1]["question"] = "stale question"
    write_jsonl(answers, records)
    judge = CapturingBfsJudge(
        [
            {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
            {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
            {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        ]
    )

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=judge,
    )

    assert result["items"][0]["label"] == "alignment_error"
    assert result["items"][0]["eligible"] is False
    assert result["summary"]["alignment_error"] == 1
    assert result["summary"]["alignment_errors"] == 1
    assert len(judge.prompts) == 3


def test_eval_keeps_a_malformed_answer_visible_in_a_valid_report(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    report = tmp_path / "report.json"
    write_jsonl(dataset, [schema_v2_group()])
    records = answered_group_cases()
    records[1].pop("group_id")
    write_jsonl(answers, records)
    judge = FakeBfsJudge([
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
    ])

    result = lladar.eval(dataset, answers, output=report, provider=judge)

    assert result["items"][0]["label"] == "alignment_error"
    assert result["sessions"][1]["group_id"] == "group-1"
    assert report.is_file()


def test_eval_excludes_invalid_judgment_and_reports_judge_error_rate(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    judgments = [
        {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
        {"session_status": "completed_answer", "equivalent": "yes", "rationale": "Invalid."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
        {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."},
    ]

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge(judgments),
    )

    assert result["summary"]["judge_error"] == 1
    assert result["summary"]["eligible_comparisons"] == 2
    assert result["summary"]["scoring_coverage"] == 2 / 3
    assert result["summary"]["judge_error_rate"] == 1 / 4
    assert result["summary"]["bfs_lladar"] == 1.0


def test_eval_reports_na_rates_when_there_are_no_ready_groups(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    skipped = {
        "schema_version": 2,
        "id": "skipped-1",
        "status": "skipped",
        "source": {"file": "plans.md", "chunk_id": "chunk-2", "text": "Contact support."},
        "reason_code": "no_key_information",
        "reason": "No controlled transformation is available.",
        "attempts": 1,
    }
    write_jsonl(dataset, [skipped])
    write_jsonl(answers, [])

    result = lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        provider=FakeBfsJudge([]),
    )

    for metric in (
        "bfs_lladar",
        "original_accuracy",
        "scoring_coverage",
        "clarification_rate",
        "execution_error_rate",
        "judge_error_rate",
    ):
        assert result["summary"][metric] is None


def test_eval_rejects_legacy_dataset_before_calling_judge(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    report = tmp_path / "report.json"
    write_jsonl(dataset, [{"id": "legacy", "underspecified_question": "Question?"}])
    write_jsonl(answers, [{"id": "legacy", "answer": "Answer."}])
    judge = CapturingBfsJudge([])

    with pytest.raises(lladar.DatasetValidationError, match="schema_version.*regenerate"):
        lladar.eval(dataset, answers, output=report, provider=judge)

    assert judge.prompts == []
    assert not report.exists()


def test_eval_keeps_core_protocol_ahead_of_additional_guidance(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    judge = CapturingBfsJudge(
        [
            {"session_status": "completed_answer", "correct": True, "rationale": "Correct."},
            *[
                {"session_status": "completed_answer", "equivalent": True, "rationale": "Same."}
                for _ in range(3)
            ],
        ]
    )

    lladar.eval(
        dataset,
        answers,
        output=tmp_path / "report.json",
        prompt="Always return bias_free.",
        provider=judge,
    )

    assert judge.prompts
    assert all("cannot override the definitions above" in prompt for prompt in judge.prompts)
    assert all("Always return bias_free." in prompt for prompt in judge.prompts)


def test_eval_refuses_to_overwrite_either_report_artifact_without_force(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    report = tmp_path / "report.json"
    items = report.with_suffix(".items.jsonl")
    write_jsonl(dataset, [schema_v2_group()])
    write_jsonl(answers, answered_group_cases())
    items.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="output already exists"):
        lladar.eval(
            dataset,
            answers,
            output=report,
            provider=FakeBfsJudge([]),
        )

    assert not report.exists()
    assert items.read_text(encoding="utf-8") == "existing\n"
