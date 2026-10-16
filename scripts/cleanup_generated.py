"""Review and remove generated LLaDAR test and build directories.

Use ``--all-generated`` to select test/build caches, runs/, and legacy
root response/evaluation artifacts together. This includes saved answers,
resume checkpoints, exported adapters and captured state in those artifacts.
It does not select .lladar login profiles or root .env/.venv. Preview first;
add ``--apply`` and type DELETE to remove the displayed items.

Run ``python scripts/cleanup_generated.py`` to preview. Add
``--include-pytest-state`` to include generated pytest trees that contain
browser profiles or .env files. Use ``--clear-lladar`` to preview removal of
all contents of .lladar, including run history, browser profiles, caches, and
backups. Use ``--clear-tmp`` to preview removal of .tmp. Run with
``--apply`` and type DELETE to remove the displayed items.

Use ``--clean-root`` to preview old root-level operation reports, evaluation
workspaces, test/build output, and local diagram/index output. Add
``--include-captured-state`` to include recordings and possible credentials
inside those selected artifacts. This mode never selects .env, .venv, logs,
.lladar, source files, documentation, or tracked files.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
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
ROOT_ARTIFACT_DIRECTORIES = (
    ".tmp",
    ".pytest_cache",
    "build",
    "dist",
    "graphify-out",
    ".archify",
    "linux-venv",
    "practical-tests-log",
)
EVALUATION_WORKSPACE = re.compile(r"evaluation\.json\.work(?:-[0-9a-f]{8})?\Z")
OPERATION_REPORT = re.compile(
    r"(?:evaluation\.json|responses\.jsonl)\.operation(?:-\d+)?\.md\Z"
)
RUN_ARTIFACT = re.compile(
    r"(?:responses?\.jsonl(?:\.(?:run\.json|trials\.jsonl|turns\.jsonl|"
    r"scenarios\.jsonl|calibration\.json|operation(?:-\d+)?\.md|"
    r"work(?:-[0-9a-f]{8})?|api-adapter))?|"
    r"evaluation\.json(?:\.(?:work(?:-[0-9a-f]{8})?|operation(?:-\d+)?\.md))?)\Z"
)


def run_artifact(path: Path) -> bool:
    return path.parent == ROOT and (path.name == "runs" or bool(RUN_ARTIFACT.fullmatch(path.name)))


def candidates(
    clear_lladar: bool = False,
    clear_tmp: bool = False,
    clean_root: bool = False,
    all_generated: bool = False,
) -> list[Path]:
    if all_generated:
        # Editable installations need their package metadata to keep working.
        paths = [path for path in candidates() if path != ROOT / "src" / "lladar.egg-info"]
        paths += [path for path in ROOT.iterdir() if run_artifact(path)]
        return sorted(set(paths), key=str)
    if clean_root:
        paths = [ROOT / name for name in ROOT_ARTIFACT_DIRECTORIES]
        paths.extend(
            path
            for path in ROOT.iterdir()
            if (path.is_dir() and EVALUATION_WORKSPACE.fullmatch(path.name))
            or (path.is_file() and OPERATION_REPORT.fullmatch(path.name))
        )
        return sorted(
            {path for path in paths if path.exists() or path.is_symlink()}, key=str
        )
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
    paths = [*ROOT.glob(".pytest-*"), *ROOT.glob(".pytest_*"),
             *(ROOT / ".tmp").glob("pytest-*")]
    paths += [ROOT / ".tmp" / "pytest", ROOT / ".tmp" / "pytest-cache"]
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
        (path.parent == ROOT and path.name.startswith((".pytest-", ".pytest_")))
        or path in {ROOT / ".tmp" / "pytest", ROOT / ".tmp" / "pytest-cache"}
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
    clean_root: bool = False,
    include_captured_state: bool = False,
    all_generated: bool = False,
) -> str | None:
    """Return a reason to keep an item, or None when it can be deleted."""
    root = ROOT.resolve()
    if clean_root and not (
        path.parent == ROOT
        and (
            path.name in ROOT_ARTIFACT_DIRECTORIES
            or EVALUATION_WORKSPACE.fullmatch(path.name)
            or OPERATION_REPORT.fullmatch(path.name)
        )
    ):
        return "not a selected root artifact"
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return "link or non-directory"
    selected_file = path.is_file() and (
        (clear_lladar and path.parent == ROOT / ".lladar")
        or (clear_tmp and path.parent == ROOT / ".tmp")
        or (clean_root and path.parent == ROOT and OPERATION_REPORT.fullmatch(path.name))
        or (all_generated and run_artifact(path))
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
    allow_generated_state = all_generated and (run_artifact(path) or pytest_temp(path))
    allow_sensitive = allow_generated_state or allow_lladar_state or (clean_root and include_captured_state) or (
        include_pytest_state and pytest_temp(path)
    )
    if path.is_file():
        if path.suffix.lower() in SENSITIVE_EXTENSIONS and not allow_sensitive:
            return "key or certificate file"
        if sensitive_name(path) and not allow_sensitive:
            return "possible credentials or browser state"
        return None
    try:
        pending = [path]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if entry.is_symlink() or (
                        hasattr(entry, "is_junction") and entry.is_junction()
                    ):
                        # The old Linux virtualenv contains links such as
                        # lib64 -> lib. rmtree removes links without following
                        # them; keep the exception confined to this one
                        # explicitly selected, disposable environment.
                        if not allow_generated_state and not (
                            clean_root
                            and include_captured_state
                            and path == ROOT / "linux-venv"
                        ):
                            return "contains a link"
                        continue
                    if not allow_sensitive:
                        if Path(entry.name).suffix.lower() in SENSITIVE_EXTENSIONS:
                            return "contains a key or certificate file"
                        if sensitive_name(Path(entry.name)):
                            return "contains possible credentials or browser state"
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
    except OSError:
        return "cannot inspect every file"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete safe candidates after confirmation")
    parser.add_argument(
        "--all-generated", action="store_true",
        help="select test/build caches, runs/ and legacy root response/evaluation artifacts, including answers, resume and captured state",
    )
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
    parser.add_argument(
        "--clean-root",
        action="store_true",
        help="remove fixed root-level generated artifacts and operation reports",
    )
    parser.add_argument(
        "--include-captured-state",
        action="store_true",
        help="with --clean-root, also remove selected artifacts containing browser state or credentials",
    )
    args = parser.parse_args()
    if args.all_generated and (args.clean_root or args.clear_lladar or args.clear_tmp
                              or args.include_pytest_state or args.include_captured_state):
        parser.error("--all-generated cannot be combined with other cleanup modes")
    if args.clean_root and (args.clear_lladar or args.clear_tmp or args.include_pytest_state):
        parser.error("--clean-root cannot be combined with other cleanup modes")
    if args.include_captured_state and not args.clean_root:
        parser.error("--include-captured-state requires --clean-root")

    tracked = tracked_files()
    safe: list[Path] = []
    kept: list[tuple[Path, str]] = []
    for path in candidates(args.clear_lladar, args.clear_tmp, args.clean_root, args.all_generated):
        if args.clean_root and path.is_dir():
            print(f"Inspecting {path.relative_to(ROOT)}...", flush=True)
        reason = classify(
            path, tracked, args.include_pytest_state, args.clear_lladar,
            args.clear_tmp, args.clean_root, args.include_captured_state,
            args.all_generated,
        )
        if reason:
            kept.append((path, reason))
        else:
            safe.append(path)

    print(f"Project: {ROOT}")
    print("Mode: APPLY (confirmation required)" if args.apply else "Mode: PREVIEW ONLY (no files will be deleted)")
    if args.all_generated:
        print("Selected run artifacts include answers, resume checkpoints, adapters and captured state; deleting them loses that data.")
    print(f"Removal candidates: {len(safe)}")
    reports = [path for path in safe if path.parent == ROOT and OPERATION_REPORT.fullmatch(path.name)]
    if reports:
        print(f"  SELECT {len(reports)} root operation reports (evaluation.json/responses.jsonl)")
    for path in safe:
        if path in reports:
            continue
        print(f"  SELECT {path.relative_to(ROOT)}")
    print(f"Preserved items: {len(kept)}")
    for path, reason in kept:
        print(f"  KEEP   {path.relative_to(ROOT)} ({reason})")

    if not args.apply:
        print("PREVIEW ONLY: nothing was deleted.")
        flags = [flag for flag in (
            "--all-generated" if args.all_generated else "",
            "--clean-root" if args.clean_root else "",
            "--clear-lladar" if args.clear_lladar else "",
            "--clear-tmp" if args.clear_tmp else "",
            "--include-pytest-state" if args.include_pytest_state else "",
            "--include-captured-state" if args.include_captured_state else "",
        ) if flag]
        tokens = ["python", str(Path(__file__).resolve()), *flags, "--apply"]
        command = subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)
        print(f"To delete, run: {command}")
        print("Then type DELETE at the confirmation prompt.")
        return
    if not safe:
        return
    item_label = "item" if len(safe) == 1 else "items"
    answer = input(f"Type DELETE to remove {len(safe)} {item_label}: ")
    if answer != "DELETE":
        print("Cancelled; nothing was removed.")
        return

    removed = 0
    tracked = tracked_files()
    for path in safe:
        reason = classify(
            path, tracked, args.include_pytest_state, args.clear_lladar,
            args.clear_tmp, args.clean_root, args.include_captured_state,
            args.all_generated,
        )
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
