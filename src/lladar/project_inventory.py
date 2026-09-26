"""Stable project inventory used by adapter discovery."""

import hashlib
import os
from pathlib import Path


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ValueError(f"Project directory is missing: {root}")
    resolved_root = root.resolve()
    ignored = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".lladar",
        ".vibe-testing",
    }
    result: dict[str, str] = {}
    for directory, names, files in os.walk(root):
        names[:] = [
            name
            for name in names
            if name not in ignored
            and not name.startswith(".env")
            and not (Path(directory) / name).is_symlink()
            and (Path(directory) / name).resolve().is_relative_to(resolved_root)
        ]
        for name in files:
            path = Path(directory) / name
            if (
                name == ".env"
                or name.startswith(".env.")
                or path.suffix == ".pyc"
                or path.is_symlink()
            ):
                continue
            result[path.relative_to(root).as_posix()] = fingerprint(path)
    return result
