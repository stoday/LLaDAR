from __future__ import annotations

import json
from typing import Any, Protocol

from ..exceptions import ProviderError
from ..trace import TraceCall


class LLMProvider(Protocol):
    def generate_structured(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> dict[str, Any]: ...


def parse_json_object(response: Any) -> dict[str, Any]:
    if not isinstance(response, str):
        raise ProviderError("provider response must be text")
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProviderError("provider response was not valid JSON") from error
    if not isinstance(value, dict):
        raise ProviderError("provider response JSON must be an object")
    return value


def generate_structured(
    provider: LLMProvider,
    prompt: str,
    *,
    model: str,
    temperature: float,
    trace_call: TraceCall | None = None,
) -> dict[str, Any]:
    try:
        generate_text = getattr(provider, "generate_text", None)
        if callable(generate_text):
            raw_response = generate_text(prompt, model=model, temperature=temperature)
            if trace_call is not None and isinstance(raw_response, str):
                trace_call.response(raw_response)
            value = parse_json_object(raw_response)
        else:
            value = provider.generate_structured(
                prompt,
                model=model,
                temperature=temperature,
            )
            if trace_call is not None:
                trace_call.response(
                    json.dumps(value, ensure_ascii=False),
                    capture="serialized_structured_value",
                )
        if trace_call is not None:
            trace_call.parsed(value)
        return value
    except ProviderError as error:
        if trace_call is not None:
            trace_call.fail(
                reason_code="provider_error",
                reason=str(error),
                retry=False,
            )
        raise
