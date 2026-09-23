from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .progress import ProgressReporter
from .records import read_records, write_records


Answerer = Callable[[str], str]


class SandboxTools:
    """Root-confined read-only tools used for project inspection."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def _path(self, relative_path: str | Path) -> Path:
        candidate = (self.workspace / relative_path).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError(f"path escapes workspace: {relative_path}")
        return candidate

    def list_directory(self, relative_path: str = ".") -> list[str]:
        path = self._path(relative_path)
        if not path.is_dir():
            raise NotADirectoryError(relative_path)
        return sorted(item.name for item in path.iterdir())

    def read_file(self, relative_path: str) -> str:
        path = self._path(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")

    def search(self, query: str, relative_path: str = ".") -> list[str]:
        root = self._path(relative_path)
        matches: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".txt", ".toml"}:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if query in content:
                matches.append(str(path.relative_to(self.workspace)))
        return sorted(matches)


def resolve_project_python(project: str | Path) -> Path:
    root = Path(project).resolve()
    for candidate in (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    raise ValueError(
        "No target .venv found. Create the project's environment or supply --target-python."
    )


@contextmanager
def copy_project(project: str | Path, *, runs_root: str | Path | None = None):
    source = Path(project).resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"project is not a directory: {source}")
    root = Path(runs_root or (Path.cwd() / ".lladar" / "runs")).resolve()
    run_root = root / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    workspace = run_root / source.name
    run_root.mkdir(parents=True, exist_ok=False)
    base_ignore = shutil.ignore_patterns(
        ".env", ".env.*", ".git", ".venv", "venv", "__pycache__",
        ".pytest_cache", ".lladar", ".vibe-testing", "node_modules", "*.pyc",
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(base_ignore(directory, names))
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(source):
                ignored.add(name)
        return ignored

    shutil.copytree(source, workspace, ignore=ignore)
    yield workspace


def run_agent(
    dataset: str | Path,
    output: str | Path,
    *,
    answer: Answerer | None = None,
    project: str | Path | None = None,
    env_file: str | Path | None = None,
    force: bool = False,
    verbose: bool = True,
    runs_root: str | Path | None = None,
    model: str = "gemini:gemini-2.5-flash",
    target_python: str | Path | None = None,
    timeout: float = 120,
    max_tool_calls: int = 100,
    max_cases: int | None = None,
    interactive: bool | None = None,
    intent: str = "",
    graphify: bool = True,
    graphify_python: str | Path | None = None,
    service_url: str | None = None,
) -> int:
    """Fill actual_response in a new JSONL file without evaluating it."""
    if (answer is None) == (project is None):
        raise ValueError("provide exactly one of answer or project")
    if timeout <= 0 or max_tool_calls <= 0:
        raise ValueError("timeout and max_tool_calls must be positive")
    if max_cases is not None and max_cases <= 0:
        raise ValueError("max_cases must be positive")

    records = read_records(dataset)
    if any(record["actual_response"] is not None for record in records):
        raise ValueError("run-agent requires records with actual_response set to null")
    selected = records if max_cases is None else records[:max_cases]
    output_path = Path(output)
    run_path = output_path.with_name(output_path.name + ".run.json")
    for candidate in (output_path, run_path):
        if candidate.exists() and not force:
            raise FileExistsError(f"output already exists: {candidate}")

    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {"dataset": dataset, "project": project, "records": len(selected), "output": output}
    )
    target_python_path = Path(sys.executable)
    if project is not None:
        target_python_path = (
            Path(target_python).resolve() if target_python else resolve_project_python(project)
        )
        from .target_environment import validate_target_python

        validate_target_python(target_python_path)

    context = (
        copy_project(project, runs_root=runs_root)
        if project is not None
        else _empty_context()
    )
    completed = 0
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    with context as workspace:
        automatic = None
        if project is not None and selected:
            from .auto_adapter import AutoAdapter

            automatic = AutoAdapter(
                workspace,
                python=target_python_path,
                env_file=env_file,
                model=model,
                timeout=timeout,
                max_tool_calls=max_tool_calls,
                verbose=verbose,
                graphify=graphify,
                graphify_python=graphify_python,
                service_url=service_url,
            )
            probes = list(dict.fromkeys(record["question"] for record in selected))[:2]
            automatic.prepare(probes, interactive=interactive, intent=intent)

        for index, record in enumerate(selected, 1):
            result = dict(record)
            try:
                if answer is not None:
                    response = answer(record["question"])
                elif automatic is not None:
                    response = automatic.answer(record["question"], f"record-{index}")
                else:
                    raise RuntimeError("automatic adapter was not prepared")
                result["actual_response"] = (
                    response if isinstance(response, str) else str(response)
                )
                completed += 1
                status = "ok"
            except Exception as error:
                result["actual_response"] = None
                status = "execution_error"
                errors.append(
                    {"line": index, "error": f"{type(error).__name__}: {error}"}
                )
                reporter.emit("WARN", f"line={index} error_type={type(error).__name__}")
            results.append(result)
            reporter.session(index, len(selected), status)

    write_records(results, output_path, overwrite=force)
    run_record = {
        "run_id": datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f"),
        "dataset": str(Path(dataset).resolve()),
        "responses": str(output_path.resolve()),
        "responses_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "total": len(selected),
        "completed": completed,
        "failed": len(errors),
        "errors": errors,
        "target": {
            "project": str(Path(project).resolve()) if project is not None else None,
            "adapter_model": model if project is not None else None,
        },
    }
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        json.dumps(run_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reporter.done(completed, metric="completed")
    return completed


@contextmanager
def _empty_context():
    yield None