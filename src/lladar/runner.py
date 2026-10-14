from __future__ import annotations

from .language import tr, language_stage, current_language

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
from .resource_limits import resource_limit
from .model_profiles import resolve_model_profile
from .records import read_records, write_records
from .method_skill import SkillAgentFactory, resolve_skill
from .question_types import load_probe_contract, load_question_type_contract
from .run_strategy import SkillStrategy


Answerer = Callable[[str], str]
DEFAULT_ADAPTER_MODEL = "gemini:gemini-3.8-flash"
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


def resolve_project_python(
    project: str | Path, target_python: str | Path | None = None
) -> Path:
    if target_python is not None:
        # POSIX venv launchers are symlinks to the base interpreter. Keep the
        # launcher path so Python can discover the adjacent pyvenv.cfg.
        return Path(target_python).absolute()
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

from .work_session import current_session, stage_session, publish_bundle, publication_metadata, file_sha
from .run_strategy import ScheduledCase, StrategyCase


@language_stage
@stage_session("run-agent")
def run_agent(
    dataset: str | Path,
    output: str | Path,
    *,
    answer: Answerer | None = None,
    project: str | Path | None = None,
    env_file: str | Path | None = None,
    resume: str | Path | None = None,
    force: bool = False,
    verbose: bool = True,
    runs_root: str | Path | None = None,
    model: str | None = None,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    target_python: str | Path | None = None,
    timeout: float = 3600,
    max_tool_calls: int = 1000,
    extend_model_calls: int | None = None,
    skill: str | Path | None = None,
    seed: int = 0,
    interactive: bool | None = None,
    graphify: bool = True,
    graphify_python: str | Path | None = None,
    adapt: str | Path | None = None,
    service_url: str | None = None,
    interface_selector: Callable[[dict], str] | None = None,
    strategy_agent_factory: SkillAgentFactory | None = None,
    page_url: str | None = None,
    probe_question: str | None = None,
    browser_session_policy: str | None = None,
    browser_target_factory: Callable[..., Any] | None = None,
    confirm_browser_run: bool = False,
    fresh_browser_profile: bool = False,
    extraction_provider_factory: Callable[..., Any] | None = None,
    allow_response_model_transfer: bool = False,
    lang: str | None = None,
) -> int:
    """Fill actual_response in a new JSONL file without evaluating it."""
    if sum(value is not None for value in (answer, project, page_url)) != 1:
        raise ValueError("provide exactly one of answer, project, or page_url")
    if adapt is not None and project is None and page_url is None:
        raise ValueError("--adapt requires --project or --page-url")
    if page_url is not None and service_url is not None:
        raise ValueError("--service-url applies only to a project target")
    if project is None and interface_selector is not None:
        raise ValueError("interface_selector applies only to a project target")
    if page_url is None and confirm_browser_run:
        raise ValueError("--confirm-browser-run requires --page-url")
    if page_url is None and fresh_browser_profile:
        raise ValueError("--fresh-browser-profile requires --page-url")
    if probe_question is not None:
        if page_url is None:
            raise ValueError("--probe-question requires --page-url")
        if not isinstance(probe_question, str) or not probe_question.strip():
            raise ValueError("--probe-question must be a non-empty question")
    if browser_session_policy is not None and (page_url is None or browser_session_policy not in {'independent','continuous','demonstrated'}):
        raise ValueError('--conversation-policy requires --page-url and independent, continuous or demonstrated')
    if page_url is None and (extraction_provider_factory is not None or allow_response_model_transfer):
        raise ValueError("response extraction options require --page-url")
    from .answer_extraction import DEFAULT_EXTRACTION_MODEL
    extraction_model = model or DEFAULT_EXTRACTION_MODEL
    model = model or DEFAULT_ADAPTER_MODEL
    if project is None and page_url is None and (max_input_tokens is not None or max_output_tokens is not None):
        raise ValueError("max_input_tokens and max_output_tokens apply only to a project target")
    if page_url is not None:
        from .answer_extraction import ExtractionOptions
        browser_limits = ExtractionOptions(model_destination=extraction_model, max_output_tokens=max_output_tokens, max_input_tokens=max_input_tokens)
        browser_limits.output_token_limit
        browser_limits.input_byte_limit
    adapter_profile = resolve_model_profile(
        model, max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens,
    ) if project is not None else None
    page_origin = None
    if page_url is not None:
        from .browser_target import browser_failure_diagnostic, validate_browser_page_url

        browser_page = validate_browser_page_url(page_url)
        page_origin = f"{browser_page.scheme}://{browser_page.netloc}"
    if timeout <= 0 or max_tool_calls <= 0:
        raise ValueError("timeout and max_tool_calls must be positive")
    if extend_model_calls is not None and (page_url is None or resume is None
            or type(extend_model_calls) is not int
            or extend_model_calls <= resource_limit('BROWSER_DISCOVERY_MODEL_CALLS')):
        raise ValueError('--extend-model-calls requires resumed --page-url and a total greater than the original model-call limit')
    selected_skill = resolve_skill(skill, BUILTIN_RUN_SKILL_DIR)

    records = read_records(dataset)
    question_type_contract = load_question_type_contract(Path(dataset), records)
    probe_contract = load_probe_contract(Path(dataset), records)
    if any(record["actual_response"] is not None for record in records):
        raise ValueError("run-agent requires records with actual_response set to null")
    output_path = Path(output)
    run_path = output_path.with_name(output_path.name + ".run.json")
    trials_path = output_path.with_name(output_path.name + ".trials.jsonl")
    session = current_session()
    blocked_artifact = session.data["state"].get("run_blocker_artifact") if session and resume else None
    archive_blocked = False
    for candidate in (output_path, run_path, trials_path):
        if candidate.exists() and not force:
            if (candidate == run_path and blocked_artifact
                    and blocked_artifact.get("path") == str(run_path.resolve())
                    and blocked_artifact.get("sha256") == file_sha(run_path)):
                archive_blocked = True
                continue
            raise FileExistsError(f"output already exists: {candidate}")
    if archive_blocked:
        archive = session.directory / ("blocked-run-" + blocked_artifact["sha256"] + ".json")
        if archive.exists():
            raise FileExistsError(f"output already exists: {archive}")
        run_path.rename(archive)

    strategy = SkillStrategy(records, skill=selected_skill, seed=seed, model=model,
                             env_file=env_file or ".env", runs_root=runs_root,
                             agent_factory=strategy_agent_factory)
    session = current_session()
    saved_schedule = session.data["state"].get("run_schedule") if session else None
    if saved_schedule:
        schedule = [ScheduledCase(StrategyCase(**item["case"]), item["repeats"])
                    for item in saved_schedule["items"]]
        strategy.evidence = saved_schedule["evidence"]
    else:
        schedule = strategy.generate()
        if session:
            from dataclasses import asdict
            session.put("run_schedule", {"items": [asdict(item) for item in schedule], "evidence": strategy.evidence})
    saved_trials = session.data["state"].get("run_trials", {}) if session else {}

    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {"dataset": dataset, "project": project, "page_url": page_origin, "records": len(schedule),
         "trials": sum(item.repeats for item in schedule), "output": output}
    )
    target_python_path = Path(sys.executable)
    if project is not None and schedule:
        target_python_path = resolve_project_python(project, target_python)
        from .target_environment import validate_target_python

        validate_target_python(target_python_path)

    context = (
        copy_project(project, runs_root=runs_root)
        if project is not None and schedule
        else _empty_context()
    )
    completed = 0
    browser_cancelled = False
    browser_blocker = None
    last_schedule_index = 0
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    target_evidence: dict[str, Any] = {
        "project": str(Path(project).resolve()) if project is not None else None,
        "adapter_model": model if project is not None else None,
    }
    responses_spool = session.directory / "responses.pending.jsonl"
    trials_spool = session.directory / "trials.pending.jsonl"
    with context as workspace, responses_spool.open("w", encoding="utf-8", newline="\n") as response_stream, trials_spool.open("w", encoding="utf-8", newline="\n") as trial_stream:
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
                    max_input_tokens=adapter_profile.max_input_tokens,
                    max_output_tokens=adapter_profile.max_output_tokens,
                    timeout=timeout,
                    max_tool_calls=max_tool_calls,
                    verbose=verbose,
                    graphify=graphify,
                    graphify_python=graphify_python,
                    service_url=service_url,
                )
                if adapt is not None:
                    automatic.prepare_existing(adapt, probes)
                else:
                    automatic.prepare(
                        probes,
                        interactive=interactive,
                        interface_selector=interface_selector,
                    )
                if automatic.source is not None:
                    adapter_sha256 = hashlib.sha256(automatic.source).hexdigest()
                    target_evidence.update(adapter_sha256=adapter_sha256,
                                           builder_evidence=str(automatic.evidence),
                                           reused_adapter=automatic.report.get("reused_adapter"))
            elif page_url is not None and schedule:
                from functools import partial
                from .answer_extraction import ExtractionOptions
                from .extraction_provider import AkashaExtractionProvider

                extraction_options = ExtractionOptions(
                    model_destination=extraction_model,
                    max_output_tokens=max_output_tokens,
                    max_input_tokens=max_input_tokens,
                    provider_factory=extraction_provider_factory or partial(AkashaExtractionProvider, env_file=env_file or ".env"),
                    transfer_approved=allow_response_model_transfer,
                )
                if browser_target_factory is None:
                    from .independent_target import IndependentTarget

                    browser_target_factory = partial(IndependentTarget,
                        env_file=env_file or '.env',
                        workspace=session.directory/'browser-discovery', adapter_path=adapt,
                        max_tool_calls=max_tool_calls,conversation_policy=browser_session_policy or 'independent')
                browser_target_kwargs = dict(
                    page_url=page_url,
                    timeout=timeout,
                    runs_root=runs_root,
                    verbose=verbose,
                    confirmed=confirm_browser_run,
                    fresh_profile=fresh_browser_profile,
                    probe_question=probe_question,
                    extraction_options=extraction_options,
                )
                if extend_model_calls is not None:
                    browser_target_kwargs['model_call_limit'] = extend_model_calls
                browser_target = browser_target_factory(**browser_target_kwargs)
                try:
                    browser_target.prepare(
                        [],
                        interactive=interactive,
                        record_count=len(schedule),
                        request_count=sum(item.repeats for item in schedule),
                    )
                except (Exception, KeyboardInterrupt) as error:
                    diagnostic = browser_failure_diagnostic(error, browser_target.evidence)
                    reporter.emit("WARN", tr('phase=browser_prepare ') + " ".join(
                        f"{key}={str(value).lower() if isinstance(value, bool) else value}"
                        for key, value in diagnostic.items()
                    ))
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
                            "error_type": diagnostic["error_type"],
                            "diagnostic": diagnostic,
                        },
                        "target": target_evidence,
                        "skill": strategy.evidence,
                    }
                    run_path.parent.mkdir(parents=True, exist_ok=True)
                    run_path.write_text(
                        json.dumps(blocked_run, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    session.put("run_blocker_artifact", {
                        "path": str(run_path.resolve()), "sha256": file_sha(run_path),
                    })
                    raise
                target_evidence = dict(browser_target.evidence)

            trials: list[dict[str, Any]] = []
            for schedule_index, item in enumerate(schedule, 1):
                last_schedule_index = schedule_index
                record = records[item.case.record_index - 1]
                result = dict(record)
                result["actual_response"] = None
                for trial in range(1, item.repeats + 1):
                    trial_result: dict[str, Any] = {
                        "record_index": schedule_index,
                        "source_record_index": item.case.record_index,
                        "trial": trial,
                        "question": record["question"],
                        "expected_answer": record["expected_answer"],
                        "status": "execution_error",
                        "adapter_sha256": adapter_sha256,
                    }
                    trial_id = f"trial_{item.case.record_index}_{trial}"
                    trial_result["trial_id"] = trial_id
                    previous = session.get_item("run_trials", trial_id)
                    if previous:
                        if previous.get("completion_state") == "completion_unknown":
                            previous = {**previous, "status": "execution_error", "error": "completion_unknown: request was sent; no automatic retry"}
                            errors.append({"line": item.case.record_index, "trial": trial, "error": previous["error"]})
                        elif previous["status"] == "execution_error":
                            errors.append({"line": item.case.record_index, "trial": trial, "error": previous.get("error", "execution_error")})
                        if previous["status"] == "ok" and result["actual_response"] is None:
                            result["actual_response"] = previous["actual_response"]
                        reporter.session(len(trials) + 1, sum(item.repeats for item in schedule), tr('Resumed trials'))
                        trials.append({key: value for key, value in previous.items() if key != "actual_response"})
                        json.dump(previous, trial_stream, ensure_ascii=False)
                        trial_stream.write("\n")
                        continue
                    started = time.perf_counter()
                    reporter.record(record, context=f"line={item.case.record_index} trial={trial}")
                    if session:
                        pending = {**trial_result, "completion_state": "completion_unknown"}
                        session.put_item("run_trials", trial_id, pending)
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
                        reporter.answer("RESPONSE", response_text)
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
                        error_record = {"line": item.case.record_index, "trial": trial, "error": message}
                        detail = f"line={item.case.record_index} trial={trial} error_type={type(error).__name__}"
                        if browser_target is not None:
                            diagnostic = browser_failure_diagnostic(error, browser_target.evidence)
                            message = (f"{diagnostic['error_type']}: browser request failed "
                                       f"({diagnostic['reason']}); private diagnostics withheld")
                            trial_result["error"] = message
                            error_record["error"] = message
                            trial_result["diagnostic"] = diagnostic
                            error_record["diagnostic"] = diagnostic
                            detail = f"line={item.case.record_index} trial={trial} " + " ".join(
                                f"{key}={str(value).lower() if isinstance(value, bool) else value}"
                                for key, value in diagnostic.items()
                            )
                            if not isinstance(error,KeyboardInterrupt):
                                browser_blocker={"stage":"dataset_request","line":item.case.record_index,
                                    "trial":trial,"diagnostic":diagnostic}
                        errors.append(error_record)
                        reporter.emit(
                            "WARN", detail
                        )
                    trial_result["duration_seconds"] = time.perf_counter() - started
                    if session:
                        session.put_item("run_trials", trial_id, trial_result)
                    reporter.session(len(trials) + 1, sum(item.repeats for item in schedule), tr('Completed trials'))
                    trials.append({key: value for key, value in trial_result.items() if key != "actual_response"})
                    json.dump(trial_result, trial_stream, ensure_ascii=False)
                    trial_stream.write("\n")
                    if browser_cancelled or browser_blocker is not None:
                        break
                status = "ok" if result["actual_response"] is not None else "execution_error"
                if status == "ok":
                    completed += 1
                json.dump(result, response_stream, ensure_ascii=False)
                response_stream.write("\n")
                reporter.emit("SESSION", tr('record={p0} status={p1}', p0=item.case.record_index, p1=status))
                if browser_cancelled or browser_blocker is not None:
                    break
            if browser_blocker is not None:
                reporter.emit('WARN',tr('The adapter could not be repaired and requalified; stopping the remaining browser trials. reason={reason}',
                    reason=browser_blocker['diagnostic']['reason']))
                for remaining in schedule[last_schedule_index:]:
                    skipped=dict(records[remaining.case.record_index-1])
                    skipped['actual_response']=None
                    json.dump(skipped,response_stream,ensure_ascii=False)
                    response_stream.write('\n')
        finally:
            if browser_target is not None:
                target_evidence = dict(browser_target.evidence)
                browser_target.close()


    run_record = {
        "run_id": datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f"),
        "dataset": str(Path(dataset).resolve()),
        "responses": str(output_path.resolve()),
        "responses_sha256": file_sha(responses_spool),
        "total": len(schedule),
        "trial_schema_version": 2,
        "trials": len(trials),
        "completed": completed,
        "failed": len(errors),
        "errors": errors,
        "target": target_evidence,
    }
    run_record["skill"] = strategy.evidence
    if question_type_contract is not None:
        run_record["question_type_contract"] = question_type_contract
    if probe_contract is not None:
        run_record["probe_contract"] = probe_contract
    if browser_cancelled:
        run_record["status"] = "cancelled"
    elif browser_blocker is not None:
        run_record["status"] = "blocked"
        run_record["blocker"] = browser_blocker
    else:
        run_record["status"] = "ok" if not errors else "completed_with_errors"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_record["session"] = publication_metadata()
    publish_bundle({output_path: responses_spool, trials_path: trials_spool,
                    run_path: json.dumps(run_record, ensure_ascii=False, indent=2) + "\n"},
                   force=force, result=completed)
    reporter.done(completed, metric="completed")
    if browser_cancelled:
        raise KeyboardInterrupt()
    return completed


@contextmanager
def _empty_context():
    yield None
