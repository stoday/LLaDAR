"""Best-effort, private-safe reports for CLI invocations (never Python API calls)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

from .language import Language, language_scope, tr
from .resource_limits import resource_limit

_CURRENT = ContextVar("lladar_operation", default=None)
_STAGES = {"test-dataset", "situation", "run-agent", "eval", "report"}
_INPUTS = ("knowledge", "dataset", "responses", "eval_output", "situation_config",
           "instructions_file", "observe_file", "stop_criteria_file", "skill", "resume")
_SETTINGS = ("count", "seed", "question_type", "max_turns", "num_scenarios",
             "max_input_tokens", "max_output_tokens", "max_tool_calls", "timeout",
             "temperature", "auto_window_ratio", "force", "strict", "verbose")
_SIDECARS = {
    "test-dataset": (".generation.json", ".graph.json"),
    "run-agent": (".run.json", ".trials.jsonl", ".turns.jsonl", ".scenarios.jsonl", ".calibration.json"),
    "report": (".report.json",),
}


def current_operation():
    return _CURRENT.get()


def exception_exit_code(error):
    if isinstance(error, SystemExit):
        return error.code if isinstance(error.code, int) else 0 if error.code is None else 1
    return 130 if isinstance(error, KeyboardInterrupt) else 2


@contextmanager
def operation_scope(operation):
    token = _CURRENT.set(operation)
    try:
        yield operation
    finally:
        _CURRENT.reset(token)


def _stamp(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
        return None


def _safe(value):
    # Prevent file names from injecting Markdown sections or links.
    value = str(value)
    if re.search(r"[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return "[URL withheld]"
    return value.replace("\r", " ").replace("\n", " ").replace("`", "'").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


class OperationReport:
    def __init__(self):
        self.cwd = Path.cwd()
        self.started = datetime.now(timezone.utc)
        self.clock = time.monotonic()
        self.stage = None
        self.language = Language()
        self.args = None
        self.output = None
        self.before = {}
        self.statistics = {}
        self.problem = None
        self.hint = None
        self.done = False
        self.issues = False

    def identify(self, prog):
        parts = prog.split()
        stage = parts[-1]
        if stage in _STAGES and (stage not in {"test-dataset", "situation"} or "create" in parts):
            self.stage = stage
        elif stage.endswith("-skill") and stage.removesuffix("-skill") in _STAGES:
            self.stage = stage

    def configure(self, args, resolver):
        self.args = args
        self.stage = args.create_command if args.command == "create" else args.command
        self.language = Language(args.lang)
        destination = getattr(args, "output", None)
        if self.stage.endswith("-skill"):
            self.set_output(destination or Path("lladar-skills") / self.stage.removesuffix("-skill"))
        elif self.stage != "test-dataset":
            filename = {"situation": "situation.json", "run-agent": "responses.jsonl",
                        "eval": "evaluation.json", "report": "report.md"}[self.stage]
            self.set_output(resolver(destination, filename))

    def set_output(self, output):
        path = Path(output).resolve()
        if path == self.output:
            return
        self.output = path
        self.before = {item: _stamp(item) for item in self.artifacts()}

    def artifacts(self):
        if self.output is None:
            return []
        if self.stage.endswith("-skill"):
            return [self.output / name for name in ("SKILL.md", "AUTHORING.md", "language.json")]
        paths = [self.output, *(Path(str(self.output) + suffix) for suffix in _SIDECARS.get(self.stage, ()))]
        if self.stage == "test-dataset" and self.args and getattr(self.args, "from_github", None):
            from .github_dataset import artifact_paths
            paths.extend(artifact_paths(self.output))
        return paths

    def record(self, *, count=None, summary=None, status=None):
        if isinstance(count, int):
            self.statistics["records"] = count
        # Only known numeric statistics; never reasons, prompts, answers or URLs.
        for key in ("evaluated", "valid_determinate", "judge_error", "execution_error",
                    "invalid", "indeterminate", "failed", "skipped", "completed",
                    "failed_sources", "failed_points", "scheduled_trials", "coverage"):
            value = (summary or {}).get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.statistics[key] = value
                if key in {"judge_error", "execution_error", "invalid", "indeterminate", "failed", "skipped", "failed_sources", "failed_points"} and value:
                    self.issues = True
        if status in {"partial", "cancelled", "blocked"}:
            self.issues = True

    def error(self, error=None, category=None):
        if category is None:
            from .exceptions import ProviderError
            from .interfaces import NeedsConfirmation
            category = ("cancelled" if isinstance(error, KeyboardInterrupt) else
                        "confirmation" if isinstance(error, NeedsConfirmation) else
                        "provider" if isinstance(error, ProviderError) else
                        "io" if isinstance(error, OSError) else
                        "arguments" if isinstance(error, (ValueError, SystemExit)) else "runtime")
        self.problem = category
        hint = getattr(error, "work_session_hint", None)
        if isinstance(hint, dict) and hint.get("path"):
            self.hint = {"path": str(hint["path"]), "persistence_failed": bool(hint.get("persistence_failed"))}

    def _read_run_counts(self, code):
        if self.stage != "run-agent" or self.output is None:
            return
        path = Path(str(self.output) + ".run.json")
        # Ignore old metadata on failed invocations and bound this optional read.
        try:
            if (code != 0 and _stamp(path) == self.before.get(path)) or not path.is_file() or path.stat().st_size > resource_limit('REPORT_METADATA_BYTES'):
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self.record(summary=data, status=data.get("status"))
            if isinstance(data.get("errors"), list) and "failed" not in data:
                self.record(summary={"failed": len(data["errors"])})
        except (OSError, ValueError, UnicodeError):
            pass

    def render(self, code):
        self._read_run_counts(code)
        status = ("Cancelled" if code == 130 else "Needs confirmation" if code == 3 else
                  "Failed" if code != 0 else "Completed with issues" if self.issues else "Completed")
        lines = ["# " + tr("Operation result report"), "",
                 f"- {tr('Stage')}: `{self.stage}`",
                 f"- {tr('Status')}: {tr(status)}",
                 f"- {tr('Exit code')}: {code}",
                 f"- {tr('Started (UTC)')}: {self.started.isoformat()}",
                 f"- {tr('Finished (UTC)')}: {datetime.now(timezone.utc).isoformat()}",
                 f"- {tr('Duration (seconds)')}: {time.monotonic() - self.clock:.3f}",
                 f"- {tr('Language')}: `{self.language.lang}`; ui=`{self.language.ui_lang}`", "",
                 "## " + tr("Inputs and settings"), ""]
        if self.args:
            for key in _INPUTS:
                value = getattr(self.args, key, None)
                if value:
                    for item in value if isinstance(value, list) else [value]:
                        lines.append(f"- `{key}`: `{_safe(item)}`")
            for key in _SETTINGS:
                value = getattr(self.args, key, None)
                if isinstance(value, (int, float, bool)) or (key == "question_type" and value in {"contrastive", "positive", "negative"}):
                    lines.append(f"- `{key}`: `{_safe(value)}`")
        lines += ["", "## " + tr("Statistics"), ""]
        lines += [f"- `{key}`: {value}" for key, value in self.statistics.items()] or [tr("No operation statistics available.")]
        lines += ["", "## " + tr("Artifacts"), ""]
        for path in self.artifacts():
            now = _stamp(path)
            before = self.before.get(path)
            state = "Missing" if now is None else "New" if before is None else "Modified" if now != before else "Existing"
            lines.append(f"- `{_safe(path)}` — {tr(state)}")
        if not self.output:
            lines.append(tr("Output location was not resolved."))
        lines += ["", tr("Existing files are not evidence of output produced by this invocation."), "",
                  "## " + tr("Problems and next steps"), ""]
        explanations = {
            "arguments": "Check arguments, input files and resume settings; then run the command again.",
            "io": "Check file permissions and existing output or log files; then run the command again.",
            "provider": "Check provider configuration and availability. Remote error details are omitted.",
            "cancelled": "The operation was cancelled. Inspect artifacts before continuing.",
            "confirmation": "Human confirmation is required. Follow the terminal instructions.",
            "runtime": "The operation encountered a runtime error. Inspect terminal diagnostics before retrying.",
        }
        if self.problem:
            lines += [f"- `{self.problem}`: {tr(explanations[self.problem])}"]
        elif self.issues:
            lines += [tr("Inspect execution errors and incomplete results in the stage artifacts.")]
        else:
            lines += [tr("No operation problems were recorded.")]
        if self.hint:
            lines += [f"- {tr('Work session')}: `{_safe(self.hint['path'])}`"]
            lines += [tr("Checkpoint persistence failed; inspect the session before attempting resume.") if self.hint["persistence_failed"] else
                      tr("Continue with --resume SESSION_PATH and the same inputs/settings.")]
        if code == 0 and self.output:
            next_stage = {"test-dataset": "run-agent", "situation": "run-agent", "run-agent": "eval", "eval": "report"}.get(self.stage)
            lines.append(tr("Next stage: {stage}", stage=next_stage) if next_stage else tr("Open the output files to review the result."))
        lines += ["", tr("This report omits raw prompts, answers, URLs, credentials and arbitrary exception messages."), ""]
        return "\n".join(lines)

    def finish(self, code):
        if self.done or self.stage is None:
            return
        self.done = True
        with language_scope(self.language):
            try:
                content = self.render(code)
                preferred = self.output.parent / (self.output.name + ".operation.md") if self.output else None
                fallback = self.cwd / ".lladar" / "operation-reports" / (self.stage + ".operation.md")
                for base in dict.fromkeys(path for path in (preferred, fallback) if path is not None):
                    try:
                        base.parent.mkdir(parents=True, exist_ok=True)
                        index = 1
                        while True:
                            candidate = base if index == 1 else base.with_name(base.name.removesuffix(".md") + f"-{index}.md")
                            try:
                                with candidate.open("x", encoding="utf-8", newline="\n") as stream:
                                    try:
                                        stream.write(content)
                                        stream.flush()
                                    except OSError:
                                        # This path was exclusively created by us; keep no partial report.
                                        stream.close()
                                        try:
                                            candidate.unlink()
                                        except OSError:
                                            pass
                                        raise
                                try:
                                    print(tr("Operation report: {path}", path=candidate), file=sys.stdout if code == 0 else sys.stderr)
                                except (OSError, UnicodeError):
                                    pass
                                return
                            except FileExistsError:
                                index += 1
                    except OSError:
                        continue
            except Exception:
                # Reporting must never replace the original result or exception.
                pass
            try:
                print(tr("Warning: could not write the operation result report."), file=sys.stderr)
            except (OSError, UnicodeError):
                pass
