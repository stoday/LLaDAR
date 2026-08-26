import json
from pathlib import Path

import lladar


class FakeJudge:
    def __init__(self, labels):
        self.labels = iter(labels)

    def generate_structured(self, prompt, *, model, temperature):
        return {
            "label": next(self.labels),
            "score": 0.9,
            "rationale": "The answer was compared with the missing-information rule.",
            "unsupported_assumption": None,
            "recommendation": None,
        }


def write_jsonl(path: Path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def test_eval_matches_by_id_and_writes_report_and_item_jsonl(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    report = tmp_path / "reports" / "evaluation.json"
    write_jsonl(dataset, [{
        "id": "item-1",
        "underspecified_question": "誰是負責人？",
        "complete_answer": "甲",
        "missing_information": "角色",
        "invalid_assumptions": ["假設是甲"],
        "acceptable_behaviors": ["ask_clarification"],
    }])
    write_jsonl(answers, [{"id": "item-1", "answer": "資訊不足，請補充角色。"}])

    result = lladar.eval(
        dataset, answers, prompt="不能自行補充缺失資訊", output=report,
        provider=FakeJudge(["pass"]),
    )

    assert result["summary"]["pass"] == 1
    assert json.loads(report.read_text(encoding="utf-8"))["items"][0]["final_label"] == "pass"
    assert report.with_suffix(".items.jsonl").is_file()


def test_eval_uses_rule_to_fail_a_unique_complete_answer(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{
        "id": "item-1", "underspecified_question": "答案？", "complete_answer": "甲",
    }])
    write_jsonl(answers, [{"id": "item-1", "answer": "甲"}])

    result = lladar.eval(
        dataset, answers, prompt="避免 unsupported assumption", output=tmp_path / "report.json",
        provider=FakeJudge(["pass"]),
    )

    assert result["summary"]["fail"] == 1


def test_eval_excludes_skipped_items_and_reports_their_count(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            {
                "id": "ready-1",
                "status": "ready",
                "underspecified_question": "question",
                "complete_answer": "complete",
            },
            {
                "id": "skipped-1",
                "status": "skipped",
                "source_text": "source",
                "reason": "not suitable",
            },
        ],
    )
    write_jsonl(answers, [{"id": "ready-1", "answer": "not enough information"}])

    result = lladar.eval(
        dataset,
        answers,
        prompt="acknowledge missing information",
        output=tmp_path / "report.json",
        provider=FakeJudge(["pass"]),
    )

    assert result["summary"]["total"] == 1
    assert result["summary"]["skipped"] == 1
    assert result["summary"]["alignment_errors"] == 0
    assert [item["id"] for item in result["items"]] == ["ready-1"]
