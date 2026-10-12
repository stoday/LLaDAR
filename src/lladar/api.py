from __future__ import annotations

from .language import language_stage, current_language

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .loaders import KnowledgeInput
from .model_profiles import resolve_model_profile
from .skill_generation import generate_with_skill, preflight_output
from .controlled_variants import normalize_controlled_variant_topics


DEFAULT_DATASET_MODEL = "gemini:gemini-3.7-flash"
BUILTIN_SKILL_DIR = Path(__file__).resolve().parent / "skill_assets" / "knowledge-point-qa"
QUESTION_TYPES = ("free", "auto", "single-choice", "multiple-choice", "ranking")


from .work_session import stage_session


@language_stage
@stage_session("generation")
def create_test_dataset(
    knowledge: KnowledgeInput | None = None,
    *,
    from_github: str | None = None,
    github_client: Any = None,
    output: str | Path | None = None,
    count: int = 0,
    question_type: str = "free",
    controlled_variant_topics: Iterable[str] = (),
    controlled_variant_selector: Callable[[tuple[dict[str, Any], ...]], Iterable[str]] | None = None,
    seed: int = 0,
    model: str = DEFAULT_DATASET_MODEL,
    env_file: str | Path = ".env",
    temperature: float = 0.0,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    auto_window_ratio: float | None = None,
    resume: str | Path | None = None,
    force: bool = False,
    verbose: bool = True,
    skill: str | Path | None = None,
    skill_agent_factory: Callable[..., Any] | None = None,
    lang: str | None = None,
) -> list[dict[str, Any]]:
    """Generate source-grounded QA with the bundled or selected local skill."""
    if (knowledge is None) == (from_github is None):
        raise ValueError("choose exactly one of knowledge or from_github")
    if count < 0:
        raise ValueError("count must be zero or greater")
    if question_type not in QUESTION_TYPES:
        raise ValueError(f"unknown question type: {question_type}")
    controlled_variant_topics = normalize_controlled_variant_topics(controlled_variant_topics)
    profile = resolve_model_profile(
        model,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        auto_window_ratio=auto_window_ratio,
    )
    if output is not None:
        preflight_output(Path(output), force=force)
    if from_github is not None:
        from .github_dataset import GithubClient, artifact_paths, generate_from_github, repository_identity
        repository_identity(from_github)
        if output is None:
            raise ValueError("GitHub generation requires output to save its mandatory evidence report")
        preflight_output(Path(output), force=force, extra_paths=artifact_paths(Path(output)))
        if github_client is None:
            github_client = GithubClient()
        return generate_from_github(
            from_github, output=Path(output), client=github_client, skill=skill,
            agent_factory=skill_agent_factory, force=force,
            options=dict(count=count, question_type=question_type,
                         controlled_variant_topics=controlled_variant_topics,
                         controlled_variant_selector=controlled_variant_selector, seed=seed,
                         model=model, env_file=env_file, temperature=temperature, profile=profile, verbose=verbose),
        )
    result = generate_with_skill(
        knowledge,
        skill=Path(skill) if skill is not None else BUILTIN_SKILL_DIR,
        agent_factory=skill_agent_factory,
        count=count,
        question_type=question_type,
        controlled_variant_topics=controlled_variant_topics,
        controlled_variant_selector=controlled_variant_selector,
        seed=seed,
        model=model,
        env_file=env_file,
        temperature=temperature,
        profile=profile,
        verbose=verbose,
        allow_candidate_files=skill is not None,
    )
    if output is not None:
        result.publish(Path(output), force=force)
    return result.records
