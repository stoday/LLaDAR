from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .domain import AgentRequest, AgentResponse


class ReplayAdapter:
    """Deterministic, offline adapter backed by approved JSONL fixtures."""

    def __init__(self, fixture_path: str | Path):
        self.fixture_path = Path(fixture_path)
        self._responses: dict[str, dict[str, Any]] = {}
        for line in self.fixture_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                self._responses[item["case_id"]] = item

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        item = self._responses.get(request.case_id)
        if item is None:
            return AgentResponse(
                text="",
                raw=None,
                model="fixture:missing",
                latency_ms=0,
                token_usage={},
                error="fixture_not_found",
            )
        return AgentResponse(
            text=item["answer"],
            raw=item,
            model=item["model"],
            latency_ms=0,
            token_usage=item.get("token_usage", {}),
            error=None,
        )


class AkashaAgentAdapter:
    """A narrow boundary around akasha-terminal's public agent facade."""

    def __init__(
        self,
        *,
        model: str,
        env_file: str = ".env",
        agent_factory: Callable[..., Any] | None = None,
    ):
        self.model = model
        self.env_file = env_file
        self._agent_factory = agent_factory

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        started = time.perf_counter()
        try:
            factory = self._agent_factory
            if factory is None:
                from akasha.agent import agents

                factory = agents
            agent = factory(
                model=self.model,
                env_file=self.env_file,
                system_prompt=request.system_prompt,
                tools=[],
                stream=False,
                thinking=False,
                verbose=False,
                keep_logs=False,
            )
            messages = [dict(item) for item in request.messages]
            question = messages.pop()["content"] if messages else ""
            text = await asyncio.wait_for(
                agent.acall(question, messages=messages),
                timeout=request.timeout_s,
            )
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty_response")
            return AgentResponse(
                text=text,
                raw={"provider": "akasha-terminal"},
                model=self.model,
                latency_ms=round((time.perf_counter() - started) * 1000),
                token_usage={"total": int(getattr(agent, "tokens", 0))},
                error=None,
            )
        except TimeoutError:
            error = "timeout"
        except Exception as exc:
            error = str(exc) if str(exc) in {"empty_response"} else exc.__class__.__name__
        return AgentResponse(
            text="",
            raw=None,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            token_usage={},
            error=error,
        )

