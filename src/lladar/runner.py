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

from .exceptions import DatasetValidationError
from .progress import ProgressReporter
from .validation import validate_dataset_item


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
    raise ValueError("No target .venv found. Create the project's own environment and install its dependencies, or supply --target-python PATH. LLaDAR's environment is not used as a fallback.")


def resolve_project_entrypoint(project: str | Path, entrypoint: str | Path) -> Path:
    """Return an entrypoint path relative to the original project root."""
    root = Path(project).resolve()
    supplied = Path(entrypoint)
    working_directory_candidate = supplied.resolve()
    if supplied.is_absolute() or working_directory_candidate.is_relative_to(root):
        absolute = working_directory_candidate
    else:
        absolute = (root / supplied).resolve()
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise ValueError(f"entrypoint is outside project: {entrypoint}") from error
    if not absolute.is_file():
        raise FileNotFoundError(f"entrypoint not found: {entrypoint}")
    return relative


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
        ".vibe-testing",
        "node_modules",
        "*.pyc",
    )
    def ignore_workspace_paths(directory, names):
        # Do not dereference links/junctions into secrets or another checkout.
        ignored = set(ignored_names(directory, names))
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(source):
                ignored.add(name)
        return ignored

    try:
        shutil.copytree(source, workspace, ignore=ignore_workspace_paths)
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


