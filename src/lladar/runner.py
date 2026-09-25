from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .progress import ProgressReporter
from .records import read_records, write_records
from .method_skill import SkillAgentFactory, resolve_skill
from .run_strategy import SkillStrategy


Answerer = Callable[[str], str]
BUILTIN_RUN_SKILL_DIR = Path(__file__).resolve().parent / "skill_assets" / "run-agent-stability"


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
    model: str | None = None,
    target_python: str | Path | None = None,
    timeout: float = 3600,
    max_tool_calls: int = 100,
    skill: str | Path | None = None,
    seed: int = 0,
    interactive: bool | None = None,
    graphify: bool = True,
    graphify_python: str | Path | None = None,
    service_url: str | None = None,
    strategy_agent_factory: SkillAgentFactory | None = None,
    page_url: str | None = None,
    browser_target_factory: Callable[..., Any] | None = None,
    confirm_browser_run: bool = False,
    fresh_browser_profile: bool = False,
    extraction_provider_factory: Callable[..., Any] | None = None,
    allow_response_model_transfer: bool = False,
) -> int:
    """Fill actual_response in a new JSONL file without evaluating it."""
    if sum(value is not None for value in (answer, project, page_url)) != 1:
        raise ValueError("provide exactly one of answer, project, or page_url")
    if page_url is not None and service_url is not None:
        raise ValueError("--service-url applies only to a project target")
    if page_url is None and confirm_browser_run:
        raise ValueError("--confirm-browser-run requires --page-url")
    if page_url is None and fresh_browser_profile:
        raise ValueError("--fresh-browser-profile requires --page-url")
    if page_url is None and (extraction_provider_factory is not None or allow_response_model_transfer):
        raise ValueError("response extraction options require --page-url")
    from .answer_extraction import DEFAULT_EXTRACTION_MODEL
    extraction_model = model or DEFAULT_EXTRACTION_MODEL
    model = model or "gemini:gemini-2.5-flash"
    page_origin = None
    if page_url is not None:
        from .browser_target import validate_browser_page_url

        browser_page = validate_browser_page_url(page_url)
        page_origin = f"{browser_page.scheme}://{browser_page.netloc}"
    if timeout <= 0 or max_tool_calls <= 0:
        raise ValueError("timeout and max_tool_calls must be positive")
    selected_skill = resolve_skill(skill, BUILTIN_RUN_SKILL_DIR)

    records = read_records(dataset)
    if any(record["actual_response"] is not None for record in records):
        raise ValueError("run-agent requires records with actual_response set to null")
    output_path = Path(output)
    run_path = output_path.with_name(output_path.name + ".run.json")
    trials_path = output_path.with_name(output_path.name + ".trials.jsonl")
    for candidate in (output_path, run_path, trials_path):
        if candidate.exists() and not force:
            raise FileExistsError(f"output already exists: {candidate}")

    strategy = SkillStrategy(records, skill=selected_skill, seed=seed, model=model,
                             env_file=env_file or ".env", runs_root=runs_root,
                             agent_factory=strategy_agent_factory)
    schedule = strategy.generate()

    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {"dataset": dataset, "project": project, "page_url": page_origin, "records": len(schedule),
         "trials": sum(item.repeats for item in schedule), "output": output}
    )
    target_python_path = Path(sys.executable)
    if project is not None and schedule:
        target_python_path = (
            Path(target_python).resolve() if target_python else resolve_project_python(project)
        )
        from .target_environment import validate_target_python

        validate_target_python(target_python_path)

    context = (
        copy_project(project, runs_root=runs_root)
        if project is not None and schedule
        else _empty_context()
    )
    completed = 0
    browser_cancelled = False
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    target_evidence: dict[str, Any] = {
        "project": str(Path(project).resolve()) if project is not None else None,
        "adapter_model": model if project is not None else None,
    }
    with context as workspace:
        automatic = None
        browser_target = None
        adapter_sha256 = None
        try:
            probes = list(dict.fromkeys(item.case.question for item in schedule))[:2]
            if project is not None and schedule:
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
                automatic.prepare(probes, interactive=interactive)
                if automatic.source is not None:
                    adapter_sha256 = hashlib.sha256(automatic.source).hexdigest()
            elif page_url is not None and schedule:
                from functools import partial
                from .answer_extraction import ExtractionOptions
                from .extraction_provider import GeminiExtractionProvider

                extraction_options = ExtractionOptions(
                    model_destination=extraction_model,
                    provider_factory=extraction_provider_factory or partial(GeminiExtractionProvider, env_file=env_file or ".env"),
                    transfer_approved=allow_response_model_transfer,
                )
                if browser_target_factory is None:
                    from .browser_target import BrowserTarget

                    browser_target_factory = BrowserTarget
                browser_target = browser_target_factory(
                    page_url=page_url,
                    timeout=timeout,
                    runs_root=runs_root,
                    verbose=verbose,
                    confirmed=confirm_browser_run,
                    fresh_profile=fresh_browser_profile,
                    extraction_options=extraction_options,
                )
                try:
                    browser_target.prepare(
                        probes,
                        interactive=interactive,
                        record_count=len(schedule),
                        request_count=sum(item.repeats for item in schedule),
                    )
                except (Exception, KeyboardInterrupt) as error:
                    target_evidence = dict(browser_target.evidence)
                    blocked_run = {
                        "run_id": datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f"),
                        "status": "blocked",
                        "dataset": str(Path(dataset).resolve()),
                        "responses": str(output_path.resolve()),
                        "total": len(schedule),
                        "trials": 0,
                        "completed": 0,
                        "failed": 0,
                        "errors": [],
                        "blocker": {
                            "stage": "browser_prepare",
                            "error_type": type(error).__name__,
                        },
                        "target": target_evidence,
                        "skill": strategy.evidence,
                    }
                    run_path.parent.mkdir(parents=True, exist_ok=True)
                    run_path.write_text(
                        json.dumps(blocked_run, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    raise
                target_evidence = dict(browser_target.evidence)

            trials: list[dict[str, Any]] = []
            for schedule_index, item in enumerate(schedule, 1):
                record = records[item.case.record_index - 1]
                result = dict(record)
                result["actual_response"] = None
                for trial in range(1, item.repeats + 1):
                    trial_result: dict[str, Any] = {
                        "record_index": item.case.record_index,
                        "trial": trial,
                        "question": record["question"],
                        "expected_answer": record["expected_answer"],
                        "status": "execution_error",
                        "adapter_sha256": adapter_sha256,
                    }
                    started = time.perf_counter()
                    try:
                        if browser_cancelled:
                            raise RuntimeError("Browser run cancelled")
                        if answer is not None:
                            response = answer(record["question"])
                        elif automatic is not None:
                            response = automatic.answer(
                                record["question"], f"record-{item.case.record_index}-trial-{trial}"
                            )
                        elif browser_target is not None:
                            response = browser_target.answer(
                                record["question"], f"record-{item.case.record_index}-trial-{trial}"
                            )
                        else:
                            raise RuntimeError("target adapter was not prepared")
                        response_text = response if isinstance(response, str) else str(response)
                        trial_result.update(status="ok", actual_response=response_text)
                        if result["actual_response"] is None:
                            result["actual_response"] = response_text
                    except (Exception, KeyboardInterrupt) as error:
                        if isinstance(error, KeyboardInterrupt):
                            if browser_target is None:
                                raise
                            browser_cancelled = True
                        message = (
                            f"{type(error).__name__}: browser request failed; private diagnostics withheld"
                            if browser_target is not None
                            else f"{type(error).__name__}: {error}"
                        )
                        trial_result.update(error=message)
                        errors.append({"line": item.case.record_index, "trial": trial, "error": message})
                        reporter.emit(
                            "WARN", f"line={item.case.record_index} trial={trial} error_type={type(error).__name__}"
                        )
                    trial_result["duration_seconds"] = time.perf_counter() - started
                    trials.append(trial_result)
                status = "ok" if result["actual_response"] is not None else "execution_error"
                if status == "ok":
                    completed += 1
                results.append(result)
                reporter.session(schedule_index, len(schedule), status)
        finally:
            if browser_target is not None:
                target_evidence = dict(browser_target.evidence)
                browser_target.close()

    write_records(results, output_path, overwrite=force)
    from .output import write_dataset
    write_dataset(trials, trials_path, "jsonl", overwrite=force)
    run_record = {
        "run_id": datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f"),
        "dataset": str(Path(dataset).resolve()),
        "responses": str(output_path.resolve()),
        "responses_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "total": len(schedule),
        "trials": len(trials),
        "completed": completed,
        "failed": len(errors),
        "errors": errors,
        "target": target_evidence,
    }
    run_record["skill"] = strategy.evidence
    if browser_cancelled:
        run_record["status"] = "cancelled"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        json.dumps(run_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reporter.done(completed, metric="completed")
    if browser_cancelled:
        raise KeyboardInterrupt()
    return completed


@contextmanager
def _empty_context():
    yield None
