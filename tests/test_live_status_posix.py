"""POSIX input preservation plus real Linux/macOS controlling-terminal tests."""
from collections import deque
from contextlib import contextmanager
import io
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from lladar.command_log import command_log
from lladar.live_status import (_PosixEnter, current_status, read_user_input,
                               status_queries)


class Source:
    encoding = "utf-8"
    errors = "strict"
    def fileno(self):
        return 123


@pytest.fixture
def canonical_reader(monkeypatch):
    lines = deque()
    monkeypatch.setattr("select.select", lambda *args: ([123] if lines else [], [], []))
    monkeypatch.setattr("lladar.live_status.os.read", lambda descriptor, size: lines.popleft())
    reader = _PosixEnter(Source())
    return reader, lines


def test_posix_reads_each_enter_without_reading_a_future_answer(canonical_reader):
    reader, lines = canonical_reader
    lines.extend([b"\n", b"\n", "提早輸入✓\n".encode("utf-8"), b"MATCH\n"])
    assert reader.poll() and reader.poll()
    assert not reader.poll()
    assert not reader.poll()
    assert reader.take_pending() == "提早輸入✓\n".encode("utf-8")
    assert list(lines) == [b"MATCH\n"]


@pytest.mark.parametrize("line,expected", [(b"YES\n", "YES"), (b"  YES  \n", "  YES  "),
                                          ("私密說明✓\n".encode("utf-8"), "私密說明✓")])
def test_posix_typeahead_reaches_prompt_without_echoing_values_to_log(
        canonical_reader, tmp_path, monkeypatch, line, expected):
    reader, lines = canonical_reader
    lines.append(line)
    assert not reader.poll()
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: reader)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    log = tmp_path / "status.log"
    with command_log(log), status_queries(True):
        assert read_user_input("Reply: ") == expected
    assert expected not in log.read_text(encoding="utf-8")


def test_posix_preserves_eof_for_foreground_prompt(canonical_reader, monkeypatch):
    reader, lines = canonical_reader
    lines.append(b"")
    assert not reader.poll()
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: reader)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with status_queries(True), pytest.raises(EOFError):
        read_user_input()


def test_custom_input_function_keeps_ownership(canonical_reader, monkeypatch):
    reader, lines = canonical_reader
    lines.append(b"YES\n")
    reader.poll()
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: reader)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    with status_queries(True):
        assert read_user_input("custom", input_fn=lambda prompt: "custom answer") == "custom answer"
        assert reader.pending == b"YES\n"


def test_recorder_input_thread_receives_posix_typeahead(canonical_reader, monkeypatch):
    import builtins
    from types import SimpleNamespace
    from lladar.demonstration_recorder import DemonstrationRecorder

    reader, lines = canonical_reader
    lines.append(b"MATCH\n")
    reader.poll()
    monkeypatch.setattr("lladar.live_status._enter_reader", lambda: reader)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    recorder = DemonstrationRecorder.__new__(DemonstrationRecorder)
    recorder.browser = SimpleNamespace(page=SimpleNamespace(wait_for_timeout=lambda ms: time.sleep(0.001)))
    with status_queries(True):
        assert recorder.prompt("Confirmation: ", builtins.input) == "MATCH"
        assert current_status().paused == 0


PTY_CHILD = r'''
import fcntl, json, os, sys, termios, time
from threading import Event, enumerate as threads
from lladar.command_log import command_log
from lladar.live_status import current_status, read_user_input, status_activity, status_queries

mode, log = sys.argv[1:]
attrs = termios.tcgetattr(0) if os.isatty(0) else None
flags = fcntl.fcntl(0, fcntl.F_GETFL)

def unchanged():
    assert attrs is None or termios.tcgetattr(0) == attrs
    assert fcntl.fcntl(0, fcntl.F_GETFL) == flags
    assert current_status() is None
    assert not any(t.name == 'lladar-status-input' for t in threads())

try:
    with command_log(log), status_queries(mode not in {'disabled', 'pipe'}):
        monitor = current_status()
        if mode in {'disabled', 'pipe'}:
            assert monitor is None
            assert read_user_input('INPUT_PROMPT: ') == 'protocol input'
        else:
            assert monitor is not None, 'Real controlling terminal did not enable POSIX status'
            count = [0]
            emit = monitor.emit
            def capture(label, message):
                emit(label, message)
                if message.startswith('Current activity:'):
                    count[0] += 1
            monitor.emit = capture
            def wait_for(predicate):
                deadline = time.monotonic() + 15
                while not predicate():
                    assert time.monotonic() < deadline, 'terminal handshake timed out'
                    Event().wait(0.01)
            with status_activity('Building project code graph'):
                print('READY_QUERIES', flush=True)
                if mode == 'interrupt':
                    Event().wait(20)
                    raise AssertionError('Ctrl+C did not interrupt active work')
                wait_for(lambda: count[0] == 2)
                if mode == 'failure':
                    raise RuntimeError('controlled test failure')
                print('TYPEAHEAD_READY', flush=True)
                wait_for(lambda: monitor.reader.pending is not None)
            assert read_user_input('TYPEAHEAD_PROMPT: ') == '私密說明✓'
            assert read_user_input('ENTER_PROMPT: ') == ''
            assert read_user_input('MATCH_PROMPT: ') == 'MATCH'
            assert count[0] == 2, 'The monitor consumed an input prompt reply'
            with status_activity('Waiting for target response'):
                print('READY_FINAL_QUERY', flush=True)
                wait_for(lambda: count[0] == 3)
    unchanged()
    print('DONE', flush=True)
except KeyboardInterrupt:
    unchanged()
    print('CANCELLED_CLEANLY', flush=True)
    raise SystemExit(130)
except RuntimeError:
    unchanged()
    print('FAILED_CLEANLY', flush=True)
    raise SystemExit(7)
'''


