from __future__ import annotations

from typing import Any, Protocol


class LLMProvider(Protocol):
    def generate_structured(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> dict[str, Any]: ...