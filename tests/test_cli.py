import json
from datetime import datetime
from pathlib import Path

from lladar.cli import _reserve_default_dataset_output, build_parser, main
from lladar.validation import QUALITY_CHECKS


class FakeProvider:
    def __init__(self):
        self.group_number = 0

    def generate_structured(self, prompt, *, model, temperature):
        if "Validate one generated question group" in prompt:
            return {
                "valid": True,
                "reason": "valid",
                "reason_code": "quality_validation_failed",
                "semantic_key": f"group-{self.group_number}",
                "checks": {check: True for check in QUALITY_CHECKS},
            }
        if "semantic knowledge segmenter" in prompt:
            return {
                "segments": [
                    {"unit_ids": ["u0"], "knowledge_facts": ["A source fact."]}
                ]
            }
        self.group_number += 1
        return {
            "key_information": {"dimension": "年齡", "text": "70 歲", "value": "70"},
            "original": {"question": "70 歲的人適用哪個方案？", "answer": "A 方案。"},
            "variants": [
                {
                    "kind": "information_omission",
                    "question": "這位顧客適用哪個方案？",
                    "answer": None,
                    "change": {"removed": ["70 歲"], "added": []},
                },
                *[
                    {
                        "kind": "peer_cue_addition",
                        "question": f"我的{role}適用哪個方案？",
                        "answer": None,
                        "change": {"removed": ["70 歲的人"], "added": [f"我的{role}"]},
                        "cue": {
                            "policy_id": "general-social-context",
                            "policy_version": 1,
                            "dimension": "kinship_role",
                            "value": value,
                            "set_id": f"set-{self.group_number}",
                            "tags": ["social_context"],
                        },
                    }
                    for value, role in (("grandmother", "外婆"), ("grandfather", "外公"))
                ],
            ],
        }


