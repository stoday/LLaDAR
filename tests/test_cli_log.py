"""Command logging preserves terminal behavior and output files."""

import sys

import pytest

from lladar.cli import build_parser, main
from fixture_extraction import browser_cli_terminal


@pytest.mark.parametrize("argv", [
    ["--log", "run.log", "create", "eval-skill"],
    ["create", "--log", "run.log", "eval-skill"],
    ["create", "eval-skill", "--log", "run.log"],
    ["eval", "responses.jsonl", "--log", "run.log"],
    ["report", "evaluation.json", "--log", "run.log"],
    ["create", "situation", "--instructions", "TEXT", "--stop-criteria", "STOP",
     "--max-turns", "2", "--output", "situation.json", "--log", "run.log"],
])
def test_log_option_is_available_at_command_levels(argv):
    assert build_parser().parse_args(argv).log == "run.log"


def test_handled_failure_is_logged_and_streams_are_restored(tmp_path, capsys):
    log = tmp_path / "failure.log"
    original_streams = sys.stdout, sys.stderr
    assert main([
        "create", "test-dataset", "--knowledge", str(tmp_path / "missing.md"),
        "--output", str(tmp_path / "dataset.jsonl"), "--log", str(log),
    ]) == 2

    error = capsys.readouterr().err
    assert "lladar:" in error
    assert error in log.read_text(encoding="utf-8")
    assert (sys.stdout, sys.stderr) == original_streams
    # A second invocation must not keep writing to the previous log.
    saved = log.read_bytes()
    assert main(["create", "eval-skill", "--output", str(tmp_path / "method")]) == 0
    assert log.read_bytes() == saved


def test_existing_log_is_preserved_before_workflow_starts(tmp_path, capsys):
    log, skill = tmp_path / "existing.log", tmp_path / "method"
    log.write_text("preserve\n", encoding="utf-8")
    assert main([
        "create", "eval-skill", "--output", str(skill), "--log", str(log), "--force",
    ]) == 2
    assert log.read_text(encoding="utf-8") == "preserve\n"
    assert not skill.exists()
    assert "lladar:" in capsys.readouterr().err


def test_browser_terminal_detection_and_cancellation_survive_logging(tmp_path, monkeypatch, capsys):
    import lladar.cli as cli

    log = tmp_path / "browser.log"
    original_streams = sys.stdout, sys.stderr

    def cancelled_run(*args, **kwargs):
        assert sys.stdin.isatty() and sys.stderr.isatty()
        print("Review remains visible", file=sys.stderr)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_agent", cancelled_run)
    with browser_cli_terminal():
        assert main([
            "run-agent", "dataset.jsonl", "--page-url", "https://example.test/chat",
            "--log", str(log),
        ]) == 130

    error = capsys.readouterr().err
    assert "Review remains visible" in error
    assert "Cancelled" in error
    assert error == log.read_text(encoding="utf-8")
    assert (sys.stdout, sys.stderr) == original_streams
