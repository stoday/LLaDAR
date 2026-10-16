"""Enter-to-query CLI status, with exclusive handoff to human input prompts."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import builtins
import os
import sys
from threading import Event, RLock, Thread
import time

from .language import current_language, tr


_CURRENT = ContextVar("lladar_live_status", default=None)


class _WindowsConsole:
    """Peek native events without taking characters away from input()."""
    def __init__(self, descriptor):
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class Character(ctypes.Union):
            _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", wintypes.CHAR)]

        class KeyEvent(ctypes.Structure):
            _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                        ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                        ("uChar", Character), ("dwControlKeyState", wintypes.DWORD)]

        class EventData(ctypes.Union):
            _fields_ = [("KeyEvent", KeyEvent), ("padding", wintypes.BYTE * 16)]

        class InputRecord(ctypes.Structure):
            _fields_ = [("EventType", wintypes.WORD), ("Event", EventData)]

        self.ctypes, self.record_type = ctypes, InputRecord
        self.handle = msvcrt.get_osfhandle(descriptor)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_mode = kernel.GetConsoleMode
        get_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_mode.restype = wintypes.BOOL
        if not get_mode(self.handle, ctypes.byref(wintypes.DWORD())):
            raise ctypes.WinError(ctypes.get_last_error())
        self.peek_input, self.read_input = kernel.PeekConsoleInputW, kernel.ReadConsoleInputW
        for function in (self.peek_input, self.read_input):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD)]
            function.restype = wintypes.BOOL

    def _event(self, function):
        record = self.record_type()
        count = self.ctypes.c_ulong()
        if not function(self.handle, self.ctypes.byref(record), 1, self.ctypes.byref(count)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        if not count.value:
            return None
        key = record.Event.KeyEvent
        return record.EventType == 1 and bool(key.bKeyDown), key.uChar.UnicodeChar, key.wRepeatCount

    def peek(self):
        return self._event(self.peek_input)

    def take(self):
        self._event(self.read_input)


class _WindowsEnter:
    def __init__(self, console):
        self.console = console
        self.repeats = 0

    def poll(self):
        if self.repeats:
            self.repeats -= 1
            return True
        for _ in range(32):
            event = self.console.peek()
            if event is None:
                return False
            key_down, character, repeats = event
            if key_down and character not in {"\r", "\n"}:
                return False
            self.console.take()
            if key_down:
                self.repeats = max(0, repeats - 1)
                return True
        return False

    def reset(self):
        self.repeats = 0


class _PosixEnter:
    """Read complete canonical lines; retain typeahead for the next human prompt.

    Never peek through sys.stdin.buffer: input()/readline may use a different
    buffer. All foreground prompts use read_user_input to receive retained text.
    Terminal attributes, signal processing and descriptor flags stay unchanged.
    """
    def __init__(self, source):
        self.source = source
        self.descriptor = source.fileno()
        self.pending = None

    def poll(self):
        import select

        if self.pending is not None or not select.select([self.descriptor], [], [], 0)[0]:
            return False
        # POSIX canonical read() returns at most one completed line, even if
        # multiple Enter keys or a future answer are already queued.
        line = os.read(self.descriptor, 65536)
        if line in {b"\n", b"\r", b"\r\n"}:
            return True
        self.pending = line
        return False

    def reset(self):
        # A manual prompt, rather than reset(), owns any retained answer/EOF.
        pass

    def take_pending(self):
        pending, self.pending = self.pending, None
        return pending


def _enter_reader():
    """Use nonblocking reads only on the command's real interactive terminal."""
    try:
        if not (sys.stdin.isatty() and sys.stderr.isatty() and os.isatty(sys.stdin.fileno())):
            return None
        if os.name == "nt":
            return _WindowsEnter(_WindowsConsole(sys.stdin.fileno()))
        if os.name == "posix":
            import termios

            descriptor = sys.stdin.fileno()
            try:
                attributes = termios.tcgetattr(descriptor)
            except termios.error:
                return None
            if not attributes[3] & termios.ICANON:
                return None
            if os.tcgetpgrp(descriptor) != os.getpgrp():
                return None
            return _PosixEnter(sys.stdin)
    except (AttributeError, ImportError, OSError, ValueError):
        pass
    return None


