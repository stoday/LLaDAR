from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stdout
import sys
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
        verbose: bool = False,
        stream: bool = False,
    ) -> None:
        self.env_file = env_file
        self._agent_factory = agent_factory
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.verbose = verbose
        self.stream = stream

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
            stream=self.stream,
            thinking=False,
            max_input_tokens=profile.max_input_tokens,
            max_output_tokens=profile.max_output_tokens,
            temperature=temperature,
            verbose=self.verbose,
            keep_logs=False,
        )
        try:
            # Akasha emits its visible trace to stdout. Keep provider trace
            # separate from the CLI's structured data and returned answer.
            with redirect_stdout(sys.stderr):
                response = agent(prompt)
                if self.stream and not isinstance(response, str):
                    parts = []
                    for event in response:
                        if isinstance(event, str):
                            parts.append(event)
                        elif isinstance(event, dict):
                            if event.get("type") == "answer":
                                chunk = event.get("data")
                                if not isinstance(chunk, str):
                                    raise ProviderError("Akasha answer chunks must be text")
                                parts.append(chunk)
                            elif event.get("type") == "error":
                                raise ProviderError("Akasha generation stream failed")
                        else:
                            raise ProviderError("Unsupported Akasha stream event")
                    response = "".join(parts)
            if not isinstance(response, str):
                raise ProviderError("Akasha response must be text")
            if not response.strip():
                raise ProviderError("Akasha response was empty")
            return response
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError("Akasha generation failed") from error
