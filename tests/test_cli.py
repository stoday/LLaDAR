import json
from pathlib import Path

from lladar.cli import main


class FakeProvider:
    def generate_structured(self, prompt, *, model, temperature):
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
    assert json.loads(output.read_text(encoding="utf-8"))["source_text"] == "家庭稱謂資料。"

def test_user_can_understand_every_test_dataset_option_from_help(capsys):
    try:
        main(["create", "test-dataset", "--help"])
    except SystemExit as error:
        exit_code = error.code
    else:
        raise AssertionError("--help should exit after displaying usage")

    help_text = " ".join(capsys.readouterr().out.split())

    assert exit_code == 0
    for explanation in (
        "Files or directories containing knowledge documents",
        "Built-in strategy name or custom generation instructions",
        "UTF-8 file containing custom generation instructions",
        "Positive character count for fixed chunks",
        "Fraction of each fixed chunk repeated in the next chunk",
        "Akasha model identifier used for semantic chunking",
        "Override the model profile's input-token budget",
        "Override the model profile's output-token budget",
        "Fraction of max output tokens used as the approximate auto-window",        "Output format: JSONL writes one object per line",
        "Environment file used by Akasha for provider credentials",
        "Allow overwriting an existing output file",
        "Directory for semantic and pair cache files",        "Semantic chunking with the language model",
        "Ignored when --chunk-size auto is used",
        "Number of question pairs generated per chunk",
        "Protects an existing output file unless --force is used",
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
