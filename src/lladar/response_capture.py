"""Host-owned response material and provenance; no answer-field selection."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from itertools import count
import json


_ORDER = count()


def observation_order() -> int:
    return next(_ORDER)


@dataclass(frozen=True, repr=False)
class ObservedResponse:
    content_type: str
    body: str | bytes
    stream_closed: bool = True
    manual_complete: bool = False
    _receipt: _ResponseReceipt | None = field(default=None, repr=False, compare=False)


def _binding(observation: ObservedResponse) -> str:
    body = observation.body.encode("utf-8") if isinstance(observation.body, str) else observation.body
    metadata = json.dumps([observation.content_type, observation.stream_closed, observation.manual_complete]).encode()
    return hashlib.sha256(metadata + b"\0" + body).hexdigest()


@dataclass(frozen=True, repr=False)
class _ResponseReceipt:
    source: object
    request: object
    request_id: str
    started_order: int
    binding: str

    def matches(self, observation: ObservedResponse) -> bool:
        return self.binding == _binding(observation)


@dataclass(frozen=True, repr=False)
class _ResponseCapture:
    source: object
    request_id: str
    started_order: int = field(default_factory=observation_order)
    request: object = field(default_factory=object)

    def observe(self, content_type: str, body: str | bytes, stream_closed: bool = True,
                *, manual_complete: bool = False) -> ObservedResponse:
        observation = ObservedResponse(content_type, body, stream_closed, manual_complete)
        receipt = _ResponseReceipt(self.source, self.request, self.request_id, self.started_order, _binding(observation))
        return ObservedResponse(content_type, body, stream_closed, manual_complete, _receipt=receipt)


class ResponseObservationSource:
    """One trusted transport owner per browser; never a model tool."""

    def __init__(self) -> None:
        self._source = object()

    def begin_request(self, request_id: str) -> _ResponseCapture:
        if type(request_id) is not str or not request_id:
            raise ValueError("Observed request identity is required")
        return _ResponseCapture(self._source, request_id)


@dataclass(frozen=True, repr=False)
class ObservedAnswer:
    """Independent trusted local reference, never expected_answer or model input."""
    request_id: str
    text: str
    complete: bool