class _StatusQueries:
    def __init__(self, reader, emit):
        self.reader, self.emit = reader, emit
        self.language = current_language()
        self.lock = RLock()
        self.stopped = Event()
        self.paused = 0
        self.activities = []
        self.started = self.last_progress = time.perf_counter()
        self.thread = None

    @contextmanager
    def progress_output(self):
        with self.lock:
            if self.paused:
                yield False
                return
            self.last_progress = time.perf_counter()
            yield True

    @contextmanager
    def activity(self, message):
        token = object()
        with self.lock:
            self.activities.append((token, message, time.perf_counter()))
        try:
            yield
        finally:
            with self.lock:
                self.activities = [item for item in self.activities if item[0] is not token]

    @contextmanager
    def user_input(self):
        # Synchronize with polling and printing before displaying a prompt.
        with self.lock:
            self.paused += 1
        try:
            yield
        finally:
            with self.lock:
                self.reader.reset()
                self.paused -= 1

    def report(self):
        with self.lock:
            if self.paused or self.stopped.is_set():
                return
            _, message, started = self.activities[-1] if self.activities else (
                None, tr("Running command", language=self.language), self.started)
            now = time.perf_counter()
            self.emit("STATUS", tr(
                "Current activity: {activity}; stage elapsed={elapsed:.1f}s; last progress message={last:.1f}s ago",
                language=self.language, activity=message, elapsed=max(0, now - started),
                last=max(0, now - self.last_progress)))

    def poll(self):
        with self.lock:
            if not self.paused and not self.stopped.is_set() and self.reader.poll():
                self.report()

    def start(self):
        self.emit("STATUS", tr("Press Enter while work is running to show its current activity. Input prompts keep their normal behavior.", language=self.language))

        def watch():
            while not self.stopped.wait(0.1):
                try:
                    self.poll()
                except (OSError, ValueError):
                    # Losing the terminal must not fail or cancel the workflow.
                    break

        self.thread = Thread(target=watch, name="lladar-status-input", daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


def current_status():
    return _CURRENT.get()


@contextmanager
def status_queries(enabled):
    reader = _enter_reader() if enabled else None
    if reader is None:
        yield
        return
    from .progress import ProgressReporter

    reporter = ProgressReporter()
    queries = _StatusQueries(reader, reporter.emit)
    token = _CURRENT.set(queries)
    try:
        queries.start()
        yield
    finally:
        queries.close()
        _CURRENT.reset(token)
        reporter.close()


@contextmanager
def status_activity(message):
    queries = current_status()
    if queries is None:
        yield
    else:
        with queries.activity(tr(message, language=queries.language)):
            yield


@contextmanager
def status_user_input():
    queries = current_status()
    if queries is None:
        yield
    else:
        with queries.user_input():
            yield


def read_user_input(prompt=None, *, input_fn=None):
    """Read a human reply, including a POSIX line typed before its prompt."""
    input_fn = builtins.input if input_fn is None else input_fn
    queries = current_status()
    with status_user_input():
        if queries is not None and isinstance(queries.reader, _PosixEnter) and input_fn is builtins.input:
            with queries.lock:
                line = queries.reader.take_pending()
            if line is not None:
                if prompt is not None:
                    print(prompt, end="", flush=True)
                if not line:
                    raise EOFError
                # The terminal already echoed this line. Do not copy its value
                # to stdout/--log, which could contain a private clarification.
                print(flush=True)
                source = queries.reader.source
                text = line.decode(source.encoding or "utf-8", source.errors or "strict")
                return text.removesuffix("\n").removesuffix("\r")
        return input_fn() if prompt is None else input_fn(prompt)
