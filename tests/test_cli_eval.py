import json

from lladar.cli import main


class FakeJudge:
    def generate_structured(self, prompt, *, model, temperature):
        return {
            "label": "pass",
            "score": 1.0,
            "rationale": "The answer acknowledges missing information.",
            "unsupported_assumption": None,
            "recommendation": None,
        }


def test_eval_command_writes_report(tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    output = tmp_path / "report.json"
    dataset.write_text(json.dumps({
        "id": "item-1",
        "underspecified_question": "問題？",
        "complete_answer": "答案",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    answers.write_text(json.dumps({"id": "item-1", "answer": "資訊不足。"}, ensure_ascii=False) + "\n", encoding="utf-8")

    exit_code = main([
        "eval", str(dataset), str(answers),
        "--prompt", "不得自行補充資訊",
        "--output", str(output),
    ], provider=FakeJudge())

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["pass"] == 1
    assert "Evaluated 1 item(s)" in capsys.readouterr().out


def test_eval_command_help_describes_inputs(capsys):
    try:
        main(["eval", "--help"])
    except SystemExit as error:
        assert error.code == 0
    help_text = capsys.readouterr().out
    assert "Original test dataset JSONL" in help_text
    assert "Agent answer JSONL" in help_text
    assert "Evaluation rubric sent to the judge" in help_text
