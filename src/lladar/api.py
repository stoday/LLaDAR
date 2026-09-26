from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .loaders import KnowledgeInput
from .model_profiles import resolve_model_profile
from .skill_generation import generate_with_skill, preflight_output


DEFAULT_DATASET_MODEL = "gemini:gemini-3.7-flash"
BUILTIN_SKILL_DIR = Path(__file__).resolve().parent / "skill_assets" / "knowledge-point-qa"


def create_test_dataset(
    knowledge: KnowledgeInput,
    *,
    output: str | Path | None = None,
    count: int = 0,
    seed: int = 0,
    model: str = DEFAULT_DATASET_MODEL,
    env_file: str | Path = ".env",
    temperature: float = 0.0,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    auto_window_ratio: float | None = None,
    force: bool = False,
    verbose: bool = True,
    skill: str | Path | None = None,
    skill_agent_factory: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate source-grounded QA with the bundled or selected local skill."""
    if count < 0:
        raise ValueError("count must be zero or greater")
    profile = resolve_model_profile(
        model,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        auto_window_ratio=auto_window_ratio,
    )
    if output is not None:
        preflight_output(Path(output), force=force)
    result = generate_with_skill(
        knowledge,
        skill=Path(skill) if skill is not None else BUILTIN_SKILL_DIR,
        agent_factory=skill_agent_factory,
        count=count,
        seed=seed,
        model=model,
        env_file=env_file,
        temperature=temperature,
        profile=profile,
        verbose=verbose,
    )
    if output is not None:
        result.publish(Path(output), force=force)
    return result.records
