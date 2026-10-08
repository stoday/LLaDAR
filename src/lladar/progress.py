from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from threading import Event, Thread
from typing import Any, TextIO


_COLORS = {
    "CONFIG": "\x1b[34m",
    "SOURCE": "\x1b[36m",
    "WINDOW": "\x1b[36m",
    "CHUNK": "\x1b[35m",
    "GROUP": "\x1b[32m",
    "CACHE": "\x1b[33m",
    "RETRY": "\x1b[33m",
    "PAIR": "\x1b[32m",
    "SESSION": "\x1b[32m",
    "WRITE": "\x1b[36m",
    "WARN": "\x1b[33m",
    "DONE": "\x1b[32m",
    "QUESTION": "\x1b[36m",
    "EXPECTED": "\x1b[32m",
    "RESPONSE": "\x1b[35m",
    "BROWSER": "\x1b[36m",
}
_RESET = "\x1b[0m"


class ProgressReporter:
    """Render timestamped package progress without contaminating data output."""

    def __init__(self, enabled: bool = True, stream: TextIO | None = None) -> None:
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.started_at = time.perf_counter()
        self.use_color = bool(
            enabled
            and getattr(self.stream, "isatty", lambda: False)()
            and "NO_COLOR" not in os.environ
        )

    def configuration(self, values: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.emit("CONFIG", "effective settings")
        for key, value in values.items():
            self.emit("CONFIG", f"{key:<20} {_display(value)}")

    def emit(self, label: str, message: str) -> None:
        if not self.enabled:
            return
        now = datetime.now().astimezone()
        timestamp = f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d} {now:%z}"
        elapsed = max(0.0, time.perf_counter() - self.started_at)
        marker = f"[{label}]"
        if self.use_color:
            marker = f"{_COLORS.get(label, '')}{marker}{_RESET}"
        print(f"{timestamp} [+{elapsed:.3f}s] {marker} {message}", file=self.stream, flush=True)

    def pair(self, completed: int, total: int, message: str) -> None:
        """Report legacy pair/runner progress."""
        self._progress("PAIR", completed, total, message)

    def record(self, record: dict[str, Any], *, context: str) -> None:
        """Flush readable QA details as soon as a record becomes available."""
        self.emit("ITEM", context)
        self.answer("QUESTION", record.get("question"))
        self.answer("EXPECTED", record.get("expected_answer"))
        self.answer("RESPONSE", record.get("actual_response"))

    def answer(self, label: str, value: str | None) -> None:
        if not self.enabled or value is None:
            return
        # Color both the label and all content, including multiline answers.
        if self.use_color:
            value = f"{_COLORS.get(label, '')}{value}{_RESET}"
        self.emit(label, value)

    @contextmanager
    def waiting(self, message: str, *, interval: float = 5.0):
        """Report synchronous work without moving browser operations off-thread."""
        if not self.enabled:
            yield
            return
        stopped = Event()
        started = time.perf_counter()
        self.emit("BROWSER", f"{message} started")

        def heartbeat() -> None:
            while not stopped.wait(interval):
                self.emit("BROWSER", f"{message} still working stage_elapsed={_duration(time.perf_counter() - started)}")

        thread = Thread(target=heartbeat, name="lladar-progress", daemon=True)
        thread.start()
        status = "finished"
        try:
            yield
        except BaseException:
            status = "interrupted"
            raise
        finally:
            stopped.set()
            thread.join()
            self.emit("BROWSER", f"{message} {status} stage_elapsed={_duration(time.perf_counter() - started)}")

    def session(self, completed: int, total: int, message: str) -> None:
        self._progress("SESSION", completed, total, message)

    def group(self, completed: int, total: int, message: str) -> None:
        self._progress("GROUP", completed, total, message)

    def _progress(self, label: str, completed: int, total: int, message: str) -> None:
        elapsed = time.perf_counter() - self.started_at
        remaining = max(total - completed, 0)
        eta = elapsed / completed * remaining if completed else None
        eta_text = "estimating" if eta is None else _duration(eta)
        self.emit(
            label,
            f"{completed}/{total} {message} elapsed={_duration(elapsed)} ETA={eta_text}",
        )

    def done(self, item_count: int, *, metric: str = "generated") -> None:
        elapsed = time.perf_counter() - self.started_at
        self.emit("DONE", f"{metric}={item_count} elapsed={_duration(elapsed)}")


def _display(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
