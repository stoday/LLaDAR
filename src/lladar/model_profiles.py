from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    max_input_tokens: int
    max_output_tokens: int
    auto_window_ratio: float = 0.8


MODEL_PROFILES = {
    "gemini:gemini-2.5-flash": ModelProfile(
        max_input_tokens=1_048_576,
        max_output_tokens=65_536,
    ),
}
DEFAULT_MODEL_PROFILE = ModelProfile(
    max_input_tokens=16_384,
    max_output_tokens=8_192,
)


def resolve_model_profile(
    model: str,
    *,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    auto_window_ratio: float | None = None,
) -> ModelProfile:
    base = MODEL_PROFILES.get(model, DEFAULT_MODEL_PROFILE)
    resolved_input = base.max_input_tokens if max_input_tokens is None else max_input_tokens
    resolved_output = (
        base.max_output_tokens if max_output_tokens is None else max_output_tokens
    )
    resolved_ratio = (
        base.auto_window_ratio if auto_window_ratio is None else auto_window_ratio
    )
    if resolved_input <= 0:
        raise ValueError("max_input_tokens must be greater than 0")
    if resolved_output <= 0:
        raise ValueError("max_output_tokens must be greater than 0")
    if not 0 < resolved_ratio <= 1:
        raise ValueError("auto_window_ratio must satisfy 0 < value <= 1")
    return ModelProfile(
        max_input_tokens=resolved_input,
        max_output_tokens=resolved_output,
        auto_window_ratio=resolved_ratio,
    )