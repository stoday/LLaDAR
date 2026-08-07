from __future__ import annotations

import io
import re
from pathlib import Path

import lladar
from lladar.cli import main


PAIR = {
    "complete_question": "If the limit is 10, what is the limit?",
    "complete_answer": "10",
    "underspecified_question": "What is the limit?",
    "missing_information": "Which limit applies.",
    "invalid_assumptions": ["The limit is 10."],
    "acceptable_behaviors": ["ask_clarification"],
}


class FakeProvider:
    def generate_structured(self, prompt, *, model, temperature):
        return PAIR


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_api_shows_timestamped_configuration_progress_and_eta_by_default(
    tmp_path: Path,
    capsys,
):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")

    lladar.create_test_dataset(knowledge=knowledge, provider=FakeProvider())

    progress = " ".join(capsys.readouterr().err.split())
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", progress)
    for expected in (
        "[CONFIG]",
        "knowledge=",
        "strategy=ambiguity",
        "chunk_size=2000",
        "overlap=0.1",
        "num_pairs=1",
        "model=gemini:gemini-2.5-flash",
        "max_input_tokens=1048576",
        "max_output_tokens=65536",
        "auto_window_ratio=0.8",
        "provider=FakeProvider",
        "[SOURCE]",
        "[CHUNK]",
        "[PAIR]",
        "1/1",
        "elapsed=",
        "ETA=",
        "[DONE]",
    ):
        assert expected in progress


def test_cli_colors_verbose_labels_on_a_tty(tmp_path: Path, monkeypatch):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    stderr = TtyBuffer()
    monkeypatch.setattr("sys.stderr", stderr)

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
    assert "\x1b[" in stderr.getvalue()
    assert "[CONFIG]" in stderr.getvalue()


def test_cli_can_disable_verbose_progress(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--output",
            str(output),
            "--no-verbose",
        ],
        provider=FakeProvider(),
    )

    assert exit_code == 0
    assert capsys.readouterr().err == ""

def test_api_reports_retry_cache_and_write_events(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    cache_dir = tmp_path / "cache"

    class RecoveringProvider:
        def __init__(self):
            self.failed = False

        def generate_structured(self, prompt, *, model, temperature):
            if not self.failed:
                self.failed = True
                raise lladar.ProviderError("temporary malformed response")
            return PAIR

    lladar.create_test_dataset(
        knowledge=knowledge,
        output=output,
        cache=True,
        cache_dir=cache_dir,
        provider=RecoveringProvider(),
    )
    first_progress = capsys.readouterr().err

    class UnavailableProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise AssertionError("pair cache should satisfy this run")

    lladar.create_test_dataset(
        knowledge=knowledge,
        cache=True,
        cache_dir=cache_dir,
        provider=UnavailableProvider(),
    )
    second_progress = capsys.readouterr().err

    assert "[RETRY]" in first_progress
    assert "[CACHE]" in first_progress and "miss" in first_progress
    assert "saved" in first_progress
    assert "[WRITE]" in first_progress
    assert "[CACHE]" in second_progress and "hit" in second_progress


def test_api_reports_each_semantic_window(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact. " * 20, encoding="utf-8")

    class SegmentProvider:
        def generate_structured(self, prompt, *, model, temperature):
            assert "semantic knowledge segmenter" in prompt
            return {"segments": []}

    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        max_output_tokens=20,
        auto_window_ratio=0.5,
        provider=SegmentProvider(),
        strict=True,
    )

    progress = capsys.readouterr().err
    assert "[WINDOW]" in progress
    assert "1/" in progress

def test_verbose_progress_does_not_echo_provider_secrets(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact.", encoding="utf-8")

    class SecretErrorProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise lladar.ProviderError("request failed with API key TOP-SECRET-VALUE")

    lladar.create_test_dataset(
        knowledge=knowledge,
        provider=SecretErrorProvider(),
    )

    progress = capsys.readouterr().err
    assert "[RETRY]" in progress
    assert "TOP-SECRET-VALUE" not in progress

def test_cli_does_not_echo_provider_secrets_on_strict_failure(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    class SecretErrorProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise lladar.ProviderError("request failed with API key CLI-SECRET-VALUE")

    exit_code = main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--output",
            str(output),
            "--strict",
        ],
        provider=SecretErrorProvider(),
    )

    stderr = capsys.readouterr().err
    assert exit_code == 2
    assert "provider generation failed" in stderr
    assert "CLI-SECRET-VALUE" not in stderr
