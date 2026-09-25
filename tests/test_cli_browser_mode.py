"""Browser CLI has one guided workflow; project interaction options stay separate."""
import sys

import pytest

from lladar.cli import main
from fixture_extraction import browser_cli_terminal


@pytest.mark.parametrize("flag", ["--interactive", "--no-interactive"])
def test_browser_rejects_project_interaction_flags_before_any_work(flag, capsys):
    with pytest.raises(SystemExit) as error:
        main(["run-agent", "missing.jsonl", "--page-url", "https://fixture.test/chat", flag],
             browser_target_factory=lambda **_: pytest.fail("browser must not open"),
             extraction_provider_factory=lambda **_: pytest.fail("model must not initialize"))
    assert error.value.code == 2
    assert "omit both with --page-url" in capsys.readouterr().err


@pytest.mark.parametrize("stdin_tty,stderr_tty", [(False, True), (True, False), (False, False)])
@pytest.mark.parametrize("preapproved", [False, True])
def test_browser_requires_a_terminal_before_reading_dataset_or_opening_browser(
    monkeypatch, capsys, stdin_tty, stderr_tty, preapproved
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: stderr_tty)
    flags = ["--confirm-browser-run", "--allow-response-model-transfer"] if preapproved else []
    with pytest.raises(SystemExit) as error:
        main(["run-agent", "missing.jsonl", "--page-url", "https://fixture.test/chat", *flags],
             browser_target_factory=lambda **_: pytest.fail("browser must not open"),
             extraction_provider_factory=lambda **_: pytest.fail("model must not initialize"))
    assert error.value.code == 2
    assert "requires terminal stdin and stderr" in capsys.readouterr().err


@pytest.mark.parametrize("flags,expected", [([], None), (["--interactive"], True), (["--no-interactive"], False)])
def test_project_mode_preserves_its_interaction_setting(monkeypatch, flags, expected):
    import lladar.cli as cli
    received = []

    def run_agent(*args, **options):
        received.append(options)
        return 0

    monkeypatch.setattr(cli, "run_agent", run_agent)
    assert main(["run-agent", "dataset.jsonl", "--project", ".", *flags]) == 0
    assert len(received) == 1 and received[0]["interactive"] is expected


def test_page_url_selects_guided_workflow_without_interaction_flags(monkeypatch):
    import lladar.cli as cli
    received = []

    def run_agent(*args, **options):
        received.append(options)
        return 0

    monkeypatch.setattr(cli, "run_agent", run_agent)
    with browser_cli_terminal():
        assert main(["run-agent", "dataset.jsonl", "--page-url", "https://fixture.test/chat"]) == 0
    assert len(received) == 1 and received[0]["interactive"] is True
    assert received[0]["project"] is None
