from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any, TextIO


_COLORS = {
    "CONFIG": "\x1b[34m",
    "SOURCE": "\x1b[36m",
    "WINDOW": "\x1b[36m",
    "CHUNK": "\x1b[35m",
    "CACHE": "\x1b[33m",
    "RETRY": "\x1b[33m",
    "PAIR": "\x1b[32m",
    "WRITE": "\x1b[36m",
    "WARN": "\x1b[33m",
    "DONE": "\x1b[32m",
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
        rendered = " ".join(f"{key}={_display(value)}" for key, value in values.items())
        self.emit("CONFIG", rendered)

    def emit(self, label: str, message: str) -> None:
        if not self.enabled:
            return
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        marker = f"[{label}]"
        if self.use_color:
            marker = f"{_COLORS.get(label, '')}{marker}{_RESET}"
        print(f"{timestamp} {marker} {message}", file=self.stream, flush=True)

    def pair(self, completed: int, total: int, message: str) -> None:
        elapsed = time.perf_counter() - self.started_at
        remaining = max(total - completed, 0)
        eta = elapsed / completed * remaining if completed else None
        eta_text = "estimating" if eta is None else _duration(eta)
        self.emit(
            "PAIR",
            f"{completed}/{total} {message} elapsed={_duration(elapsed)} ETA={eta_text}",
        )

    def done(self, item_count: int) -> None:
        elapsed = time.perf_counter() - self.started_at
        self.emit("DONE", f"generated={item_count} elapsed={_duration(elapsed)}")


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