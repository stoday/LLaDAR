"""Provider-reported token accounting; never persist prompts or estimate billing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from threading import RLock

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from .language import tr
from .progress import ProgressReporter

_CURRENT = ContextVar("lladar_token_usage", default=None)
_CALLBACK = ContextVar("lladar_token_callback", default=None)
register_configure_hook(_CALLBACK, inheritable=True)
_COUNTERS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
             "reasoning_output_tokens", "reported_calls", "unreported_calls", "failed_calls")


def _empty():
    return dict.fromkeys(_COUNTERS, 0)


class TokenUsage:
    def __init__(self, *, saved=None, verbose=False, session=None, operation=None, historical_unknown=False):
        self.lock = RLock()
        self.session = session
        self.operation = operation
        self.reporter = ProgressReporter(verbose)
        self.data = deepcopy(saved) if saved else {
            "schema_version": 1, "measurement": "provider_reported", "scope": "work_session",
            **_empty(), "stages": {}, "historical_usage_unknown": historical_unknown,
        }
        self.invocation = _empty()
        self.sync()

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.data)
            result["status"] = ("partial" if result["historical_usage_unknown"] or
                                result["unreported_calls"] else
                                "reported" if result["reported_calls"] else "unavailable")
            return result

    def sync(self):
        snapshot = self.snapshot()
        if self.operation is not None:
            self.operation.token_usage = {**snapshot, "invocation": deepcopy(self.invocation)}
        if self.session is not None:
            self.session.put("token_usage", snapshot)

    def record(self, stage, usage=None, *, failed=False):
        with self.lock:
            delta = _empty()
            if usage is None:
                delta["unreported_calls"] = 1
            else:
                delta.update(usage)
                delta["reported_calls"] = 1
            delta["failed_calls"] = int(failed)
            bucket = self.data["stages"].setdefault(stage, _empty())
            for key in _COUNTERS:
                self.data[key] += delta[key]
                self.invocation[key] += delta[key]
                bucket[key] += delta[key]
            self.sync()
            self.reporter.emit("TOKENS", tr(
                "stage={stage} cumulative input={input} output={output} total={total}; unreported calls={unknown}",
                stage=stage, input=self.data["input_tokens"], output=self.data["output_tokens"],
                total=self.data["total_tokens"], unknown=self.data["unreported_calls"]))


def usage_snapshot():
    tracker = _CURRENT.get()
    return tracker.snapshot() if tracker is not None else None


def usage_report_lines(usage):
    lines = ["", "## " + tr("Token usage"), ""]
    if usage is None:
        return lines + [tr("Token usage was not collected for this invocation.")]
    lines += [tr("Provider-reported tokens; totals include all recorded attempts in this work session."),
              f"- {tr('Measurement status')}: `{usage['status']}`"]
    for key in _COUNTERS:
        lines.append(f"- `{key}`: {usage[key]}")
    if "invocation" in usage:
        lines += ["", tr("This invocation: input={input}, output={output}, total={total} tokens.",
                         input=usage['invocation']['input_tokens'], output=usage['invocation']['output_tokens'],
                         total=usage['invocation']['total_tokens'])]
    lines += ["", "| " + tr("Stage") + " | input | output | total | unreported calls |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for stage, counts in usage["stages"].items():
        lines.append(f"| `{stage}` | {counts['input_tokens']} | {counts['output_tokens']} | "
                     f"{counts['total_tokens']} | {counts['unreported_calls']} |")
    lines += ["", tr("Counts cover inventory, reconciliation, generation, audit, coverage review and repair calls. Repeated prompts, tool results and retries count again when reported by the provider."),
              tr("Updates appear after each model response, not while a response is pending. No extra token-count API calls are made."),
              tr("total_tokens = input_tokens + output_tokens. Cached input and reasoning output are subsets when supplied; do not add them again."),
              tr("Missing usage is unknown, not zero. Failed requests, provider-internal retries and work predating tracking may be unreported. These totals are not a billing statement.")]
    if usage["historical_usage_unknown"]:
        lines += [tr("This session contains earlier work without token records; historical usage is unknown.")]
    return lines


def token_usage_stage(function):
    """Inside stage_session: restore counters without recounting completed work."""
    @wraps(function)
    def invoke(*args, **kwargs):
        from .operation_report import current_operation
        from .work_session import current_session
        session = current_session()
        saved = session.data["state"].get("token_usage") if session else None
        historical_unknown = bool(session and not saved and session.data["state"])
        tracker = TokenUsage(saved=saved, verbose=kwargs.get("verbose", True), session=session,
                             operation=current_operation(), historical_unknown=historical_unknown)
        if session is None:
            tracker.data["scope"] = "invocation"
            tracker.sync()
        token = _CURRENT.set(tracker)
        try:
            return function(*args, **kwargs)
        finally:
            _CURRENT.reset(token)
    return invoke


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _normalize(raw):
    if not isinstance(raw, dict):
        return None
    incoming = _number(raw.get("input_tokens", raw.get("prompt_tokens", raw.get("prompt_token_count"))))
    outgoing = _number(raw.get("output_tokens", raw.get("completion_tokens", raw.get("candidates_token_count"))))
    if incoming is None or outgoing is None:
        return None
    input_details = raw.get("input_token_details") or raw.get("prompt_tokens_details") or {}
    output_details = raw.get("output_token_details") or raw.get("completion_tokens_details") or {}
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    return {"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": incoming + outgoing,
            "cached_input_tokens": _number(input_details.get("cache_read", input_details.get("cached_tokens"))) or 0,
            "reasoning_output_tokens": _number(output_details.get("reasoning", output_details.get("reasoning_tokens"))) or 0}


def _response_usage(response):
    # One candidate per prompt: alternative generations do not represent new requests.
    parts = []
    for group in getattr(response, "generations", []) or []:
        if group:
            message = getattr(group[0], "message", None)
            usage = _normalize(getattr(message, "usage_metadata", None))
            if usage is None:
                metadata = getattr(message, "response_metadata", {}) or {}
                usage = _normalize(metadata.get("token_usage") or metadata.get("usage_metadata"))
            parts.append(usage)
    if parts and all(part is not None for part in parts):
        return {key: sum(part[key] for part in parts) for key in parts[0]}
    output = getattr(response, "llm_output", None) or {}
    return _normalize(output.get("token_usage") or output.get("usage_metadata") or output.get("usage"))


class TokenCallback(BaseCallbackHandler):
    # Inline handling retains context and checkpoints before the next model/tool step.
    run_inline = True
    raise_error = True

    def __init__(self, tracker, stage):
        self.tracker = tracker
        self.stage = stage
        self.pending = set()
        self.finished = set()
        self.lock = RLock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.on_llm_start(serialized, None, run_id=run_id, **kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        # Akasha's Ollama facade copies usage from its real inner model response.
        # Count the inner model only, so copied metadata is not charged twice.
        serialized = serialized or {}
        if serialized.get("name") == "OllamaJSONModel" or "OllamaJSONModel" in serialized.get("id", []):
            return
        with self.lock:
            self.pending.add(run_id)

    def _finish(self, run_id, response=None, *, failed=False):
        with self.lock:
            if run_id not in self.pending or run_id in self.finished:
                return
            self.pending.remove(run_id)
            self.finished.add(run_id)
            self.tracker.record(self.stage, _response_usage(response), failed=failed)

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(run_id, response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, kwargs.get("response"), failed=True)

    def close(self):
        for run_id in list(self.pending):
            self._finish(run_id, failed=True)


@contextmanager
def model_usage(stage):
    tracker = _CURRENT.get()
    if tracker is None:
        yield None
        return
    callback = TokenCallback(tracker, stage)
    token = _CALLBACK.set(callback)
    try:
        yield tracker
    finally:
        try:
            callback.close()
        finally:
            _CALLBACK.reset(token)
