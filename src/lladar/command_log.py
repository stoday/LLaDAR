"""Record CLI output without changing terminal interaction."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
import re
import sys
from threading import Lock


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


class _TeeStream:
    def __init__(self, terminal, log, lock):
        self.terminal, self.log, self.lock = terminal, log, lock

    def write(self, text):
        with self.lock:
            result = self.terminal.write(text)
            self.log.write(_ANSI_ESCAPE.sub("", text))
            self.log.flush()
            return result

    def flush(self):
        with self.lock:
            self.terminal.flush()
            self.log.flush()

    def __getattr__(self, name):
        # Preserve isatty(), encoding, fileno(), and other terminal properties.
        return getattr(self.terminal, name)


@contextmanager
def command_log(path: str | Path | None):
    if path is None:
        yield
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = Lock()
    with destination.open("x", encoding="utf-8", newline="") as log:
        with redirect_stdout(_TeeStream(sys.stdout, log, lock)), redirect_stderr(
            _TeeStream(sys.stderr, log, lock)
        ):
            yield
