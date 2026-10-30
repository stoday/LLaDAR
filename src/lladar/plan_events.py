"""One redacted event stream for console, text log and JSONL diagnostics."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from threading import RLock
import traceback
import uuid

from .plan_spec import PlanError


_SECRET_KEY = re.compile(r"secret|password|passwd|token|api[_-]?key|authorization|cookie|credential", re.I)
_TOKEN_COUNTS = {"input_tokens", "output_tokens", "total_tokens", "max_input_tokens", "max_output_tokens"}
_ASSIGNMENT = re.compile(
    r'''((?:[\w-]*(?:secret|password|passwd|token|api[_-]?key|authorization|cookie|credential)[\w-]*)["']?\s*[:=]\s*)(\[REDACTED\]|"[^"]*"|'[^']*'|[^\s,;&}\]]+)''', re.I)


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key not in _TOKEN_COUNTS and _SECRET_KEY.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for key, secret in os.environ.items():
        if _SECRET_KEY.search(key) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;}]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)([?&](?:key|sig|signature|auth|code)=)[^&\s#]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+", r"\1[REDACTED]", text)
    return _ASSIGNMENT.sub(lambda match: match[1] + "[REDACTED]", text)


class Redactor:
    """Remember only this run's secret values so later echoes are also masked."""
    def __init__(self):
        self.secrets = set()

    def _learn(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in _TOKEN_COUNTS and _SECRET_KEY.search(key) and isinstance(item, str) and len(item) >= 4:
                    self.secrets.add(item)
                self._learn(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._learn(item)
        elif isinstance(value, str):
            for match in _ASSIGNMENT.finditer(value):
                secret = match[2].strip("\"'")
                if secret != "[REDACTED]" and len(secret) >= 4:
                    self.secrets.add(secret)

    def __call__(self, value):
        self._learn(value)

        def clean(item):
            if isinstance(item, dict):
                return {key: "[REDACTED]" if key not in _TOKEN_COUNTS and _SECRET_KEY.search(key) else clean(child) for key, child in item.items()}
            if isinstance(item, (list, tuple)):
                return [clean(child) for child in item]
            if isinstance(item, str):
                for secret in sorted(self.secrets, key=len, reverse=True):
                    item = item.replace(secret, "[REDACTED]")
                return redact(item)
            return item

        return clean(value)


class PlanEvents:
    def __init__(self, destination: Path, *, log: str | Path | None, debug: bool, output):
        self.destination = destination
        self.log_path = Path(log).resolve() if log is not None else destination / "plan.log"
        self.events_path = destination / "plan-events.jsonl"
        if self.log_path == self.events_path or self.log_path in {
            destination / name for name in ("plan.json", "plan.md", "commands.ps1", "diagnostics.json")
        }:
            raise PlanError("PLAN_INPUT_INVALID", "Log path conflicts with a plan artifact", stage="input", field="log")
        self.run_id = uuid.uuid4().hex
        self.plan_id = uuid.uuid4().hex
        self.debug, self.output = debug, output
        self.sequence = 0
        self.lock = RLock()
        self.errors = []
        self.disabled = set()
        self.owned = set()
        self.closed = False
        self.redact = Redactor()

    def emit(self, stage, event_type, message, **details):
        with self.lock:
            if self.closed:
                return None
            self.sequence += 1
            event = self.redact({"timestamp": datetime.now().astimezone().isoformat(),
                            "run_id": self.run_id, "plan_id": self.plan_id,
                            "trace_id": f"evt-{self.sequence:05d}", "stage": stage,
                            "event_type": event_type, "message": message, **details})
            if self.debug or event_type in {"error", "finished"}:
                self.output(self.render(event))
            for path, content in ((self.events_path, json.dumps(event, ensure_ascii=False) + "\n"),
                                  (self.log_path, self.render(event) + "\n")):
                if path in self.disabled:
                    continue
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a" if path in self.owned else "x", encoding="utf-8") as stream:
                        stream.write(content)
                    self.owned.add(path)
                except OSError as error:
                    self.disabled.add(path)
                    diagnostic = PlanError("PLAN_ARTIFACT_WRITE_ERROR", f"Cannot write log {path}: {error}",
                                           stage="log", field="log", recoverable=False,
                                           next_action="Choose a new writable log path.").diagnostic
                    diagnostic["trace_id"] = event["trace_id"]
                    diagnostic["exception_type"] = type(error).__name__
                    diagnostic["stack_trace"] = traceback.format_exc()
                    self.errors.append(self.redact(diagnostic))
                    self.output(self.render(self.redact({**event, "event_type": "error", **diagnostic})))
            return event

    @staticmethod
    def render(event):
        header = (f"[{event['timestamp']}] [run={event['run_id']}] [stage={event['stage']}] "
                  f"{event['event_type'].upper()} {event.get('code', '')}: {event['message']} "
                  f"trace_id={event['trace_id']}")
        details = {key: value for key, value in event.items() if key not in {
            "timestamp", "run_id", "stage", "event_type", "message", "code", "trace_id"}}
        stack = details.pop("stack_trace", None)
        return header + "\n" + json.dumps(details, ensure_ascii=False) + ("\n" + stack if stack else "")

    def diagnostic(self, diagnostic, **details):
        event = self.emit(diagnostic["stage"], "error", diagnostic["message"],
                          **{k: v for k, v in diagnostic.items() if k not in {"stage", "message", "trace_id"}}, **details)
        return {**self.redact(diagnostic), "trace_id": event["trace_id"]}
