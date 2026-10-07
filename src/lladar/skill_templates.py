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


def create_skill_template(stage: str, output: str | Path | None = None, *, force: bool = False) -> Path:
    destination = Path(output) if output is not None else Path("lladar-skills") / stage
    destination = destination.resolve()
    if not _NAME.fullmatch(destination.name):
        raise ValueError(f"skill directory name must use lowercase letters, digits and hyphens: {destination.name}")
    existed = destination.exists()
    if existed and not force:
        raise FileExistsError(f"skill directory already exists: {destination}; use --force to overwrite the template files")
    if existed and not destination.is_dir():
        raise NotADirectoryError(f"skill destination is not a directory: {destination}")
    originals = {}
    for name in ("SKILL.md", "AUTHORING.md"):
        path = destination / name
        if path.is_symlink():
            raise ValueError(f"template file must not be a symbolic link: {path}")
        originals[name] = path.read_bytes() if path.exists() else None

    source = _ASSETS / "skill_assets" / _METHODS[stage] / "SKILL.md"
    skill_text = source.read_text(encoding="utf-8")
    skill_text = re.sub(r"(?m)^name: .+$", f"name: {destination.name}", skill_text, count=1)
    frontmatter_end = skill_text.index("\n---", 4) + len("\n---")
    introduction = (
        "\n\n## About this template\n\n"
        "The method below is the active default. Authors can find tool contracts, "
        "parameter definitions, and replacement examples in [AUTHORING.md](AUTHORING.md). "
        "That file is an author guide: its alternative examples are not additional "
        "execution instructions. Execute the active method in this SKILL.md.\n"
    )
    skill_text = skill_text[:frontmatter_end] + introduction + skill_text[frontmatter_end:]
    active_guide = _ASSETS / "template_assets" / f"{stage}.skill.md"
    if active_guide.is_file():
        skill_text = skill_text.rstrip() + "\n\n" + active_guide.read_text(encoding="utf-8")
    guide_text = (_ASSETS / "template_assets" / f"{stage}.md").read_text(encoding="utf-8")

    destination.mkdir(parents=True, exist_ok=force)
    try:
        (destination / "SKILL.md").write_text(skill_text, encoding="utf-8", newline="\n")
        (destination / "AUTHORING.md").write_text(guide_text, encoding="utf-8", newline="\n")
    except OSError:
        for name, original in originals.items():
            try:
                if original is None:
                    (destination / name).unlink(missing_ok=True)
                else:
                    (destination / name).write_bytes(original)
            except OSError:
                pass
        if not existed:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise
    return destination
