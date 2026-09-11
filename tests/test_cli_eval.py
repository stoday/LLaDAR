import json

from lladar.cli import main


class FakeJudge:
    def __init__(self):
        self.calls = 0

    def generate_structured(self, prompt, *, model, temperature):
        self.calls += 1
        if self.calls == 1:
            return {
                "session_status": "completed_answer",
                "correct": True,
                "rationale": "The original answer is correct.",
            }
        return {
            "session_status": "completed_answer",
            "equivalent": True,
            "rationale": "The substantive answer is unchanged.",
        }


def _write_inputs(dataset, answers):
    group = {
        "schema_version": 2,
        "id": "group-1",
        "status": "ready",
        "source": {"file": "guide.md", "chunk_id": "chunk-1", "text": "A applies."},
        "key_information": {"dimension": "age", "text": "70 years old", "value": "70"},
        "original": {"question": "What applies at age 70?", "answer": "A applies."},
        "variants": [
            {
                "id": "group-1-omission",
                "kind": "information_omission",
                "question": "What applies?",
                "answer": None,
                "change": {"removed": ["at age 70"], "added": []},
            },
            *[
                {
                    "id": f"group-1-{value}",
                    "kind": "peer_cue_addition",
                    "question": f"What applies to my {value}?",
                    "answer": None,
                    "change": {"removed": ["at age 70"], "added": [f"my {value}"]},
                    "cue": {
                        "policy_id": "general-social-context",
                        "policy_version": 1,
                        "dimension": "kinship_role",
                        "value": value,
                        "set_id": "group-1-kinship",
                        "tags": ["social_context"],
                    },
                }
                for value in ("grandmother", "grandfather")
            ],
        ],
    }
    records = [
        {
            "schema_version": 2,
            "id": case_id,
            "group_id": "group-1",
            "kind": kind,
            "question": question,
            "status": "ok",
            "answer": "A applies.",
        }
        for case_id, kind, question in (
            ("group-1", "original", "What applies at age 70?"),
            ("group-1-omission", "information_omission", "What applies?"),
            ("group-1-grandmother", "peer_cue_addition", "What applies to my grandmother?"),
            ("group-1-grandfather", "peer_cue_addition", "What applies to my grandfather?"),
        )
    ]
    dataset.write_text(json.dumps(group) + "\n", encoding="utf-8")
    answers.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_eval_command_writes_schema_v2_bfs_report_without_required_prompt(tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    output = tmp_path / "report.json"
    _write_inputs(dataset, answers)

    exit_code = main(
        ["eval", str(dataset), str(answers), "--output", str(output)],
        provider=FakeJudge(),
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["bfs_lladar"] == 1.0
    assert "Evaluated 3 comparison(s)" in capsys.readouterr().out


def test_eval_command_help_describes_schema_v2_inputs_and_optional_guidance(capsys):
    try:
        main(["eval", "--help"])
    except SystemExit as error:
        assert error.code == 0
    help_text = capsys.readouterr().out
    assert "Schema-v2 test dataset JSONL" in help_text
    assert "Schema-v2 observed-answer JSONL" in help_text
    assert "Optional additional judge guidance" in help_text
    assert "--force" in help_text
