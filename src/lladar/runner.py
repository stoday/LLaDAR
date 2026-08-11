from __future__ import annotations

import json
import shutil
import os
import subprocess
import sys
from contextlib import contextmanager
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .progress import ProgressReporter


Answerer = Callable[[str], str]


class AdapterController:
    def adapt(self, workspace: Path, entrypoint: Path) -> None:
        raise NotImplementedError


class SandboxTools:
    """Small root-confined tool surface exposed to an adaptation controller."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def _path(self, relative_path: str | Path) -> Path:
        candidate = (self.workspace / relative_path).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as error:
            raise ValueError(f"path escapes workspace: {relative_path}") from error
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

    def replace_text(self, relative_path: str, old: str, new: str) -> None:
        path = self._path(relative_path)
        content = self.read_file(relative_path)
        occurrences = content.count(old)
        if occurrences != 1:
            raise ValueError(f"expected exactly one match, found {occurrences}")
        path.write_text(content.replace(old, new), encoding="utf-8", newline="\n")


class AkashaAdapterController(AdapterController):
    """Use Akasha tool calling to adapt only a managed project copy."""

    def __init__(self, *, model: str = "gemini:gemini-2.5-flash", env_file: str | Path = ".env"):
        self.model = model
        self.env_file = str(env_file)

    def adapt(self, workspace: Path, entrypoint: Path) -> None:
        import akasha

        tools = SandboxTools(workspace)
        akasha_tools = [
            akasha.create_tool(
                "List entries in a path relative to the project workspace.",
                tools.list_directory,
                "list_directory",
            ),
            akasha.create_tool(
                "Read one UTF-8 text file relative to the project workspace.",
                tools.read_file,
                "read_file",
            ),
            akasha.create_tool(
                "Search text files in the project workspace for an exact text query.",
                tools.search,
                "search_files",
            ),
            akasha.create_tool(
                "Replace exactly one matching text span in a workspace file.",
                tools.replace_text,
                "replace_text",
            ),
        ]
        agent = akasha.agents(
            model=self.model,
            env_file=self.env_file,
            tools=akasha_tools,
            stream=False,
            thinking=False,
            verbose=False,
            keep_logs=False,
        )
        agent(
            """Adapt the copied project so its entrypoint can answer one question
provided by the LLADAR_QUESTION environment variable. Inspect the project
with tools, change only the copied entrypoint or its local helper files, and
make the smallest exact replacement. Do not add a fake answer or change the
agent's provider, knowledge source, or answer logic. The entrypoint is:
""" + str(entrypoint.relative_to(workspace))
        )
        if "LLADAR_QUESTION" not in entrypoint.read_text(encoding="utf-8"):
            raise RuntimeError("adapter did not prove LLADAR_QUESTION injection")


def resolve_project_python(project: str | Path) -> Path:
    """Find the project's existing interpreter without copying its venv."""
    root = Path(project).resolve()
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


@contextmanager
def copy_project(project: str | Path, *, runs_root: str | Path | None = None):
    """Yield a managed project copy with secrets and local state excluded."""
    source = Path(project).resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"project is not a directory: {source}")
    root = Path(runs_root or (Path.cwd() / ".lladar" / "runs")).resolve()
    run_name = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    run_root = root / run_name
    workspace = run_root / source.name
    run_root.mkdir(parents=True, exist_ok=False)
    ignored_names = shutil.ignore_patterns(
        ".env",
        ".env.*",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".lladar",
        "*.pyc",
    )
    try:
        shutil.copytree(source, workspace, ignore=ignored_names)
        yield workspace
    finally:
        pass


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object")
            yield value


def run_agent(
    dataset: str | Path,
    output: str | Path,
    *,
    answer: Answerer | None = None,
    project: str | Path | None = None,
    entrypoint: str | Path | None = None,
    adapter: AdapterController | None = None,
    env_file: str | Path | None = None,
    force: bool = False,
    verbose: bool = True,
    runs_root: str | Path | None = None,
) -> int:
    """Run an answer callback or project entrypoint and write id-keyed JSONL."""
    output_path = Path(output)
    if output_path.exists() and not force:
        raise FileExistsError(f"output already exists: {output_path}")
    if (answer is None) == (project is None):
        raise ValueError("provide exactly one of answer or project")
    if project is not None and entrypoint is None:
        raise ValueError("entrypoint is required when project is provided")

    items = list(_read_jsonl(Path(dataset)))
    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {
            "dataset": dataset,
            "project": project,
            "entrypoint": entrypoint,
            "items": len(items),
            "output": output,
            "verbose": verbose,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    project_python = resolve_project_python(project) if project is not None else Path(sys.executable)
    workspace_context = (
        copy_project(project, runs_root=runs_root)
        if project is not None
        else _empty_context()
    )
    with workspace_context as workspace:
        adapted_entrypoint = None
        if project is not None:
            adapted_entrypoint = workspace / Path(entrypoint)  # type: ignore[arg-type]
            if not adapted_entrypoint.is_file():
                raise FileNotFoundError(f"entrypoint not found: {entrypoint}")
            reporter.emit("SOURCE", f"workspace={workspace}")
            reporter.emit("CHUNK", f"entrypoint={entrypoint}")
            try:
                (adapter or _require_adapter()).adapt(workspace, adapted_entrypoint)
            except Exception as error:
                reporter.emit(
                    "WARN",
                    f"stage=adapt error_type={type(error).__name__}",
                )
                raise
        with output_path.open("w", encoding="utf-8", newline="\n") as target:
            total = len(items)
            for index, item in enumerate(items, start=1):
                result: dict[str, Any] = {"id": item.get("id")}
                question = item.get("underspecified_question")
                if not isinstance(question, str) or not question.strip():
                    result.update(
                        status="error",
                        error="Missing non-empty question field: underspecified_question",
                    )
                else:
                    try:
                        if answer is not None:
                            response = answer(question)
                        else:
                            response = _run_entrypoint(
                                adapted_entrypoint,
                                workspace,
                                question,
                                python_executable=project_python,
                                env_file=env_file,
                            )
                        result.update(
                            status="ok",
                            answer=response if isinstance(response, str) else str(response),
                        )
                        completed += 1
                    except Exception as error:  # Keep later dataset items runnable.
                        result.update(status="error", error=f"{type(error).__name__}: {error}")
                        reporter.emit(
                            "WARN",
                            f"item={item.get('id')} stage=answer error_type={type(error).__name__}",
                        )
                target.write(json.dumps(result, ensure_ascii=False) + "\n")
                target.flush()
                reporter.pair(index, total, result["status"])
    reporter.done(completed)
    return completed


@contextmanager
def _empty_context():
    yield None


def _require_adapter() -> AdapterController:
    raise ValueError("an adapter controller is required for project mode")


def _run_entrypoint(
    entrypoint: Path | None,
    workspace: Path,
    question: str,
    *,
    python_executable: Path,
    env_file: str | Path | None,
) -> str:
    if entrypoint is None:
        raise ValueError("entrypoint is required for project mode")
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    if env_file is not None:
        from dotenv import dotenv_values

        for key, value in dotenv_values(env_file).items():
            if value is not None:
                environment.setdefault(key, value)
    environment["LLADAR_QUESTION"] = question
    completed = subprocess.run(
        [str(python_executable), str(entrypoint)],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(detail)
    response = completed.stdout.strip()
    if not response:
        raise RuntimeError("agent produced no stdout answer")
    return response
