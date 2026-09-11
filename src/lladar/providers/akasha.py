from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..exceptions import ProviderError
from ..model_profiles import resolve_model_profile
from .base import parse_json_object


class AkashaProvider:
    def __init__(
        self,
        *,
        env_file: str = ".env",
        agent_factory: Callable[..., Any] | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.env_file = env_file
        self._agent_factory = agent_factory
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens

    def generate_structured(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> dict[str, Any]:
        return parse_json_object(
            self.generate_text(prompt, model=model, temperature=temperature)
        )

    def generate_text(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> str:
        factory = self._agent_factory
        if factory is None:
            import akasha

            factory = akasha.agents
        profile = resolve_model_profile(
            model,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
        )
        agent = factory(
            model=model,
            env_file=self.env_file,
            stream=False,
            thinking=False,
            max_input_tokens=profile.max_input_tokens,
            max_output_tokens=profile.max_output_tokens,
            temperature=temperature,
            verbose=False,
            keep_logs=False,
        )
        try:
            response = agent(prompt)
            if not isinstance(response, str):
                raise ProviderError("Akasha response must be text")
            return response
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError("Akasha generation failed") from error
