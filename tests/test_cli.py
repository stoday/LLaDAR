import json
from datetime import datetime
from pathlib import Path

from lladar.cli import _reserve_default_dataset_output, main


class FakeProvider:
    def generate_structured(self, prompt, *, model, temperature):
        if "Judge this candidate contrastive pair" in prompt:
            return {"valid": True, "reason": "valid", "checks": {
                "standalone_question": True, "same_task": True,
                "source_supported_answer": True,
                "answer_determining_missing_fact": True,
                "multiple_supported_answers": True,
                "no_unresolved_references": True,
            }}
        if "semantic knowledge segmenter" in prompt:
            return {
                "segments": [
                    {"unit_ids": ["u0"], "knowledge_facts": ["A source fact."]}
                ]
            }
        return {
            "complete_question": "完整問題",
            "complete_answer": "完整答案",
            "underspecified_question": "缺失問題",
            "missing_information": "缺失資訊",
            "invalid_assumptions": ["錯誤假設"],
            "acceptable_behaviors": ["ask_clarification"],
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
    assert item["source_text"] == "家庭稱謂資料。"
    assert item["metadata"]["chunk_method"] == "semantic_auto"

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
        "Built-in strategy name or custom generation instructions",
        "UTF-8 file containing custom generation instructions",
        "Positive character count for fixed chunks",
        "Fraction of each fixed chunk repeated in the next chunk",
        "Akasha model identifier used for semantic chunking",
        "Override the model profile's input-token budget",
        "Override the model profile's output-token budget",
        "Fraction of max output tokens used as the approximate auto-window",
        "Environment file used by Akasha for provider credentials",
        "Destination JSONL file. Existing files are never overwritten",
        "Directory for semantic and pair cache files",        "Semantic chunking with the language model",
        "Ignored when --chunk-size auto is used",
        "Number of question pairs generated per chunk",
        "Fail the run when chunking or generation remains invalid after retries",
        "Reuse semantic chunks and generated pairs",
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


def test_user_can_select_a_random_number_of_pairs_through_cli(tmp_path: Path):
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
            "--random-select",
            "2",
            "--output",
            str(output),
        ],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_run_agent_command_executes_project_entrypoint(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "import os\nprint(os.environ['LLADAR_QUESTION'])\n", encoding="utf-8"
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"id": "item-1", "underspecified_question": "question"}) + "\n",
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
            "--entrypoint",
            "main.py",
            "--output",
            str(output),
        ],
        adapter_controller=NoOpAdapter(),
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "id": "item-1",
        "status": "ok",
        "answer": "question",
    }


def test_run_agent_help_exposes_verbose_toggle(capsys):
    try:
        main(["run-agent", "--help"])
    except SystemExit as error:
        assert error.code == 0

    help_text = capsys.readouterr().out
    assert "--verbose" in help_text
    assert "--no-verbose" in help_text
