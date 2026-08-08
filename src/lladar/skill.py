from __future__ import annotations

import hashlib
import importlib.resources
import json
from pathlib import Path
from typing import Any

from .exceptions import LladarError

SKILL_NAME = "lladar-agent-evaluation"
SKILL_TARGETS = {
    "codex": Path(".codex") / "skills",
    "claude": Path(".claude") / "skills",
    "antigravity": Path(".agents") / "skills",
}
_MANIFEST = ".lladar-skill-install.json"


class SkillError(LladarError):
    pass


def _resource_root():
    return importlib.resources.files("lladar").joinpath("skill_assets", SKILL_NAME)


def _resource_files() -> list[str]:
    root = _resource_root()
    files: list[str] = []
    for entry in root.rglob("*"):
        if entry.is_file():
            files.append(str(entry.relative_to(root)).replace("\\", "/"))
    return sorted(files)


def _resource_bytes(relative_path: str) -> bytes:
    return _resource_root().joinpath(*relative_path.split("/")).read_bytes()


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("lladar")
    except Exception:
        return "unknown"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _target_root(target: str, project_root: str | Path = ".") -> Path:
    if target not in SKILL_TARGETS:
        raise SkillError(f"Unknown skill target: {target}")
    return Path(project_root).resolve() / SKILL_TARGETS[target] / SKILL_NAME


def _target_names(target: str) -> list[str]:
    if target == "all":
        return list(SKILL_TARGETS)
    if target not in SKILL_TARGETS:
        raise SkillError(f"Unknown skill target: {target}")
    return [target]


def _read_manifest(destination: Path) -> dict[str, Any] | None:
    path = destination / _MANIFEST
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkillError(f"Invalid skill install manifest: {path}") from error
    return value if isinstance(value, dict) else None


def _modified_files(destination: Path, manifest: dict[str, Any] | None) -> list[str]:
    if manifest is None:
        return ["<unmanaged skill directory>"] if destination.exists() else []
    modified: list[str] = []
    for relative_path, expected_hash in manifest.get("files", {}).items():
        path = destination / relative_path
        if not path.is_file() or _hash_bytes(path.read_bytes()) != expected_hash:
            modified.append(relative_path)
    return modified


def install_skill(
    target: str,
    *,
    project_root: str | Path = ".",
    force: bool = False,
) -> list[Path]:
    installed: list[Path] = []
    files = _resource_files()
    for target_name in _target_names(target):
        destination = _target_root(target_name, project_root)
        manifest = _read_manifest(destination)
        modified = _modified_files(destination, manifest)
        if modified and not force:
            details = ", ".join(modified[:5])
            raise SkillError(
                f"Skill {target_name} has modified files ({details}); use --force to replace it"
            )
        destination.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        for relative_path in files:
            path = destination / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            content = _resource_bytes(relative_path)
            path.write_bytes(content)
            hashes[relative_path] = _hash_bytes(content)
        (destination / _MANIFEST).write_text(
            json.dumps(
                {
                    "skill": SKILL_NAME,
                    "package_version": _package_version(),
                    "target": target_name,
                    "files": hashes,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        installed.append(destination)
    return installed


def list_skills(*, project_root: str | Path = ".") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for target in SKILL_TARGETS:
        destination = _target_root(target, project_root)
        if destination.exists():
            manifest = _read_manifest(destination)
            result.append(
                {
                    "target": target,
                    "path": str(destination),
                    "installed": manifest is not None,
                    "package_version": manifest.get("package_version") if manifest else None,
                }
            )
    return result


def uninstall_skill(
    target: str,
    *,
    project_root: str | Path = ".",
    force: bool = False,
) -> list[Path]:
    removed: list[Path] = []
    for target_name in _target_names(target):
        destination = _target_root(target_name, project_root)
        if not destination.exists():
            continue
        manifest = _read_manifest(destination)
        modified = _modified_files(destination, manifest)
        if modified and not force:
            details = ", ".join(modified[:5])
            raise SkillError(
                f"Skill {target_name} has modified files ({details}); use --force to remove it"
            )
        for path in sorted(destination.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        destination.rmdir()
        removed.append(destination)
    return removed
