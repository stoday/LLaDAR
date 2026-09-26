from __future__ import annotations

import json
from typing import Any, Protocol

from ..exceptions import ProviderError


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
) -> dict[str, Any]:
    generate_text = getattr(provider, "generate_text", None)
    if callable(generate_text):
        raw_response = generate_text(prompt, model=model, temperature=temperature)
        return parse_json_object(raw_response)
    return provider.generate_structured(
        prompt,
        model=model,
        temperature=temperature,
    )
