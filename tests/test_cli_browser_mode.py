"""Browser CLI has one guided workflow; project interaction options stay separate."""
import sys

import pytest

from lladar.cli import build_parser, main
from fixture_extraction import browser_cli_terminal


def test_browser_resume_force_conflict_is_not_hidden_as_browser_failure(tmp_path, capsys):
    import json
    from lladar.work_session import digest
    session = tmp_path/'work'
    session.mkdir()
    data = {'configuration': {'lang': 'en'}}
    (session/'session.json').write_text(json.dumps({'data': data, 'sha256': digest(data)}), encoding='utf-8')
    with browser_cli_terminal():
        assert main(['run-agent', 'missing.jsonl', '--page-url', 'https://fixture.test/chat',
                     '--resume', str(session), '--force']) == 2
    message = capsys.readouterr().err
    assert '--resume and --force cannot be combined' in message
    assert 'Browser operation failed' not in message


@pytest.mark.parametrize("flag", ["--interactive", "--no-interactive"])
def test_browser_rejects_project_interaction_flags_before_any_work(flag, capsys):
    with pytest.raises(SystemExit) as error:
        main(["run-agent", "missing.jsonl", "--page-url", "https://fixture.test/chat", flag],
             browser_target_factory=lambda **_: pytest.fail("browser must not open"),
             extraction_provider_factory=lambda **_: pytest.fail("model must not initialize"))
    assert error.value.code == 2
    assert "omit both with --page-url" in capsys.readouterr().err


@pytest.mark.parametrize("stdin_tty,stderr_tty", [(False, True), (True, False), (False, False)])
def test_browser_requires_a_terminal_before_reading_dataset_or_opening_browser(
    monkeypatch, capsys, stdin_tty, stderr_tty
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: stderr_tty)
    with pytest.raises(SystemExit) as error:
        main(["run-agent", "missing.jsonl", "--page-url", "https://fixture.test/chat"],
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


def test_demonstration_question_is_explicit_and_old_browser_flags_are_removed():
    parser = build_parser()
    args = parser.parse_args(["run-agent", "dataset.jsonl", "--page-url", "https://fixture.test/chat",
                              "--demo-question", "What assistance is available?", "--model", "ollama:fixture",
                              "--max-input-tokens", "131072", "--max-output-tokens", "65536"])
    assert args.demo_question == "What assistance is available?"
    assert args.model == "ollama:fixture"
    assert args.max_input_tokens == 131072 and args.max_output_tokens == 65536
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["run-agent", "dataset.jsonl", "--calibration-question", "old flow"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        parser.parse_args(['run-agent','dataset.jsonl','--probe-question','old flow'])


def test_page_url_can_explicitly_reuse_the_demonstrated_conversation():
    args = build_parser().parse_args([
        'run-agent', 'dataset.jsonl', '--page-url', 'https://example.test/chat',
        '--conversation-policy', 'demonstrated',
    ])
    assert args.conversation_policy == 'demonstrated'


def test_help_explains_autonomous_discovery_and_independent_probe_scope(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["run-agent", "--help"])
    assert caught.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for text in ("python -m playwright install chromium", "material Agent", "adapter Agent",
                 "--demo-question", "two fresh verification questions", "close the browser",
                 "--fresh-browser-profile", "--max-input-tokens", "--max-output-tokens"):
        assert text in help_text
    assert "N+2" not in help_text
    assert "submit the exact calibration question" not in help_text
