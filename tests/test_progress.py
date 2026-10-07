import io
import re

import pytest

from lladar.command_log import command_log
from lladar.progress import ProgressReporter


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("enabled", [True, False])
def test_details_are_colored_on_terminal_and_flushed_to_plain_log(tmp_path, monkeypatch, enabled):
    terminal = Terminal()
    monkeypatch.setattr("sys.stderr", terminal)
    monkeypatch.delenv("NO_COLOR", raising=False)
    log = tmp_path / "progress.log"
    with command_log(log):
        reporter = ProgressReporter(enabled)
        reporter.record({"question": "First line\nSecond line", "expected_answer": "Expected",
                         "actual_response": None}, context="record=1")
        before = log.read_text(encoding="utf-8")
        assert ("Second line" in before) == enabled
        assert "[RESPONSE]" not in before
        reporter.answer("RESPONSE", "Actual\ncontinued")
        # The file is readable before the command finishes.
        logged = log.read_text(encoding="utf-8")
        assert ("continued" in logged) == enabled
    output = terminal.getvalue()
    if enabled:
        assert "\x1b[36mFirst line\nSecond line\x1b[0m" in output
        assert "\x1b[32mExpected\x1b[0m" in output
        assert "\x1b[35mActual\ncontinued\x1b[0m" in output
    else:
        assert output == ""
    assert "\x1b" not in logged
    assert re.sub(r"\x1b\[[0-9;]*m", "", output) == logged


def test_no_color_and_non_terminal_output(monkeypatch):
    for stream in (Terminal(), io.StringIO()):
        monkeypatch.setenv("NO_COLOR", "1")
        reporter = ProgressReporter(stream=stream)
        reporter.record({"question": "Q", "expected_answer": "A", "actual_response": "R"}, context="test")
        assert "\x1b" not in stream.getvalue()
        assert "[RESPONSE] R" in stream.getvalue()
