"""Review and remove generated LLaDAR test and build directories.

Run ``python scripts/cleanup_generated.py`` to preview. Add
``--include-pytest-state`` to include generated pytest trees that contain
browser profiles or .env files. Use ``--clear-lladar`` to preview removal of
all contents of .lladar, including run history, browser profiles, caches, and
backups. Use ``--clear-tmp`` to preview removal of .tmp. Run with
``--apply`` and type DELETE to remove the displayed items.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SENSITIVE_EXTENSIONS = {".key", ".pem", ".pfx", ".p12"}
SENSITIVE_FRAGMENTS = (
    "browser-profile",
    "browser_profile",
    "user-data",
    "user_data",
    "auth-state",
    "auth_state",
    "login-state",
    "login_state",
    "cookie",
)


def candidates(clear_lladar: bool = False, clear_tmp: bool = False) -> list[Path]:
    if clear_lladar or clear_tmp:
        paths = []
        for name, selected in ((".lladar", clear_lladar), (".tmp", clear_tmp)):
            if not selected:
                continue
            root = ROOT / name
            if name == ".tmp":
                if root.exists() or root.is_symlink():
                    paths.append(root)
                continue
            if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
                paths.append(root)
            elif root.is_dir():
                paths.extend(root.iterdir())
        return sorted(paths, key=str)
    paths = [*ROOT.glob(".pytest-*"), *(ROOT / ".tmp").glob("pytest-*")]
    paths += list((ROOT / ".lladar").glob("pytest-*"))
    paths += list((ROOT / ".lladar").glob("terminal-build-tmp-*"))
    paths += [
        ROOT / "build",
        ROOT / "dist",
        ROOT / "src" / "lladar.egg-info",
        ROOT / ".pytest_cache",
        ROOT / ".tmp" / "skill-template-dist",
        ROOT / ".tmp" / "skill-template-dist-final",
        ROOT / ".tmp" / "skill-template-live-20261002",
    ]
    for source in ("src", "tests", "scripts", "examples", "example_project"):
        base = ROOT / source
        if not base.is_dir():
            continue
        for current, dirs, _ in os.walk(base, followlinks=False):
            paths += [Path(current) / name for name in dirs if name == "__pycache__"]
            dirs[:] = [
                name
                for name in dirs
                if name not in {"__pycache__", ".venv", "linux-venv", "node_modules"}
            ]
    return sorted({path for path in paths if path.exists()}, key=str)


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return {
        name.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for name in result.stdout.split(b"\0")
        if name
    }


def sensitive_name(path: Path) -> bool:
    name = path.name.lower()
    return name == ".env" or any(part in name for part in SENSITIVE_FRAGMENTS) or path.suffix.lower() in SENSITIVE_EXTENSIONS


def pytest_temp(path: Path) -> bool:
    return (
        (path.parent == ROOT and path.name.startswith(".pytest-"))
        or (path.parent in {ROOT / ".tmp", ROOT / ".lladar"} and path.name.startswith("pytest-"))
    )


def deletion_path(path: Path) -> str:
    """Use the Windows extended path form for deeply nested test artifacts."""
    absolute = os.path.abspath(path)
    if os.name == "nt":
        if absolute.startswith("\\\\"):
            return "\\\\?\\UNC\\" + absolute[2:]
        return "\\\\?\\" + absolute
    return absolute


def classify(
    path: Path,
    tracked: set[str],
    include_pytest_state: bool,
    clear_lladar: bool = False,
    clear_tmp: bool = False,
) -> str | None:
    """Return a reason to keep an item, or None when it can be deleted."""
    root = ROOT.resolve()
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return "link or non-directory"
    selected_file = path.is_file() and (
        (clear_lladar and path.parent == ROOT / ".lladar")
        or (clear_tmp and path.parent == ROOT / ".tmp")
    )
    if not path.is_dir() and not selected_file:
        return "link or non-directory"
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return "outside project"
    if any(name == relative or name.startswith(relative + "/") for name in tracked):
        return "contains tracked files"
    allow_lladar_state = clear_lladar and path.parent == ROOT / ".lladar"
    allow_sensitive = allow_lladar_state or (
        include_pytest_state and pytest_temp(path)
    )
    if path.is_file():
        if path.suffix.lower() in SENSITIVE_EXTENSIONS and not allow_lladar_state:
            return "key or certificate file"
        if sensitive_name(path) and not allow_sensitive:
            return "possible credentials or browser state"
        return None
    errors: list[OSError] = []
    try:
        for current, dirs, files in os.walk(path, followlinks=False, onerror=errors.append):
            for name in dirs + files:
                entry = Path(current) / name
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    return "contains a link"
                if entry.suffix.lower() in SENSITIVE_EXTENSIONS and not allow_lladar_state:
                    return "contains a key or certificate file"
                if sensitive_name(entry) and not allow_sensitive:
                    return "contains possible credentials or browser state"
    except OSError:
        return "cannot inspect every file"
    if errors:
        return "cannot inspect every file"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete safe candidates after confirmation")
    parser.add_argument(
        "--include-pytest-state",
        action="store_true",
        help="include pytest temporary directories containing .env or browser profiles",
    )
    parser.add_argument(
        "--clear-lladar",
        action="store_true",
        help="remove only .lladar contents, including browser profiles, credentials, run history, and backups",
    )
    parser.add_argument(
        "--clear-tmp",
        action="store_true",
        help="remove .tmp, preserving it if it contains possible credentials or browser state",
    )
    args = parser.parse_args()

    tracked = tracked_files()
    safe: list[Path] = []
    kept: list[tuple[Path, str]] = []
    for path in candidates(args.clear_lladar, args.clear_tmp):
        reason = classify(path, tracked, args.include_pytest_state, args.clear_lladar, args.clear_tmp)
        if reason:
            kept.append((path, reason))
        else:
            safe.append(path)

    print(f"Project: {ROOT}")
    print(f"Removal candidates: {len(safe)}")
    for path in safe:
        print(f"  DELETE {path.relative_to(ROOT)}")
    print(f"Preserved items: {len(kept)}")
    for path, reason in kept:
        print(f"  KEEP   {path.relative_to(ROOT)} ({reason})")

    if not args.apply or not safe:
        return
    item_label = "item" if len(safe) == 1 else "items"
    answer = input(f"Type DELETE to remove {len(safe)} {item_label}: ")
    if answer != "DELETE":
        print("Cancelled; nothing was removed.")
        return

    removed = 0
    tracked = tracked_files()
    for path in safe:
        reason = classify(path, tracked, args.include_pytest_state, args.clear_lladar, args.clear_tmp)
        if reason:
            print(f"SKIP   {path.relative_to(ROOT)} ({reason})")
            continue
        try:
            target = deletion_path(path)
            if path.is_dir():
                shutil.rmtree(target)
            else:
                os.unlink(target)
            removed += 1
        except OSError as exc:
            print(f"ERROR  {path.relative_to(ROOT)} ({exc})")
    print(f"Removed {removed} {'item' if removed == 1 else 'items'}.")


if __name__ == "__main__":
    main()