class TerminalChild:
    def __init__(self, pid, descriptor):
        self.pid, self.descriptor = pid, descriptor
        self.output = b""
        self.status = None

    def send(self, value):
        os.write(self.descriptor, value)

    def until(self, marker):
        import select
        deadline = time.monotonic() + 25
        while marker not in self.output:
            if time.monotonic() >= deadline:
                pytest.fail(f"PTY did not produce {marker!r}: {self.output[-5000:]!r}")
            if select.select([self.descriptor], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.descriptor, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    pytest.fail(f"PTY closed before {marker!r}: {self.output[-5000:]!r}")
                self.output += chunk

    def wait(self):
        deadline = time.monotonic() + 10
        while self.status is None and time.monotonic() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = status
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.01)
        pytest.fail("PTY child did not exit")


@contextmanager
def terminal_child(tmp_path, mode):
    import pty

    script = tmp_path / "terminal_child.py"
    script.write_text(PTY_CHILD, encoding="utf-8")
    log = tmp_path / "terminal.log"
    # forkpty establishes a controlling terminal and foreground process group.
    # The child immediately execs Python before importing the application.
    pid, descriptor = pty.fork()
    if pid == 0:
        os.execl(sys.executable, sys.executable, "-u", str(script), mode, str(log))
    child = TerminalChild(pid, descriptor)
    try:
        yield child, log
    finally:
        if child.status is None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        os.close(descriptor)


native_posix = pytest.mark.skipif(os.name != "posix", reason="Requires native Linux/macOS PTYs")


@native_posix
def test_native_posix_enters_typeahead_prompts_and_log(tmp_path):
    with terminal_child(tmp_path, "normal") as (child, log):
        child.until(b"READY_QUERIES")
        child.send(b"\n\n")
        child.until(b"TYPEAHEAD_READY")
        child.send("私密說明✓\n".encode("utf-8"))
        child.until(b"ENTER_PROMPT: ")
        child.send(b"\n")
        child.until(b"MATCH_PROMPT: ")
        child.send(b"MATCH\n")
        child.until(b"READY_FINAL_QUERY")
        child.send(b"\n")
        child.until(b"DONE")
        assert child.wait() == 0
    saved = log.read_text(encoding="utf-8")
    assert saved.count("Current activity:") == 3
    assert "Current activity: Waiting for target response" in saved
    assert "私密說明✓" not in saved


@native_posix
@pytest.mark.parametrize("mode,marker,code", [("interrupt", b"CANCELLED_CLEANLY", 130),
                                             ("failure", b"FAILED_CLEANLY", 7)])
def test_native_posix_restores_terminal_after_cancel_or_failure(tmp_path, mode, marker, code):
    with terminal_child(tmp_path, mode) as (child, _):
        child.until(b"READY_QUERIES")
        child.send(b"\x03" if mode == "interrupt" else b"\n\n")
        child.until(marker)
        assert child.wait() == code


@native_posix
def test_native_posix_no_verbose_does_not_poll(tmp_path):
    with terminal_child(tmp_path, "disabled") as (child, log):
        child.until(b"INPUT_PROMPT: ")
        child.send(b"protocol input\n")
        child.until(b"DONE")
        assert child.wait() == 0
    assert "[STATUS]" not in log.read_text(encoding="utf-8")


@native_posix
def test_native_posix_piped_stdin_is_untouched(tmp_path):
    import subprocess

    script = tmp_path / "pipe_child.py"
    script.write_text(PTY_CHILD, encoding="utf-8")
    log = tmp_path / "pipe.log"
    result = subprocess.run([sys.executable, "-u", str(script), "pipe", str(log)],
                            input="protocol input\n", capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert "DONE" in result.stdout
    assert "[STATUS]" not in log.read_text(encoding="utf-8")
