"""Create editable local method skills from the bundled methods."""

from __future__ import annotations

import re
from pathlib import Path


_ASSETS = Path(__file__).resolve().parent
_METHODS = {
    "test-dataset": "knowledge-point-qa",
    "situation": "create-situation",
    "run-agent": "run-agent-stability",
    "eval": "eval-answer-verdict",
    "report": "report-evidence-summary",
}
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def create_skill_template(stage: str, output: str | Path | None = None) -> Path:
    destination = Path(output) if output is not None else Path("lladar-skills") / stage
    destination = destination.resolve()
    if not _NAME.fullmatch(destination.name):
        raise ValueError(f"skill directory name must use lowercase letters, digits and hyphens: {destination.name}")
    if destination.exists():
        raise FileExistsError(f"skill directory already exists: {destination}")

    source = _ASSETS / "skill_assets" / _METHODS[stage] / "SKILL.md"
    skill_text = source.read_text(encoding="utf-8")
    skill_text = re.sub(r"(?m)^name: .+$", f"name: {destination.name}", skill_text, count=1)
    active_guide = _ASSETS / "template_assets" / f"{stage}.skill.md"
    if active_guide.is_file():
        skill_text = skill_text.rstrip() + "\n\n" + active_guide.read_text(encoding="utf-8")
    guide_text = (_ASSETS / "template_assets" / f"{stage}.md").read_text(encoding="utf-8")

    destination.mkdir(parents=True)
    try:
        (destination / "SKILL.md").write_text(skill_text, encoding="utf-8", newline="\n")
        (destination / "AUTHORING.md").write_text(guide_text, encoding="utf-8", newline="\n")
    except OSError:
        for name in ("SKILL.md", "AUTHORING.md"):
            try:
                (destination / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            destination.rmdir()
        except OSError:
            pass
        raise
    return destination