def _dataset_cases(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        group_id = item["id"]
        if group_id in seen_ids:
            raise DatasetValidationError(f"duplicate case id: {group_id}")
        seen_ids.add(group_id)
        if item.get("status") == "skipped":
            continue
        original = item.get("original")
        if isinstance(original, dict):
            cases.append(
                {
                    "id": group_id,
                    "group_id": group_id,
                    "kind": "original",
                    "question": original.get("question"),
                }
            )
        variants = item.get("variants")
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    variant_id = variant.get("id")
                    if variant_id in seen_ids:
                        raise DatasetValidationError(f"duplicate case id: {variant_id}")
                    seen_ids.add(variant_id)
                    cases.append(
                        {
                            "id": variant_id,
                            "group_id": group_id,
                            "kind": variant.get("kind"),
                            "question": variant.get("question"),
                        }
                    )
    return cases


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
    model: str = "gemini:gemini-2.5-flash",
    target_python: str | Path | None = None,
    timeout: float = 120,
    max_tool_calls: int = 100,
    interactive: bool | None = None,
    intent: str = "",
    graphify: bool = True,
    graphify_python: str | Path | None = None,
    service_url: str | None = None,
    resume_run: str | Path | None = None,
    candidate_id: str | None = None,
    clarification: str | None = None,
) -> int:
    """Run an answer callback or project entrypoint and write id-keyed JSONL."""
    output_path = Path(output)
    saved = None
    if resume_run is not None:
        from .run_context import load_context, resume_workspace

        saved = load_context(resume_run)
        if entrypoint is not None or answer is not None:
            raise ValueError("Resume requires automatic project mode")
        for key, value in (("dataset", dataset), ("project", project), ("output", output)):
            if value is None or Path(value).resolve() != Path(saved[key]).resolve():
                raise ValueError(f"Resume {key} differs from saved run")
    elif candidate_id is not None or clarification is not None:
        raise ValueError("Candidate selection and clarification require a paused run")
    if output_path.exists() and not force:
        raise FileExistsError(f"output already exists: {output_path}")
    if (answer is None) == (project is None):
        raise ValueError("provide exactly one of answer or project")
    if timeout <= 0 or max_tool_calls <= 0:
        raise ValueError("timeout and max_tool_calls must be positive")

    relative_entrypoint = (
        resolve_project_entrypoint(project, entrypoint)  # type: ignore[arg-type]
        if project is not None and entrypoint is not None
        else None
    )

    items = [
        validate_dataset_item(item, check_policy_references=False)
        for item in _read_jsonl(Path(dataset))
    ]
    cases = _dataset_cases(items)
    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {
            "dataset": dataset,
            "project": project,
            "entrypoint": entrypoint,
            "groups": sum(item.get("status") == "ready" for item in items),
            "sessions": len(cases),
            "output": output,
            "verbose": verbose,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    project_python = Path(sys.executable)
    if project is not None and cases:
        from .target_environment import validate_target_python

        project_python = (Path(target_python).absolute() if target_python is not None
                          else resolve_project_python(project))
        runtime = validate_target_python(project_python)
        reporter.emit("SOURCE", f"controller_python={sys.executable}")
        reporter.emit("SOURCE", f"target_python={project_python} target_prefix={runtime['prefix']}")
    workspace_context = (
        resume_workspace(resume_run) if saved is not None else
        copy_project(project, runs_root=runs_root)
        if project is not None
        else _empty_context()
    )
    with workspace_context as workspace:
        adapted_entrypoint = None
        automatic = None
        if project is not None and entrypoint is None and cases:
            from .auto_adapter import AutoAdapter
            from .interfaces import NeedsConfirmation

            reporter.emit("SOURCE", f"workspace={workspace}")
            reporter.emit("ADAPT", "Discovering project input/output and verifying an adapter")
            automatic = AutoAdapter(
                workspace, python=project_python, env_file=env_file, model=model,
                timeout=timeout, max_tool_calls=max_tool_calls, verbose=verbose,
                resume=saved is not None,
                graphify=graphify, graphify_python=graphify_python,
                service_url=service_url,
            )
            automatic.report["target_environment"] = runtime
            if saved is not None:
                from .interfaces import write_json
                saved['service_url'] = automatic.service_url
                saved['env_file'] = str(Path(env_file).resolve()) if env_file else None
                write_json(workspace.parent / 'run-context.json', saved)
            if saved is None:
                from .run_context import save_context

                save_context(workspace, dataset=dataset, project=project, output=output,
                             python=project_python, env_file=env_file, model=model,
                             timeout=timeout, max_tool_calls=max_tool_calls, intent=intent,
                             graphify=graphify, graphify_python=graphify_python, service_url=service_url)
            probes = list(dict.fromkeys(case["question"] for case in cases))[:2]
            try:
                automatic.prepare(probes, interactive=interactive, candidate_id=candidate_id,
                                  clarification=clarification, intent=intent)
            except NeedsConfirmation:
                raise
            except Exception:
                reporter.emit("WARN", f"adapter preparation failed; evidence={automatic.evidence}")
                raise
            reporter.emit("ADAPT", f"verified adapter evidence={automatic.evidence}")
        elif project is not None and entrypoint is not None and cases:
            adapted_entrypoint = workspace / relative_entrypoint  # type: ignore[operator]
            if not adapted_entrypoint.is_file():
                raise FileNotFoundError(f"entrypoint not found: {entrypoint}")
            reporter.emit("SOURCE", f"workspace={workspace}")
            reporter.emit("CHUNK", f"entrypoint={relative_entrypoint}")
            if "LLADAR_QUESTION" not in adapted_entrypoint.read_text(encoding="utf-8"):
                try:
                    (adapter or _require_adapter()).adapt(workspace, adapted_entrypoint)
                except Exception as error:
                    reporter.emit(
                        "WARN",
                        f"stage=adapt error_type={type(error).__name__}",
                    )
                    raise
        with output_path.open("w", encoding="utf-8", newline="\n") as target:
            total = len(cases)
            for index, case in enumerate(cases, start=1):
                result: dict[str, Any] = {
                    "schema_version": 2,
                    "id": case.get("id"),
                    "group_id": case.get("group_id"),
                    "kind": case.get("kind"),
                    "question": case.get("question"),
                }
                question = case.get("question")
                if not isinstance(question, str) or not question.strip():
                    result.update(
                        status="execution_error",
                        error="Missing non-empty question",
                    )
                else:
                    try:
                        if answer is not None:
                            response = answer(question)
                        elif automatic is not None:
                            response = automatic.answer(question, case["id"])
                        else:
                            response = _run_entrypoint(
                                adapted_entrypoint,
                                workspace,
                                question,
                                python_executable=project_python,
                                env_file=env_file,
                                timeout=timeout,
                            )
                        result.update(
                            status="ok",
                            answer=response if isinstance(response, str) else str(response),
                        )
                        completed += 1
                    except Exception as error:  # Keep later dataset items runnable.
                        result.update(
                            status="execution_error",
                            error=f"{type(error).__name__}: {error}",
                        )
                        reporter.emit(
                            "WARN",
                            f"item={case.get('id')} stage=answer error_type={type(error).__name__}",
                        )
                target.write(json.dumps(result, ensure_ascii=False) + "\n")
                target.flush()
                reporter.session(index, total, result["status"])
    reporter.done(completed, metric="completed_sessions")
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
    timeout: float = 120,
) -> str:
    if entrypoint is None:
        raise ValueError("entrypoint is required for project mode")
    from .target_environment import target_environment

    environment = target_environment(python_executable, env_file)
    environment["LLADAR_QUESTION"] = question
    completed = subprocess.run(
        [str(python_executable), str(entrypoint)],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(detail)
    response = completed.stdout.strip()
    if not response:
        raise RuntimeError("agent produced no stdout answer")
    return response
