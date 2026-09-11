from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


class ModelTrace:
    """Persist opt-in model exchanges without widening normal progress output."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        root: str | Path = ".lladar/runs",
        console: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        self.console = console
        self.stream = stream if stream is not None else sys.stderr
        self.run_directory: Path | None = None
        self._sequence = 0
        if not enabled:
            return
        self.run_directory = _reserve_run_directory(Path(root))
        (self.run_directory / "calls").mkdir()
        _write_json(
            self.run_directory / "run.json",
            {
                "schema_version": 1,
                "started_at": datetime.now().astimezone().isoformat(),
                "trace_console": console,
            },
        )
        (self.run_directory / "events.jsonl").write_text("", encoding="utf-8")
        self.event("trace_started", path=str(self.run_directory))
        print(
            "[TRACE] warning: trace contains complete prompts and model responses",
            file=self.stream,
            flush=True,
        )
        print(f"[TRACE] path={self.run_directory}", file=self.stream, flush=True)

    def call(
        self,
        label: str,
        *,
        stage: str,
        prompt: str,
        model: str,
        temperature: float,
        context: dict[str, Any] | None = None,
    ) -> TraceCall | None:
        if self.run_directory is None:
            return None
        self._sequence += 1
        safe_label = re.sub(r"[^a-z0-9-]+", "-", label.casefold()).strip("-")
        directory = (
            self.run_directory
            / "calls"
            / f"{self._sequence:04d}-{safe_label}-INCOMPLETE"
        )
        directory.mkdir()
        _write_json(
            directory / "request.json",
            {
                "sequence": self._sequence,
                "stage": stage,
                "model": model,
                "temperature": temperature,
                "started_at": datetime.now().astimezone().isoformat(),
                "context": context or {},
            },
        )
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
        self.event("model_call_started", sequence=self._sequence, stage=stage)
        if self.console:
            print(f"[TRACE PROMPT {self._sequence:04d}]\n{prompt}", file=self.stream, flush=True)
        return TraceCall(self, directory, self._sequence, stage, time.perf_counter())

    def event(self, event: str, **values: Any) -> None:
        if self.run_directory is None:
            return
        record = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "event": event,
            **values,
        }
        with (self.run_directory / "events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class TraceCall:
    def __init__(
        self,
        trace: ModelTrace,
        directory: Path,
        sequence: int,
        stage: str,
        started_at: float,
    ) -> None:
        self.trace = trace
        self.directory = directory
        self.sequence = sequence
        self.stage = stage
        self.started_at = started_at
        self.finished = False

    def response(self, value: str, *, capture: str = "raw_text") -> None:
        (self.directory / "response.txt").write_text(value, encoding="utf-8")
        request_path = self.directory / "request.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["response_capture"] = capture
        _write_json(request_path, request)
        if self.trace.console:
            print(
                f"[TRACE RESPONSE {self.sequence:04d}]\n{value}",
                file=self.trace.stream,
                flush=True,
            )

    def parsed(self, value: dict[str, Any]) -> None:
        _write_json(self.directory / "parsed.json", value)

    def ok(self, details: dict[str, Any] | None = None) -> Path:
        if details is not None:
            _write_json(self.directory / "validation.json", details)
        return self._finish("OK", details or {})

    def fail(
        self,
        *,
        reason_code: str,
        reason: str,
        retry: bool,
        details: dict[str, Any] | None = None,
    ) -> Path:
        failure: dict[str, Any] = {
            "reason_code": reason_code,
            "reason": reason,
            "retry": retry,
        }
        if details is not None:
            failure["details"] = details
        _write_json(self.directory / "failure.json", failure)
        return self._finish("FAIL", failure)

    def _finish(self, status: str, details: dict[str, Any]) -> Path:
        if self.finished:
            return self.directory
        elapsed = time.perf_counter() - self.started_at
        destination = self.directory.with_name(
            self.directory.name.removesuffix("-INCOMPLETE") + f"-{status}"
        )
        self.directory.rename(destination)
        self.directory = destination
        self.finished = True
        self.trace.event(
            "model_call_finished",
            sequence=self.sequence,
            stage=self.stage,
            status=status,
            duration_seconds=elapsed,
            path=str(destination),
            **details,
        )
        outcome = ""
        if status == "FAIL":
            reason_code = str(details.get("reason_code", "unknown"))
            reason = re.sub(r"\s+", " ", str(details.get("reason", "unknown"))).strip()
            if len(reason) > 300:
                reason = reason[:297] + "..."
            outcome = f" reason_code={reason_code} reason={reason}"
        print(
            f"[TRACE] stage={self.stage} status={status}{outcome} path={destination}",
            file=self.trace.stream,
            flush=True,
        )
        return destination


def _reserve_run_directory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = 0
    while True:
        directory = root / f"{stem}{'' if suffix == 0 else f'-{suffix}'}"
        try:
            directory.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        return directory


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
