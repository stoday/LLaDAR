"""Single tool-free model extraction path for captured website answers."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Callable

from .response_capture import ObservedAnswer, ObservedResponse, observation_order


DEFAULT_EXTRACTION_MODEL = "gemini:gemini-3.8-flash"
GEMINI_ORIGIN = "https://generativelanguage.googleapis.com"
PROMPT_VERSION = "captured-answer-v1"
MAX_INPUT_BYTES = 120 * 1024
MAX_CAPTURE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
_REASONS = frozenset({
    "response_transfer_not_approved", "reference_required", "reference_declined", "reference_mismatch",
    "request_identity_mismatch", "independent_observation_required", "completion_unconfirmed",
    "unsupported_response_protocol", "response_size_limit", "provider_input_limit", "sensitive_response",
    "model_incomplete", "model_ambiguous", "model_unsupported", "model_invalid", "extraction_blocked",
    "extraction_budget_exhausted", "extraction_time_exhausted", "invalid_provider_budget",
    "provider_destination_unsupported", "provider_credentials_required", "provider_failed",
    "provider_redirect_refused", "provider_authentication_failed", "provider_model_unavailable",
    "provider_rate_limited", "provider_service_unavailable", "provider_request_rejected",
    "provider_output_incomplete", "provider_response_invalid", "provider_output_limit",
    "provider_max_tokens", "provider_safety_blocked", "provider_cancelled",
    "provider_network_failed",
})


class ExtractionError(ValueError):
    """Only host-defined reasons may enter ordinary logs and sidecars."""
    def __init__(self, reason: str, *, usage: dict | None = None):
        self.reason = reason if reason in _REASONS else "provider_failed"
        self.usage = safe_usage(usage)
        super().__init__("Response extraction blocked: " + self.reason)


def validate_result(result: Any) -> str:
    if type(result) is not dict:
        raise ExtractionError("provider_response_invalid")
    status = result.get("status")
    if type(status) is str and status in {"incomplete", "ambiguous", "unsupported", "invalid"} and set(result) == {"status"}:
        raise ExtractionError("model_" + status)
    if (set(result) != {"status", "text"} or status != "final" or type(result["text"]) is not str
            or not result["text"].strip()):
        raise ExtractionError("provider_response_invalid")
    if len(result["text"].encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ExtractionError("provider_output_limit")
    return result["text"]


def safe_usage(value: Any) -> dict | None:
    if type(value) is not dict:
        return None
    result = {key: value[key] for key in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount", "thoughtsTokenCount")
              if type(value.get(key)) is int and 0 <= value[key] <= 100_000_000}
    return result or None


_CREDENTIAL_PATTERN = re.compile(
    r'(?i)(?:["\']?(?:token|access[_-]?token|refresh[_-]?token|api[_-]?key|password|authorization|cookie|client[_-]?secret)["\']?\s*[:=])'
    r'|(?:bearer\s+[a-z0-9._~-]+)|(?:eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)'
    r'|(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
)


def contains_sensitive_text(body: str, protected_values: tuple[str, ...]) -> bool:
    """Inspect escaped/nested JSON strings too, without changing model evidence.

    This is only a conservative credential screen, not a universal secret detector
    and never an answer-field recognizer. Excessive encoding is refused.
    """
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    def unescape(match):
        return chr(int(match[1], 16)) if match[1] else escapes[match[2]]
    for _ in range(8):
        if _CREDENTIAL_PATTERN.search(body) or any(value and value in body for value in protected_values):
            return True
        decoded = re.sub(r'\\(?:u([0-9a-fA-F]{4})|(["\\/bfnrt]))', unescape, body)
        if decoded == body:
            return False
        body = decoded
    return True


def response_input(observation: ObservedResponse, protected_values: tuple[str, ...] = ()) -> dict:
    if not (observation.stream_closed or observation.manual_complete):
        raise ExtractionError("completion_unconfirmed")
    media_type = observation.content_type.partition(";")[0].strip().lower()
    if media_type not in {"text/plain", "text/event-stream", "application/json", "application/ndjson", "application/x-ndjson"} and not (
        media_type.startswith("application/") and media_type.endswith("+json")
    ):
        raise ExtractionError("unsupported_response_protocol")
    try:
        body = observation.body.decode("utf-8") if isinstance(observation.body, bytes) else observation.body
        if type(body) is not str:
            raise ValueError()
        if len(body.encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise ExtractionError("response_size_limit")
        if contains_sensitive_text(body, protected_values):
            raise ExtractionError("sensitive_response")
        request = {"content_type": media_type, "body": body}
        if len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ExtractionError("provider_input_limit")
        return request
    except (UnicodeError, TypeError, ValueError) as error:
        if isinstance(error, ExtractionError):
            raise
        raise ExtractionError("unsupported_response_protocol") from None


@dataclass(frozen=True, repr=False)
class ExtractionOptions:
    model_destination: str = DEFAULT_EXTRACTION_MODEL
    provider_factory: Callable[..., Any] | None = None
    transfer_approved: bool = False
    reference_reader: Callable[[ObservedResponse, str], ObservedAnswer] | None = None
    provider_origin: str = GEMINI_ORIGIN


class ResponseExtractor:
    def __init__(self, options: ExtractionOptions, *, max_calls: int, protected_values: tuple[str, ...] = ()):
        if type(max_calls) is not int or max_calls < 1:
            raise ExtractionError("invalid_provider_budget")
        self.options = options
        self.max_calls = max_calls
        self._provider = None
        self._deadline = time.monotonic() + 3600
        self._protected_values = protected_values
        self._source = None
        self._not_before = -1
        self._request_ids: set[str] = set()
        self._blocked = False
        self.evidence = {"model": options.model_destination, "prompt_version": PROMPT_VERSION,
                         "schema_version": 1, "calls": 0, "max_calls": max_calls,
                         "max_output_tokens": 8192, "input_byte_limit": MAX_INPUT_BYTES,
                         "total_deadline_seconds": 3600, "events": []}

    def check_budget(self) -> None:
        if self._blocked:
            raise ExtractionError("extraction_blocked")
        if time.monotonic() >= self._deadline:
            raise ExtractionError("extraction_time_exhausted")
        if self.evidence["calls"] >= self.max_calls:
            raise ExtractionError("extraction_budget_exhausted")

    def remaining_seconds(self) -> float:
        self.check_budget()
        return max(0.001, self._deadline - time.monotonic())

    def extract(self, observation: ObservedResponse, *, request_id: str) -> str:
        started = time.monotonic()
        index = len(self.evidence["events"])
        event = {"index": index + 1, "status": "blocked", "usage": None,
                 "stage": ("calibration", "verification")[index] if index < 2 else "dataset"}
        try:
            self.check_budget()
            if self.options.transfer_approved is not True:
                raise ExtractionError("response_transfer_not_approved")
            receipt = observation._receipt
            if receipt is None or not receipt.matches(observation) or receipt.request_id != request_id:
                raise ExtractionError("request_identity_mismatch")
            if ((self._source is not None and receipt.source is not self._source)
                    or receipt.started_order <= self._not_before or request_id in self._request_ids):
                raise ExtractionError("independent_observation_required")
            request = response_input(observation, self._protected_values)
            if self._provider is None:
                factory = self.options.provider_factory
                if factory is None:
                    from .extraction_provider import GeminiExtractionProvider
                    factory = GeminiExtractionProvider
                self._provider = factory(model_destination=self.options.model_destination,
                                         timeout_seconds=3600, max_output_tokens=8192)
            self.check_budget()
            self.evidence["calls"] += 1
            self._request_ids.add(request_id)
            self._source = receipt.source
            result = self._provider.extract(request, timeout_seconds=self._deadline - time.monotonic())
            if time.monotonic() >= self._deadline:
                raise ExtractionError("extraction_time_exhausted")
            if type(result) is not dict or set(result) != {"result", "usage"}:
                raise ExtractionError("provider_response_invalid")
            event["usage"] = safe_usage(result["usage"])
            text = validate_result(result["result"])
            if contains_sensitive_text(text, self._protected_values):
                raise ExtractionError("sensitive_response")
            self._not_before = observation_order()
            event["status"] = "final"
            return text
        except BaseException as error:
            self._blocked = True
            reason = (error.reason if isinstance(error, ExtractionError) else
                      "provider_cancelled" if isinstance(error, KeyboardInterrupt) else "provider_failed")
            event["reason"] = reason
            if isinstance(error, ExtractionError) and error.usage is not None:
                event["usage"] = error.usage
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ExtractionError(reason, usage=event["usage"]) from None
        finally:
            event["duration_seconds"] = round(time.monotonic() - started, 3)
            self.evidence["events"].append(event)