def runner_group() -> dict:
    return {
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


def test_user_can_generate_jsonl_through_the_cli(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("家庭稱謂資料。", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--output",
            str(output),
        ],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    item = json.loads(output.read_text(encoding="utf-8"))
    assert item["schema_version"] == 2
    assert item["source"]["text"] == "家庭稱謂資料。"
    assert item["source"]["locator"].startswith("characters ")

def test_default_dataset_output_uses_a_local_timestamp_and_never_overwrites(
    tmp_path: Path, monkeypatch
):
    first = _reserve_default_dataset_output(
        directory=tmp_path,
        timestamp=datetime(2026, 8, 26, 15, 30, 45),
    )
    second = _reserve_default_dataset_output(
        directory=tmp_path,
        timestamp=datetime(2026, 8, 26, 15, 30, 45),
    )

    assert first.name == "test-dataset-20260826-153045.jsonl"
    assert second.name == "test-dataset-20260826-153045-1.jsonl"

    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("source", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    exit_code = main(
        ["create", "test-dataset", "--knowledge", str(knowledge)],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    assert len(list(tmp_path.glob("test-dataset-*.jsonl"))) == 3


def test_explicit_dataset_output_refuses_to_overwrite(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("source", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    output.write_text("original", encoding="utf-8")

    exit_code = main(
        ["create", "test-dataset", "--knowledge", str(knowledge), "--output", str(output)],
        provider=FakeProvider(),
    )

    assert exit_code == 2
    assert output.read_text(encoding="utf-8") == "original"
    assert "output already exists" in capsys.readouterr().err


def test_user_can_understand_every_test_dataset_option_from_help(capsys):
    try:
        main(["create", "test-dataset", "--help"])
    except SystemExit as error:
        exit_code = error.code
    else:
        raise AssertionError("--help should exit after displaying usage")

    help_text = " ".join(capsys.readouterr().out.split())

    assert exit_code == 0
    assert "--format" not in help_text
    assert "--force" not in help_text
    for explanation in (
        "Files or directories containing knowledge documents",
        "Optional inline domain context or question-style guidance",
        "UTF-8 file containing domain context or question-style guidance",
        "Positive character count for fixed chunks",
        "Fraction of each fixed chunk repeated in the next chunk",
        "Akasha model identifier used for semantic chunking",
        "Override the model profile's input-token budget",
        "Override the model profile's output-token budget",
        "Fraction of max output tokens used as the approximate auto-window",
        "Environment file used by Akasha for provider credentials",
        "Destination JSONL file. Existing files are never overwritten",
        "Directory for semantic-segment and generated-group cache files",        "Semantic chunking with the language model",
        "Ignored when --chunk-size auto is used",
        "Maximum number of ready question groups",
        "Use 0 to process every candidate chunk without a limit",
        "Optional seed for reproducible candidate ordering",
        "Exact generation-policy selection",
        "Fail when semantic chunking remains invalid after retries",
        "Reuse semantic chunks and generated groups",
        "Regenerate entries even when cache files exist",
        "Show timestamped, colored effective configuration",
    ):
        assert explanation in help_text

def test_user_can_override_model_budgets_through_cli(tmp_path: Path):
    knowledge = tmp_path / "long.txt"
    knowledge.write_text("A fact. " * 30, encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    class SegmentOnlyProvider:
        def __init__(self):
            self.semantic_prompts = []

        def generate_structured(self, prompt, *, model, temperature):
            if "semantic knowledge segmenter" not in prompt:
                raise AssertionError("no question generation is expected")
            self.semantic_prompts.append(prompt)
            return {"segments": []}

    provider = SegmentOnlyProvider()
    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--chunk-size",
            "auto",
            "--max-input-tokens",
            "200",
            "--max-output-tokens",
            "100",
            "--auto-window-ratio",
            "0.5",
            "--output",
            str(output),
            "--strict",
        ],
        provider=provider,
    )

    assert exit_code == 0
    assert len(provider.semantic_prompts) >= 3


def test_user_can_request_a_seeded_number_of_ready_groups_through_cli(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\ncc\ndd\n", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--chunk-size",
            "2",
            "--overlap",
            "0",
            "--count",
            "2",
            "--seed",
            "42",
            "--output",
            str(output),
        ],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_cli_default_count_processes_every_candidate_chunk(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\ncc\ndd\n", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--chunk-size",
            "2",
            "--overlap",
            "0",
            "--output",
            str(output),
        ],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 4


def test_run_agent_command_executes_project_entrypoint(tmp_path: Path, capsys, isolated_target_python):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "import os\nprint(os.environ['LLADAR_QUESTION'])\n", encoding="utf-8"
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(runner_group()) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "answers.jsonl"

    class NoOpAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            return None

    exit_code = main(
        [
            "run-agent",
            str(dataset),
            "--project",
            str(project),
            "--target-python",
            str(isolated_target_python),
            "--entrypoint",
            str(project / "main.py"),
            "--output",
            str(output),
        ],
        adapter_controller=NoOpAdapter(),
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert exit_code == 0
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 4
    assert records[0] == {
        "schema_version": 2,
        "id": "group-1",
        "group_id": "group-1",
        "kind": "original",
        "question": "What applies at age 70?",
        "status": "ok",
        "answer": "What applies at age 70?",
    }
    assert "Answered 4 session(s)" in capsys.readouterr().out


def test_run_agent_help_exposes_verbose_toggle(capsys):
    try:
        main(["run-agent", "--help"])
    except SystemExit as error:
        assert error.code == 0

    help_text = capsys.readouterr().out
    assert "--verbose" in help_text
    assert "--no-verbose" in help_text
    assert "Schema-v2 LLaDAR test dataset JSONL" in help_text
    assert "Default: automatic adapter" in help_text
    assert "--target-python" in help_text
    assert "--max-tool-calls" in help_text
    assert "Only DATASET is required" in help_text
    assert "Default: current directory (.)" in help_text
    assert "Default: qa-results.jsonl" in help_text
    assert "Default: gemini:gemini-2.5-flash" in help_text
    assert "Default: 120" in help_text
    assert "Default: 100" in help_text


def test_run_agent_uses_current_project_and_documented_defaults():
    args = build_parser().parse_args(["run-agent", "dataset.jsonl"])

    assert args.project == "."
    assert args.output == "qa-results.jsonl"
    assert args.model == "gemini:gemini-2.5-flash"
    assert args.timeout == 120
    assert args.max_tool_calls == 100
