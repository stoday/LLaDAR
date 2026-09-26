"""Shared local-skill boundary for run, evaluation, and reporting methods."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .exceptions import LladarError


SkillAgentFactory = Callable[..., Any]


def resolve_skill(skill: str | Path | None, builtin: Path) -> Path:
    """Return one validated local skill directory, never a prompt fallback."""
    path = Path(skill) if skill is not None else builtin
    path = path.resolve()
    if not (path / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill is missing SKILL.md: {path}")
    from akasha.agent.skills.loader import load_skill_directory
    selected = load_skill_directory(path)
    if not selected.instructions.strip():
        raise ValueError("SKILL.md must contain method instructions")
    return path


def invoke_skill(
    *,
    skill: Path,
    tools: dict[str, Callable[..., Any]],
    request: dict[str, Any],
    model: str,
    env_file: str | Path,
    system_prompt: str,
    agent_factory: SkillAgentFactory | None = None,
) -> dict[str, Any]:
    """Run one native local skill and require evidence that it was loaded."""
    if agent_factory is None:
        from .skill_agent import AkashaSkillAgent
        agent_factory = AkashaSkillAgent
    agent = agent_factory(
        skills=[str(skill)], tools=tools, model=model, env_file=str(env_file),
        system_prompt=system_prompt,
    )
    evidence = agent(request)
    if not isinstance(evidence, dict) or skill.name not in evidence.get("loaded_skills", []):
        raise LladarError("agent did not load the selected skill")
    files = evidence.get("skill_files", {})
    if not isinstance(files, dict) or "SKILL.md" not in files:
        raise LladarError("skill execution did not provide SKILL.md evidence")
    return evidence
