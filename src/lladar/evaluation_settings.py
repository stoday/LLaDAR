"""Selection and provenance for one evaluation's authoritative method."""
from __future__ import annotations

from pathlib import Path
import hashlib
from typing import Any


def validate_evaluation_selection(criteria: str | None, skill: str | Path | None) -> None:
    if criteria is not None and skill is not None:
        raise ValueError("criteria and skill are mutually exclusive")
    if criteria is not None and (not isinstance(criteria, str) or not criteria.strip()):
        raise ValueError("criteria must be a nonempty string")


def evaluation_settings(*, mode: str, criteria: str | None = None,
                        skill: dict[str, Any] | None = None,
                        config_evaluation: dict[str, Any] | None = None) -> dict[str, Any]:
    settings: dict[str, Any] = {"mode": mode}
    if criteria is not None:
        settings.update(criteria=criteria,
                        criteria_sha256=hashlib.sha256(criteria.encode("utf-8")).hexdigest())
    if skill is not None:
        settings["skill"] = skill
    if config_evaluation is not None:
        settings["config_evaluation"] = config_evaluation
    return settings
