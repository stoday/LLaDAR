"""Persist enough state to resume interface selection in a new process."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .interfaces import write_json


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ValueError(f"Project directory is missing: {root}")
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".lladar", ".vibe-testing"}
    result = {}
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in ignored and not name.startswith(".env")
                    and not (Path(directory) / name).is_symlink()
                    and (Path(directory) / name).resolve().is_relative_to(root.resolve())]
        for name in files:
            path = Path(directory) / name
            if name == ".env" or name.startswith(".env.") or path.suffix == ".pyc" or path.is_symlink():
                continue
            result[path.relative_to(root).as_posix()] = fingerprint(path)
    return result


def save_context(workspace: Path, *, dataset, project, output, python, env_file,
                 model, timeout, max_tool_calls, intent, graphify=True, graphify_python=None,
                 service_url=None) -> None:
    context = {
        "version": 1, "workspace": str(workspace.resolve()),
        "dataset": str(Path(dataset).resolve()), "dataset_hash": fingerprint(Path(dataset)),
        "project": str(Path(project).resolve()), "project_hashes": inventory(Path(project).resolve()),
        "workspace_hashes": inventory(workspace), "output": str(Path(output).resolve()),
        "target_python": str(python), "env_file": str(Path(env_file).resolve()) if env_file else None,
        "model": model, "timeout": timeout, "max_tool_calls": max_tool_calls, "intent": intent,
        "graphify": graphify, "graphify_python": str(Path(graphify_python).absolute()) if graphify_python else None,
        "service_url": service_url,
    }
    write_json(workspace.parent / "run-context.json", context)


def load_context(run: str | Path) -> dict:
    root = Path(run).resolve()
    context = json.loads((root / "run-context.json").read_text(encoding="utf-8"))
    if context.get("version") != 1:
        raise ValueError("Unsupported saved run version")
    workspace = Path(context["workspace"]).resolve()
    if workspace.parent != root or not workspace.is_dir():
        raise ValueError("Saved workspace is missing or outside run directory")
    evidence = root / ("adapter-evidence" if workspace.name.casefold() == "adapter" else "adapter")
    report = json.loads((evidence / "run.json").read_text(encoding="utf-8"))
    if report.get("status") != "needs_confirmation":
        raise ValueError("Only needs_confirmation runs can be resumed")
    if fingerprint(Path(context["dataset"])) != context["dataset_hash"]:
        raise ValueError("Dataset changed since pause; start a new run")
    if inventory(Path(context["project"])) != context["project_hashes"]:
        raise ValueError("Target project changed since pause; start a new run")
    if inventory(workspace) != context["workspace_hashes"]:
        raise ValueError("Saved workspace changed since pause; start a new run")
    return context


@contextmanager
def resume_workspace(run: str | Path):
    root = Path(run).resolve()
    lock = root / ".resume.lock"
    try:
        with lock.open("x", encoding="utf-8") as output:
            output.write(str(os.getpid()))
    except FileExistsError as error:
        raise ValueError("This run is already being resumed (.resume.lock exists)") from error
    try:
        yield Path(load_context(root)["workspace"])
    finally:
        lock.unlink()
