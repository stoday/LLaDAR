"""Enter reports work in progress without owning a human prompt's input."""
import io
from queue import Queue, Empty
from threading import enumerate as threads

import pytest

from lladar.command_log import command_log
from lladar.language import Language, language_scope
from lladar.live_status import (current_status, status_activity, status_queries,
                               status_user_input, _WindowsEnter)
from lladar.progress import ProgressReporter


class EnterQueue:
    def __init__(self):
        self.keys = Queue()
        self.read = 0

    def poll(self):
        try:
            self.keys.get_nowait()
        except Empty:
            return False
        self.read += 1
        return True

    def reset(self):
        pass


def test_each_enter_reports_before_blocked_work_finishes_and_flushes_log(tmp_path, monkeypatch):
    enter = EnterQueue()
    seen = Queue()

    class LiveStream(io.StringIO):
        def write(self, value):
            result = super().write(value)
            if "Current activity:" in value:
                seen.put(value)
            return result

    stream = LiveStream()
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: enter)
    with command_log(tmp_path / "run.log"), status_queries(True):
        with status_activity("Waiting for model response"):
            for _ in range(2):
                enter.keys.put(True)
                assert "Waiting for model response" in seen.get(timeout=2)
                # Work is still blocked in the active phase, not completed.
                assert "Current activity:" in (tmp_path / "run.log").read_text(encoding="utf-8")
        assert current_status() is not None
    assert current_status() is None
    assert not any(thread.name == "lladar-status-input" for thread in threads())


def test_nested_activities_restore_outer_stage_and_report_elapsed(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("lladar.live_status.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with status_queries(True):
        with status_activity("Copying target project"):
            clock[0] = 103.0
            with status_activity("Building project code graph"):
                clock[0] = 108.0
                current_status().report()
                assert "stage elapsed=5.0s" in stream.getvalue()
            current_status().report()
            assert "Copying target project; stage elapsed=8.0s" in stream.getvalue()


def test_prompt_handoff_never_polls_or_prints_and_preserves_queued_enter(monkeypatch):
    enter = EnterQueue()
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: enter)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with status_queries(True):
        reporter = ProgressReporter()
        other_reporter = ProgressReporter()
        before = stream.getvalue()
        with reporter.user_input(), status_user_input():
            enter.keys.put(True)
            current_status().poll()
            current_status().report()
            other_reporter.emit("BROWSER", "background while typing")
            assert enter.read == 0
            assert stream.getvalue() == before
            # This Enter belongs to the human prompt, not the monitor.
            assert enter.keys.get_nowait() is True
        enter.keys.put(True)
        current_status().poll()
        assert enter.read == 1
        assert "Current activity:" in stream.getvalue()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_monitor_stops_when_command_fails_or_is_cancelled(monkeypatch, failure):
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with pytest.raises(failure):
        with status_queries(True), status_activity("Waiting for target response"):
            raise failure("private exception")
    assert current_status() is None
    assert not any(thread.name == "lladar-status-input" for thread in threads())


def test_no_verbose_does_not_probe_or_read_stdin(monkeypatch):
    def forbidden():
        pytest.fail("No-verbose commands must not start keyboard polling")
    monkeypatch.setattr("lladar.live_status._enter_reader", forbidden)
    with status_queries(False):
        assert current_status() is None


def test_piped_input_is_not_read_or_monitored(monkeypatch):
    class ProtocolInput(io.StringIO):
        def read(self, *args):
            pytest.fail("Status monitor consumed the input protocol")
    monkeypatch.setattr("sys.stdin", ProtocolInput('{"request_id":"q1"}'))
    with status_queries(True):
        assert current_status() is None


def test_status_does_not_repeat_question_response_or_configuration_values(monkeypatch):
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with status_queries(True):
        reporter = ProgressReporter()
        reporter.record({"question": "private question", "expected_answer": "private expected",
                         "actual_response": "private response"}, context="case=1")
        reporter.configuration({"private": "private configuration"})
        boundary = len(stream.getvalue())
        current_status().report()
        assert "private" not in stream.getvalue()[boundary:]


def test_windows_reader_preserves_typed_answer_and_consumes_only_enter():
    class Console:
        keys = [(True, key, 1) for key in "YES\r"]
        def peek(self):
            return self.keys[0] if self.keys else None
        def take(self):
            self.keys.pop(0)
    console = Console()
    reader = _WindowsEnter(console)
    assert not reader.poll()
    assert "".join(key[1] for key in console.keys) == "YES\r"
    console.keys = [(False, "\r", 1), (True, "\r", 2)]
    assert reader.poll() and reader.poll()
    assert not reader.poll()


@pytest.mark.parametrize("enabled", [True, False])
def test_cli_installs_monitor_before_pre_session_work(tmp_path, monkeypatch, enabled):
    from lladar.cli import main
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    def execute(*args, **kwargs):
        with status_activity("Checking input files and session fingerprints"):
            assert not (tmp_path / "output.jsonl.work").exists()
            if enabled:
                current_status().report()
            else:
                assert current_status() is None
        return 0
    monkeypatch.setattr("lladar.cli._execute", execute)
    assert main(["run-agent", "dataset.jsonl", "--project", "project", "--log", "run.log",
                 "--verbose" if enabled else "--no-verbose"]) == 0
    saved = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert ("Press Enter" in saved) == enabled
    assert ("Current activity: Checking input files" in saved) == enabled


@pytest.mark.parametrize("lang,expected", [
    ("zh-TW", "目前階段：等待模型回覆"), ("zh-CN", "当前阶段：等待模型回复"),
    ("ja", "現在の処理：モデルの応答を待機中"), ("ko", "현재 작업: 모델 응답 대기 중")])
def test_status_uses_command_language(monkeypatch, lang, expected):
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    with language_scope(Language(lang)), status_queries(True), status_activity("Waiting for model response"):
        current_status().report()
    assert expected in stream.getvalue()


def test_interface_prompt_temporarily_owns_status_input(monkeypatch):
    from lladar.controlled_variants import select_controlled_variant_topics
    monkeypatch.setattr("lladar.live_status._enter_reader", EnterQueue)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    def reply(prompt):
        assert current_status().paused > 0
        return "1"
    with status_queries(True):
        assert select_controlled_variant_topics([{"id": "tone", "label": "Tone"}],
                                                input_fn=reply, output_fn=lambda text: None) == ("tone",)
        assert current_status().paused == 0
