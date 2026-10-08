import io
import re
from threading import Event, enumerate as threads

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


@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_waiting_heartbeat_is_visible_during_work_and_stops_on_exit(failure):
    heartbeat_seen = Event()

    class LiveStream(io.StringIO):
        def write(self, value):
            result = super().write(value)
            if "still working" in value:
                heartbeat_seen.set()
            return result

    stream = LiveStream()
    reporter = ProgressReporter(stream=stream)

    def work():
        with reporter.waiting("Extracting answer", interval=0.01):
            assert "Extracting answer started" in stream.getvalue()
            assert heartbeat_seen.wait(2), "No progress while operation is blocked"
            if failure is not None:
                raise failure("private-provider-details")

    if failure is None:
        work()
    else:
        with pytest.raises(failure):
            work()
    status = "finished" if failure is None else "interrupted"
    assert f"Extracting answer {status}" in stream.getvalue()
    assert "elapsed=" in stream.getvalue()
    assert "private-provider-details" not in stream.getvalue()
    assert not any(thread.name == "lladar-progress" for thread in threads())


def test_disabled_waiting_runs_without_messages_or_background_thread():
    stream = io.StringIO()
    with ProgressReporter(False, stream).waiting("Extracting answer"):
        assert not any(thread.name == "lladar-progress" for thread in threads())
    assert stream.getvalue() == ""


def test_all_progress_events_keep_timestamp_and_monotonic_elapsed_in_log(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("lladar.progress.time.perf_counter", lambda: clock[0])
    terminal = io.StringIO()
    monkeypatch.setattr("sys.stderr", terminal)
    log = tmp_path / "timed.log"
    prefix = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{4} \[\+(\d+\.\d{3})s\] "
    with command_log(log):
        reporter = ProgressReporter()
        reporter.configuration({"model": "fixture"})
        clock[0] += 12.345
        reporter.record({"question": "Q", "expected_answer": "A"}, context="record=1")
        clock[0] += 2.5
        reporter.answer("RESPONSE", "R")
        reporter.done(1)
        saved = log.read_text(encoding="utf-8")
    assert saved == terminal.getvalue()
    elapsed = []
    for line in saved.splitlines():
        match = re.match(prefix, line)
        assert match is not None, line
        elapsed.append(float(match.group(1)))
    assert elapsed == [0.0, 0.0, 12.345, 12.345, 12.345, 14.845, 14.845]
