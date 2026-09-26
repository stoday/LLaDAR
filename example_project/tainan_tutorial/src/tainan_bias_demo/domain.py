from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class DemoCase:
    id: str
    suite: str
    question: str
    context: str
    facts: tuple[str, ...]
    rules: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PolicyRule:
    id: str
    definition: str
    violation: str
    acceptable: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    version: str
    cases: tuple[DemoCase, ...]
    rules: tuple[PolicyRule, ...]


@dataclass(frozen=True)
class AgentRequest:
    case_id: str
    messages: tuple[Mapping[str, str], ...]
    system_prompt: str
    timeout_s: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResponse:
    text: str
    raw: Any
    model: str
    latency_ms: int
    token_usage: Mapping[str, int]
    error: str | None = None


class AgentAdapter(Protocol):
    async def invoke(self, request: AgentRequest) -> AgentResponse: ...


@dataclass(frozen=True)
class DetectionSignal:
    rule_id: str
    evidence: str
    start: int
    end: int


@dataclass(frozen=True)
class Verdict:
    factuality: str
    framing_compliance: str
    violations: tuple[str, ...]
    evidence_spans: tuple[str, ...]
    rationale: str
    confidence: float


@dataclass(frozen=True)
class ReviewRecord:
    status: str
    reviewer: str
    reviewed_at: str
    reason: str


@dataclass(frozen=True)
class CaseResult:
    case: DemoCase
    response: AgentResponse
    signals: tuple[DetectionSignal, ...]
    verdict: Verdict
    final_review: ReviewRecord
    retry_count: int
    judge_version: str


@dataclass(frozen=True)
class DemoRun:
    run_id: str
    created_at: str
    provenance: Mapping[str, Any]
    results: tuple[CaseResult, ...]
    summary: Mapping[str, float | int]
    replay_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

