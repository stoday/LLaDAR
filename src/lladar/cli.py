from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
from typing import Callable, Any
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata
from pathlib import Path

from .api import DEFAULT_DATASET_MODEL, create_test_dataset
from .answer_extraction import DEFAULT_EXTRACTION_MODEL, MAX_CAPTURE_BYTES, MAX_INPUT_BYTES
from .resource_limits import resource_limit
from .command_log import command_log
from .operation_report import OperationReport, current_operation, exception_exit_code, operation_scope
from .controlled_variants import normalize_controlled_variant_topics, select_controlled_variant_topics
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .interfaces import NeedsConfirmation
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .runner import DEFAULT_ADAPTER_MODEL, run_agent
from .skill_templates import create_skill_template
from .situation import create_situation, evaluate_situation, run_situation
from .language import Language, current_language, language_scope, normalize_lang, resolve_language, localize_error, tr


class _LanguageParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        invocation = list(sys.argv[1:] if args is None else args)
        codes = []
        for index, token in enumerate(invocation):
            if token == "--":
                break
            option, separator, value = token.partition("=")
            # Preserve argparse's existing unambiguous long-option abbreviations.
            if option in {"--la", "--lan", "--lang"} and separator:
                codes.append(value)
            elif option in {"--la", "--lan", "--lang"} and index + 1 < len(invocation):
                codes.append(invocation[index + 1])
        language_error = None
        try:
            normalized = [normalize_lang(code) for code in codes]
            if len(set(normalized)) > 1:
                raise ValueError("conflicting --lang values; use one language per command")
            requested = normalized[0] if normalized else None
        except ValueError as error:
            # Parse the route first so an invalid language still gets a stage report.
            language_error = str(error)
            requested = None
        language = Language(requested or "en")
        if current_operation():
            current_operation().language = language
        with language_scope(language):
            # Help exits during parsing; no session or provider is required.
            if any(token in {"-h", "--help"} for token in invocation):
                if language_error:
                    self.error(language_error)
                with language_scope(language, announce=False):
                    if language.ui_lang != language.lang:
                        print(tr('Language {p0}: interface, fixed headings and offline templates use English.', p0=language.lang), file=sys.stderr)
                    return super().parse_args(invocation, namespace)
            namespace = namespace if namespace is not None else argparse.Namespace()
            if current_operation():
                current_operation().partial_args = namespace
            parsed = super().parse_args(invocation, namespace)
            if current_operation():
                command = getattr(parsed, "command", None)
                stage = getattr(parsed, "create_command", None) if command == "create" else command
                if stage:
                    current_operation().identify("lladar create " + stage if command == "create" else "lladar " + stage)
            if language_error:
                self.error(language_error)
            try:
                language = resolve_language(requested, getattr(parsed, "resume", None))
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
                self.error(str(error))
            parsed.lang = language.lang
            return parsed

    def error(self, message):
        if current_operation():
            current_operation().identify(self.prog)
            if self.prog == "lladar":
                partial = getattr(current_operation(), "partial_args", None)
                command = getattr(partial, "command", None)
                stage = getattr(partial, "create_command", None) if command == "create" else command
                if stage:
                    current_operation().identify("lladar create " + stage if command == "create" else "lladar " + stage)
            current_operation().error(category="arguments")
        self.print_usage(sys.stderr)
        self.exit(2, tr("{prog}: error: {message}\n", prog=self.prog, message=localize_error(message)))


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    def start_section(self, heading):
        return super().start_section(tr(heading))

    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, prefix=tr("usage: ") if prefix is None else prefix)

    def add_text(self, text):
        if text and text != argparse.SUPPRESS:
            translated = tr(text)
            if translated == text and "\n" in text:
                rows = []
                for row in text.splitlines():
                    match = re.fullmatch(r"(  [a-z-]+\s+)(.+)", row)
                    if match:
                        row = match[1] + tr(match[2])
                    elif row.endswith(":"):
                        row = tr(row[:-1]) + ":"
                    else:
                        row = tr(row)
                    rows.append(row)
                translated = "\n".join(rows)
            text = translated
        return super().add_text(text)

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if text.startswith(tr("Additional guidance") + ":"):
            return "\n".join(
                textwrap.fill(line, width, initial_indent=indent, subsequent_indent=indent)
                if line else ""
                for line in text.splitlines()
            )
        return super()._fill_text(text, width, indent)

    def _get_help_string(self, action: argparse.Action) -> str:
        if action.help is not None and (
            action.default is None or "(default:" in action.help
        ):
            return tr(action.help)
        original = super()._get_help_string(action)
        if original != action.help and current_language().ui_lang != "en":
            return tr(action.help or "") + tr(" (default: %(default)s)")
        return tr(original)


class _SingleSkill(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            raise argparse.ArgumentError(self, "only one local skill is supported")
        setattr(namespace, self.dest, values)


_DEFAULT_DATASET_OUTPUT_DISPLAY = "."
_DATASET_FILENAME_DISPLAY = "test-dataset-YYYYMMDD-HHMMSS.jsonl"


def _timestamped_dataset_output(directory: Path) -> Path:
    """Choose a new timestamped dataset file in ``directory``."""
    stem = datetime.now().astimezone().strftime("test-dataset-%Y%m%d-%H%M%S")
    suffix = 1
    output = directory / f"{stem}.jsonl"
    while output.exists():
        output = directory / f"{stem}-{suffix}.jsonl"
        suffix += 1
    return output


def _resolve_dataset_output(destination: str | Path) -> Path:
    """Resolve a CLI destination as a directory or an explicit JSONL file."""
    return _resolve_output(destination, None)


def _resolve_output(destination: str | Path, filename: str | None) -> Path:
    """Use existing directories, trailing separators and suffixless new paths as directories.

    Existing files and new paths with a suffix name the output file directly.
    A dataset directory uses a collision-safe timestamp instead of a fixed filename.
    """
    raw = str(destination)
    # Accept the documented trailing backslash hint on POSIX as well as Windows.
    # Keep native root paths intact while removing the non-native separator.
    path = Path(raw.rstrip("\\") if os.name != "nt" else raw)
    directory = path.is_dir() or raw.endswith(("/", "\\"))
    if not directory and (path.is_file() or path.suffix):
        return path
    return path / filename if filename is not None else _timestamped_dataset_output(path)


def _display_command(arguments: Sequence[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _show_work_summary(stage: str, output: str | Path, *, situation: bool = False,
                       situation_config: str | None = None) -> None:
    """Describe successful workflow artifacts using their actual absolute paths."""
    path = Path(output).resolve()
    descriptions = {
        "test-dataset": tr('Generated questions and source-supported expected answers for testing an Agent.'),
        "situation": tr('Compiled instructions into a reusable multi-turn situation configuration.'),
        "run-agent": tr('Ran the target Agent and saved its responses and execution evidence.'),
        "eval": tr('Evaluated saved responses and recorded judgments and aggregate results.'),
        "report": tr('Rendered the saved evaluation into a readable Markdown report.'),
    }
    main_descriptions = {
        "test-dataset": tr('Test records: questions, expected answers and empty actual_response fields.'),
        "situation": tr('Situation instructions, stopping rules, variation axes, methods and evaluation rubric.'),
        "run-agent": (tr('Multi-turn conversation transcripts for each situation trial.') if situation else
                      tr('Questions, expected answers and actual Agent responses.')),
        "eval": tr('Per-trial judgments, reasons, evaluation settings and summary statistics.'),
        "report": tr('Human-readable findings, result tables and limitations based on saved evaluation evidence.'),
    }
    sidecars = {
        "test-dataset": [
            (".generation.json", tr('Generation methods, source provenance and validation results.')),
            (".graph.json", tr('Knowledge graph and declared control dimensions used for generation.')),
        ],
        "report": [(".report.json", tr('Report section citations and work-session metadata.'))],
        "run-agent": ([
            (".turns.jsonl", tr('Individual conversation turns and their identifiers.')),
            (".scenarios.jsonl", tr('Generated scenarios, starting messages and selected variations.')),
            (".calibration.json", tr('Session adapter calibration evidence.')),
            (".run.json", tr('Run configuration, identity, adapter evidence and execution errors.')),
        ] if situation else [
            (".trials.jsonl", tr('Individual repeated trials, responses and failure diagnostics.')),
            (".run.json", tr('Run configuration, selected cases, target evidence and execution metadata.')),
        ]),
    }
    print(tr('\nWork summary'))
    print(tr('  Work: {p0}', p0=descriptions[stage]))
    print(tr('  Output directory: {p0}', p0=path.parent))
    print(tr('  Files:'))
    print(f"    {path}\n      {main_descriptions[stage]}")
    for suffix, description in sidecars.get(stage, []):
        artifact = Path(str(path) + suffix)
        if artifact.is_file():
            print(f"    {artifact}\n      {description}")
    metadata_path = {"test-dataset": Path(str(path) + ".generation.json"),
                     "run-agent": Path(str(path) + ".run.json"), "eval": path,
                     "report": Path(str(path) + ".report.json")}.get(stage)
    metadata = None
    if metadata_path and metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            session = metadata.get("session")
        except (json.JSONDecodeError, UnicodeDecodeError):
            session = None
        if session:
            print(tr('  Work session: {p0}', p0=session['path']))
            print(tr('    Continue with --resume SESSION_PATH and the same inputs/settings.'))
    if stage == "test-dataset":
        next_step = ["lladar", "run-agent", str(path), "--project", "PROJECT"]
    elif stage == "situation":
        next_step = ["lladar", "run-agent", "--situation-config", str(path),
                     "--project", "PROJECT", "--num-scenarios", "N"]
    elif stage == "run-agent":
        if isinstance(metadata, dict) and metadata.get("status") in {"blocked", "cancelled"}:
            print(tr('  Next: Inspect {p0}, then rerun from the beginning after correcting the blocker.',
                     p0=metadata_path))
            return
        next_step = ["lladar", "eval", str(path)]
        if situation_config:
            next_step += ["--situation-config", str(Path(situation_config).resolve())]
    elif stage == "eval":
        next_step = ["lladar", "report", str(path)]
    else:
        print(tr('  Next: Open {p0} to review the report.', p0=path))
        return
    next_step += ["--lang", current_language().lang]
    print(tr('  Next: {p0}', p0=_display_command(next_step)))
    if stage in {"test-dataset", "situation"}:
        print(tr('    Replace PROJECT with your target project directory.') +
              (tr(' Replace N with a positive scenario count.') if stage == "situation" else ""))


def build_parser() -> argparse.ArgumentParser:
    log_options = argparse.ArgumentParser(add_help=False)
    log_options.add_argument(
        "--log", metavar="PATH", default=argparse.SUPPRESS,
        help="Also save emitted progress, traces, summaries and errors to a new UTF-8 log file (default: off; existing files are kept).",
    )
    log_options.add_argument("--lang", metavar="CODE", default=argparse.SUPPRESS,
        help="Language for generated content and presentation: en, zh-TW, ja (jp), ko, zh-CN; other codes use English interface/templates (default: en).")
    parser = _LanguageParser(
        prog="lladar",
        description="Generate questions, run a target Agent, evaluate responses, and report results.",
        formatter_class=_HelpFormatter,
        parents=[log_options],
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {metadata.version('lladar')}",
        help="Show the installed package version and exit.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND",
        help="Choose a command (required; no default).",
    )

    workflow_commands = {
        "test-dataset": "Generate question and expected-answer records from knowledge.",
        "situation": "Compile a knowledge-optional multi-turn behavior test.",
    }
    skill_commands = {
        f"{stage}-skill": f"Create an editable {stage} skill."
        for stage in ("test-dataset", "situation", "run-agent", "eval", "report")
    }
    create = commands.add_parser(
        "create", help="Create a test dataset, situation, or editable skill template.",
        usage="%(prog)s [-h] [--log PATH] [--lang CODE] ARTIFACT ...",
        description="Create a test dataset, situation configuration, or editable skill template.",
        formatter_class=_HelpFormatter,
        parents=[log_options],
        epilog=tr('Available commands:\n\n') + "\n\n".join(
            f"{title}:\n" + "\n".join(
                f"  {name:<20} {description}"
                for name, description in entries.items()
            )
            for title, entries in (
                (tr('Main workflows'), workflow_commands),
                (tr('Skill templates (advanced)'), skill_commands),
            )
        ),
    )
    create_commands = create.add_subparsers(
        dest="create_command", required=True, metavar="ARTIFACT", prog=create.prog,
        help=argparse.SUPPRESS,
    )
    for stage in ("test-dataset", "situation", "run-agent", "eval", "report"):
        template = create_commands.add_parser(
            f"{stage}-skill", help=skill_commands[f"{stage}-skill"],
            formatter_class=_HelpFormatter,
            parents=[log_options],
        )
        template.add_argument(
            "--output", metavar="DIRECTORY",
            help=f"Destination skill directory (default: ./lladar-skills/{stage}).",
        )
        template.add_argument(
            "--force", action="store_true",
            help="Overwrite SKILL.md, AUTHORING.md and language.json in an existing directory; keep other files (default: off).",
        )
    dataset = create_commands.add_parser(
        "test-dataset",
        help=workflow_commands["test-dataset"],
        formatter_class=_HelpFormatter,
        parents=[log_options],
    )
    dataset_sources = dataset.add_mutually_exclusive_group(required=True)
    dataset_sources.add_argument("--knowledge", action="append", metavar="PATH",
                         help="Source knowledge file or directory; repeat for multiple inputs (required unless --from-github is supplied; no default).")
    dataset_sources.add_argument("--from-github", metavar="URL",
                         help="Public GitHub repository: discover actual datasets and generate QA with an evidence report (required unless --knowledge is supplied; no default).")
    dataset.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                         help="Local method directory containing SKILL.md; selects direct QA or optional graph generation. Only one is supported (default: bundled knowledge-point-qa for --knowledge, github-dataset for --from-github).")
    dataset.add_argument("--output", default=".", metavar="PATH",
                         help=f"Output directory or file path. A directory gets a new {_DATASET_FILENAME_DISPLAY} file; new paths without a suffix are directories (default: {_DEFAULT_DATASET_OUTPUT_DISPLAY}).")
    dataset.add_argument("--count", type=int, default=0, metavar="N", help="Maximum deduplicated records; 0 includes all candidates (default: 0).")
    dataset.add_argument("--question-type", choices=("free", "auto", "single-choice", "multiple-choice", "ranking"),
                         default="free", help="Preferred generated question type; free uses source-grounded open answers (default: free).")
    dataset.add_argument("--controlled-variant-probes", action="store_true",
                         help="Interactively choose source-validated controlled-variant dimensions; requires a terminal (default: off).")
    dataset.add_argument("--controlled-variant-topics", metavar="DIMENSION_ID[,DIMENSION_ID]",
                         help="Comma-separated planned dimension IDs for scripts; cannot combine with --controlled-variant-probes (default: none).")
    dataset.add_argument("--seed", type=int, default=0,
                         help="Deterministic candidate shuffle seed (default: 0).")
    dataset.add_argument("--model", default=DEFAULT_DATASET_MODEL, metavar="MODEL",
                         help=f"Model used for dataset generation (default: {DEFAULT_DATASET_MODEL}).")
    dataset.add_argument("--env-file", default=".env", metavar="PATH",
                         help="Environment file supplied to the model provider (default: .env).")
    dataset.add_argument("--temperature", type=float, default=0.0,
                         help="Model sampling temperature (default: 0.0).")
    dataset.add_argument("--max-input-tokens", type=int, metavar="N",
                         help="Override the selected model profile input budget (default: selected model profile).")
    dataset.add_argument("--max-output-tokens", type=int, metavar="N",
                         help="Override the selected model profile output budget (default: selected model profile).")
    dataset.add_argument("--auto-window-ratio", type=float, metavar="RATIO",
                         help="Override the selected model profile automatic-window ratio (default: selected model profile).")
    dataset.add_argument("--force", action="store_true",
                         help="Replace existing output files (default: off; existing files cause an error).")
    dataset.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True,
                         help="Show LLaDAR progress, colored questions/expected answers and the dataset-generation Akasha trace; --no-verbose hides them (default: on).")

    situation = create_commands.add_parser(
        "situation", help=workflow_commands["situation"],
        formatter_class=_HelpFormatter,
        parents=[log_options],
    )
    instructions = situation.add_mutually_exclusive_group(required=True)
    instructions.add_argument("--instructions", metavar="TEXT",
                              help="Overall instructions for generation, execution and default evaluation; choose one text/file input (required; no default).")
    instructions.add_argument("--instructions-file", metavar="PATH",
                              help="UTF-8 file containing the overall situation instructions (required unless another instruction input is given; no default).")
    instructions.add_argument("--observe", metavar="TEXT",
                              help="Legacy alias for --instructions; mutually exclusive with other instruction inputs (default: none).")
    instructions.add_argument("--observe-file", metavar="PATH",
                              help="Legacy alias for --instructions-file (default: none).")
    stopping = situation.add_mutually_exclusive_group(required=True)
    stopping.add_argument("--stop-criteria", metavar="TEXT",
                          help="When the conversation should stop, as inline text (required unless --stop-criteria-file is given; no default).")
    stopping.add_argument("--stop-criteria-file", metavar="PATH",
                          help="UTF-8 file containing the stopping criteria (required unless --stop-criteria is given; no default).")
    situation.add_argument("--max-turns", type=int, required=True, metavar="N",
                           help="Maximum target Agent turns per scenario; must be positive (required; no default).")
    situation.add_argument("--knowledge", action="append", default=[], metavar="PATH",
                           help="Optional .md or .txt knowledge file; repeat for multiple files (default: no knowledge files).")
    situation.add_argument("--output", default="situation.json", metavar="PATH",
                           help="Output directory or situation JSON file path; a directory gets situation.json (default: situation.json; existing file requires --force).")
    situation.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                           help="Local situation-authoring skill directory containing SKILL.md (default: bundled create-situation).")
    situation.add_argument("--model", default=DEFAULT_DATASET_MODEL, metavar="MODEL",
                           help=f"Model used to compile the situation (default: {DEFAULT_DATASET_MODEL}).")
    situation.add_argument("--env-file", default=".env", metavar="PATH",
                           help="Environment file for the model provider (default: .env).")
    situation.add_argument("--max-input-tokens", type=int, metavar="N",
                           help="Override the situation-authoring model input budget; must be positive (default: selected model profile).")
    situation.add_argument("--max-output-tokens", type=int, metavar="N",
                           help="Override the situation-authoring model output budget; must be positive (default: selected model profile).")
    situation.add_argument("--force", action="store_true",
                           help="Replace an existing situation JSON (default: off).")

    runner = commands.add_parser(
        "run-agent",
        parents=[log_options],
        help="Fill actual_response by running a target Agent.",
        description=(
            "Run one-question DATASET records through an inspected project or an Agent-discovered website, "
            "or use --situation-config for generated multi-turn conversations with a project session driver."
        ),
        epilog=(
            "Additional guidance:\n\n"
            "Browser setup:\n"
            "Install Chromium: python -m playwright install chromium. --page-url always uses the guided terminal workflow; "
            "omit --interactive and --no-interactive (project mode only). "
            "Browser mode requires terminal stdin and stderr; without them it stops before opening the browser.\n\n"
            "One demonstration, then adapter execution:\n"
            "With --page-url, sign in, handle website verification, open a new conversation, "
            "submit the complete demonstration question shown in this terminal, wait for the complete answer, "
            "then return here and press Enter. The program will save the recording and session state, then close the browser. "
            "Wait while a material Agent selects recorded evidence, then an adapter Agent generates send/receive code; the host immediately tests one new question. "
            "The adapter may use recorded HTTP/WebSocket operations or reopen the saved-profile browser for one question when page controls and the rendered answer are the useful evidence. "
            "On failure they repeat with cumulative material and the real test error. On success code generation stops. "
            "The default independent mode asks two fresh verification questions; explicit demonstrated mode continues the shown conversation and uses model reasonableness review. "
            "Formal questions are not supplied to discovery. "
            "Reusing an HTTP/WebSocket --adapter does not open a browser; a browser UI adapter opens it once per question and closes it after collecting the answer.\n\n"
            "Review and approval:\n"
            "Review the website requests, model destination, real response content transfer and budgets together. "
            "One YES covers recording, scoped session-state export, model analysis and budgeted execution. "
            "In independent mode, without an independent full-answer reference, the program temporarily opens a browser for same-turn review: "
            "type MATCH only after comparing the full answer, MISMATCH if different, or WAIT while it is unfinished. "
            "The review browser closes afterwards; it does not send questions for the adapter. "
            "Authentication expiry may require temporary sign-in. Submitted or uncertain questions are never blindly resent.\n\n"
            "Execution limits:\n"
            "Exploration, repairs and extraction share the disclosed model-call ledger; probes, actions and requests are counted separately. "
            "Configure workload budgets with LLADAR_BROWSER_DISCOVERY_* and material/VM budgets with LLADAR_MAX_* and LLADAR_PROTOCOL_*. "
            "Output tokens per call follow the selected model profile; --max-output-tokens overrides this limit. "
            "The persisted shared deadline includes discovery, verification, review and formal execution (LLADAR_EXTRACTION_TIMEOUT_SECONDS). "
            f"Input is capped at {MAX_INPUT_BYTES // 1024} KiB including JSON framing; recordings are searched and read in bounded pages, never silently truncated. "
            f"Each captured response is limited to {MAX_CAPTURE_BYTES // (1024 * 1024)} MiB of decoded bytes; --timeout does not raise this limit. "
            f"Browser startup allows {resource_limit('BROWSER_STARTUP_TIMEOUT_SECONDS')} seconds. Page navigation allows {resource_limit('BROWSER_NAVIGATION_TIMEOUT_SECONDS')} seconds; manual verification allows {resource_limit('MANUAL_VERIFICATION_TIMEOUT_SECONDS')} seconds. --timeout controls website response waiting, not navigation. "
            "Verification and dataset requests are also bounded by the remaining shared deadline.\n\n"
            "Outputs and local session:\n"
            "Final answers are saved in responses and trials; these output files exclude cookies, tokens and raw streams. "
            "By default, the isolated browser profile retains local sign-in state in .lladar/browser-profiles "
            "under the working directory. --fresh-browser-profile uses a separate signed-out profile. "
            "Private adapter, ledger and discovery evidence are saved in OUTPUT.work/browser-discovery.\n\n"
            "Project mode:\n"
            "--service-url does not discover an arbitrary API; it is for an inspected project."
        ),
        formatter_class=_HelpFormatter,
    )
    runner.add_argument(
        "dataset", nargs="?", metavar="DATASET",
        help="Input test-dataset JSONL with empty actual_response fields; required in single-question mode, omitted with --situation-config.",
    )
    target = runner.add_mutually_exclusive_group()
    target.add_argument(
        "--project", default=None, metavar="PATH",
        help="Target project directory (default: current directory); situation mode calibrates a supplied or generated session adapter.",
    )
    target.add_argument(
        "--page-url", metavar="URL",
        help="Question website: demonstrate one complete question and answer in a visible browser, then press Enter; after saving the recording, a material Agent selects evidence and a code Agent generates an HTTP, WebSocket or browser UI adapter for immediate website testing (default: project mode; unavailable with --situation-config).",
    )
    runner.add_argument('--demo-question', metavar='TEXT',
        help='Question to demonstrate once in the visible browser. Wait for the complete answer, then press Enter in this terminal; the program saves state and closes the browser before generating an adapter (default: ask what assistance the service provides; requires --page-url).')
    runner.add_argument('--adapter', metavar='PATH',
        help='Reuse a website adapter manifest with the same --conversation-policy; verify with a fresh question (browser UI adapters reopen the saved profile; default: generate from one demonstration; requires --page-url).')
    runner.add_argument('--conversation-policy', choices=['independent', 'continuous', 'demonstrated'], default='independent',
        help='Website adapter conversation policy: fresh conversation per trial (independent, default), one new formal conversation (continuous), or explicitly reuse the demonstrated conversation (demonstrated; answers may depend on demonstration and probe history, reviewed by a model for reasonableness).')
    runner.add_argument('--extend-model-calls', type=int, metavar='TOTAL',
        help='On a resumed --page-url run, explicitly raise the total Agent model-call cap while keeping the saved inputs and other limits unchanged; the increase persists on later resumes (default: no new extension).')
    runner.add_argument(
        "--output", default="responses.jsonl", metavar="PATH",
        help="Output directory or responses JSONL file path; a directory gets responses.jsonl (default: responses.jsonl). Single-question mode also writes PATH.trials.jsonl and PATH.run.json; situation mode writes PATH.turns.jsonl, PATH.scenarios.jsonl, PATH.calibration.json and PATH.run.json.",
    )
    runner.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Use a local method directory containing SKILL.md for single-question runs (default: bundled run-agent-stability; unavailable with --situation-config).")
    runner.add_argument("--situation-config", metavar="PATH",
                        help="Use a frozen situation JSON for multi-turn project sessions (default: single-question DATASET mode; cannot combine with DATASET or --skill).")
    runner.add_argument("--adapt", metavar="PATH",
                        help="Reuse a Python project adapter with --project (default: automatic discovery; website mode uses --adapter).")
    runner.add_argument("--num-scenarios", type=int, metavar="N",
                        help="Number of distinct scenarios to generate and run; required with --situation-config, unused otherwise (no default).")
    runner.add_argument("--seed", type=int, default=0,
                        help="Deterministic random selection seed for single-question trials (default: 0).")
    runner.add_argument(
        "--model", default=None, metavar="MODEL",
        help=f"Project coding model (default: {DEFAULT_ADAPTER_MODEL}), or browser exploration, repair and faithful extraction model (default: {DEFAULT_EXTRACTION_MODEL}; all via Akasha).",
    )
    runner.add_argument(
        "--max-input-tokens", type=int, metavar="N",
        help="Override input tokens for project discovery/generation/repair or browser exploration/repair/extraction (default: selected model profile). Browser content also obeys LLADAR_MAX_INPUT_BYTES. Does not change the tested Agent.",
    )
    runner.add_argument(
        "--max-output-tokens", type=int, metavar="N",
        help="Override output tokens for project discovery/generation/repair or browser exploration/repair/extraction (default: selected model profile, positive integer). Does not change the tested Agent.",
    )
    runner.add_argument(
        "--env-file", default=".env", metavar="PATH",
        help="Environment file for Akasha and the target process. Akasha uses the file when it exists; otherwise it reads the environment (default: .env).",
    )
    runner.add_argument(
        "--target-python", metavar="PATH",
        help="Target project's Python executable (default: discover PROJECT/.venv; situation mode falls back to the current Python).",
    )
    runner.add_argument(
        "--timeout", type=float, default=3600, metavar="SECONDS",
        help="Timeout for each adapter, tool subprocess, or browser request (default: 3600 seconds / 60 minutes).",
    )
    runner.add_argument(
        "--max-tool-calls", type=int, default=resource_limit('EXPLORATION_TOOL_CALLS'), metavar="N",
        help="Maximum tool calls for project exploration/repair or the browser discovery ledger (default: shared EXPLORATION_TOOL_CALLS, 1000).",
    )
    runner.add_argument(
        "--graphify", action=argparse.BooleanOptionalAction, default=True,
        help="Build a static code graph before project adapter exploration in DATASET and situation modes; --no-graphify disables it (default: enabled).",
    )
    runner.add_argument(
        "--graphify-python", metavar="PATH",
        help="Python executable containing graphifyy (default: discover an existing uv tool environment automatically).",
    )
    runner.add_argument(
        "--service-url", metavar="URL",
        help="Existing test-service base URL that the generated adapter may call (default: none).",
    )
    runner.set_defaults(confirm_browser_run=False, allow_response_model_transfer=False)
    runner.add_argument(
        "--fresh-browser-profile", action="store_true",
        help="Use a separate signed-out browser profile for this run instead of the reusable site profile (default: off).",
    )
    runner.add_argument(
        "--interactive", action=argparse.BooleanOptionalAction, default=None,
        help="Project mode only: allow or disable project-interface prompts (default: enabled only on a TTY). Not accepted with --page-url, which always uses the guided terminal workflow.",
    )
    runner.add_argument(
        "--force", action="store_true",
        help="Replace an existing responses file and its sidecars (default: disabled).",
    )
    runner.add_argument(
        "--verbose", action=argparse.BooleanOptionalAction, default=True,
        help="Show LLaDAR progress, questions/available responses, coding-agent traces, situation progress, browser diagnostics and the browser Akasha Agent's verbose tool trace; use --no-verbose to hide them (default: enabled).",
    )

    evaluation = commands.add_parser(
        "eval",
        parents=[log_options],
        help="Judge saved single-question responses or multi-turn situation transcripts.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("responses", metavar="RESPONSES",
                            help="Responses JSONL from run-agent (required; no default).")
    evaluation.add_argument("--output", default="evaluation.json", metavar="PATH",
                            help="Output directory or evaluation JSON file path; a directory gets evaluation.json (default: evaluation.json).")
    evaluation_method = evaluation.add_mutually_exclusive_group()
    evaluation_method.add_argument("--criteria", metavar="TEXT",
                                   help="Evaluation requirements for the built-in method; mutually exclusive with --skill (default: saved situation rubric or answer correctness).")
    evaluation_method.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                                   help="Complete local evaluation method containing SKILL.md; mutually exclusive with --criteria (default: built-in method).")
    evaluation.add_argument("--situation-config", metavar="PATH",
                            help="Verify the original situation/run identity and use its rubric unless --criteria or --skill overrides evaluation (default: single-question evaluation).")
    evaluation.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL",
                            help=f"Model used for evaluation judgments (default: {DEFAULT_EVALUATION_MODEL}).")
    evaluation.add_argument("--env-file", default=".env", metavar="PATH",
                            help="Environment file for the evaluation model provider (default: .env).")
    evaluation.add_argument("--max-input-tokens", type=int, metavar="N",
                            help="Override the evaluator input token budget (positive integer; default: selected model profile).")
    evaluation.add_argument("--max-output-tokens", type=int, metavar="N",
                            help="Override the evaluator output token budget (positive integer; default: selected model profile).")
    evaluation.add_argument("--strict", action="store_true",
                            help="Stop on a judgment error instead of recording it and continuing (default: off).")
    evaluation.add_argument("--force", action="store_true",
                            help="Replace an existing evaluation JSON (default: off).")
    evaluation.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True,
                            help="Show evaluation settings, per-trial progress, waiting heartbeats and results; --no-verbose hides progress (default: enabled).")

    report = commands.add_parser(
        "report",
        parents=[log_options],
        help="Render an evidence-bounded Markdown report.",
        formatter_class=_HelpFormatter,
    )
    report.add_argument("eval_output", metavar="EVALUATION",
                        help="Evaluation JSON produced by eval (required; no default).")
    report.add_argument("--output", default="report.md", metavar="PATH",
                        help="Output directory or Markdown file path; a directory gets report.md (default: report.md).")
    report.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Local report-writing skill directory containing SKILL.md (default: bundled report-evidence-summary for single-question reports; situation reports use built-in rendering unless a skill is supplied).")
    report.add_argument("--model", default=DEFAULT_REPORT_MODEL, metavar="MODEL",
                        help=f"Model used to write report prose when a report skill is invoked (default: {DEFAULT_REPORT_MODEL}).")
    report.add_argument("--env-file", default=".env", metavar="PATH",
                        help="Environment file for the report model provider when invoked (default: .env).")
    report.add_argument("--max-input-tokens", type=int, metavar="N", help="Override the report model input budget (default: model profile).")
    report.add_argument("--max-output-tokens", type=int, metavar="N", help="Override the report model output budget (default: model profile).")
    report.add_argument("--force", action="store_true",
                        help="Replace an existing Markdown report (default: off).")
    for command in (dataset, runner, evaluation, report):
        command.add_argument("--resume", metavar="SESSION_PATH",
            help="Continue a saved work session with identical inputs and settings; run-agent --page-url may explicitly extend its model-call budget. Cannot combine with --force (default: start a new session).")
    report.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True,
                        help="Show report section and publication progress (default: enabled).")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runs_root: str | Path | None = None,
    skill_agent_factory: Callable[..., Any] | None = None,
    browser_target_factory: Callable[..., Any] | None = None,
    extraction_provider_factory: Callable[..., Any] | None = None,
    situation_provider_factory: Callable[..., Any] | None = None,
) -> int:
    operation = OperationReport()
    code = 2
    with operation_scope(operation):
        try:
            code = _main(argv, runs_root=runs_root, skill_agent_factory=skill_agent_factory,
                         browser_target_factory=browser_target_factory,
                         extraction_provider_factory=extraction_provider_factory,
                         situation_provider_factory=situation_provider_factory)
            return code
        except BaseException as error:
            code = exception_exit_code(error)
            operation.error(error)
            raise
        finally:
            operation.finish(code)


def _main(
    argv: Sequence[str] | None = None,
    *,
    runs_root: str | Path | None = None,
    skill_agent_factory: Callable[..., Any] | None = None,
    browser_target_factory: Callable[..., Any] | None = None,
    extraction_provider_factory: Callable[..., Any] | None = None,
    situation_provider_factory: Callable[..., Any] | None = None,
) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    invocation = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(invocation)
    current_operation().configure(args, _resolve_output)
    args._invocation = invocation
    args._working_directory = str(Path.cwd().resolve())
    with language_scope(Language(args.lang)):
        return _run_parsed(args, parser, runs_root=runs_root,
            skill_agent_factory=skill_agent_factory, browser_target_factory=browser_target_factory,
            extraction_provider_factory=extraction_provider_factory,
            situation_provider_factory=situation_provider_factory)


def _run_parsed(args, parser, *, runs_root, skill_agent_factory, browser_target_factory,
                extraction_provider_factory, situation_provider_factory):
    if args.command == "run-agent" and args.page_url and not args.situation_config:
        if args.interactive is not None:
            parser.error(tr('--interactive and --no-interactive are project mode only; omit both with --page-url.'))
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            parser.error(
                tr('--page-url requires terminal stdin and stderr for sign-in, discovery consent, trusted full-answer qualification and any necessary manual MATCH review. Run directly in a terminal without piping input or redirecting stderr; no interaction flag is needed.')
            )
        args.interactive = True
    try:
        with language_scope(Language(args.lang)), command_log(getattr(args, "log", None)):
            # Announce after the log is installed; nested stage calls share this context.
            if args.lang not in {"en", "zh-TW", "ja", "ko", "zh-CN"}:
                print(tr('Language {p0}: model-generated content uses this code; interface, fixed headings and offline templates use English.', p0=args.lang), file=sys.stderr)
            code = 2
            try:
                code = _execute(
                    args, parser, runs_root=runs_root,
                    skill_agent_factory=skill_agent_factory,
                    browser_target_factory=browser_target_factory,
                    extraction_provider_factory=extraction_provider_factory,
                    situation_provider_factory=situation_provider_factory,
                )
            except BaseException as error:
                code = exception_exit_code(error)
                current_operation().error(error)
                raise
            finally:
                current_operation().finish(code)
            return code
    except OSError as error:
        current_operation().error(error)
        print(tr('lladar: cannot write log: {p0}', p0=error), file=sys.stderr)
        return 2


def _show_resume_instructions(args, error):
    """Offer a runnable command only for a session created by this invocation."""
    hint = getattr(error, "work_session_hint", None)
    if not hint:
        return
    import os
    import shlex
    quote = (lambda value: "'" + str(value).replace("'", "''") + "'") if os.name == "nt" else shlex.quote
    tokens = []
    url_variables = {}
    original = args._invocation
    index = 0
    while index < len(original):
        token = original[index]
        url_option = token.split("=", 1)[0]
        if url_option in {"--page-url", "--service-url"}:
            variable = "lladarResumePageUrl" if url_option == "--page-url" else "lladarResumeServiceUrl"
            url_variables[url_option] = variable
            tokens.extend([url_option, ("$" + variable, True)])
            index += 1 if "=" in token else 2
            continue
        if token in {"--output", "--resume", "--lang"}:
            index += 2
            continue
        if token == "--force" or token.startswith(("--output=", "--resume=", "--lang=")):
            index += 1
            continue
        tokens.append(token)
        index += 1
    tokens.extend(["--output", hint["output"], "--resume", hint["path"], "--lang", current_language().lang])
    print(tr('\nResume interrupted work'), file=sys.stderr)
    print(tr('  Working directory: ') + args._working_directory, file=sys.stderr)
    for key, label in (("knowledge", tr('Knowledge input')), ("dataset", tr('Dataset')),
                       ("responses", tr('Responses')), ("eval_output", tr('Evaluation')),
                       ("project", tr('Target project')), ("adapt", tr('Adapter file')), ("skill", tr('Skill directory')),
                       ("config", tr('Configuration file')), ("env_file", tr('Environment file'))):
        value = getattr(args, key, None)
        if value:
            for location in value if isinstance(value, list) else [value]:
                print(f"  {label}: {Path(location).resolve()}", file=sys.stderr)
    print(tr('  Output: ') + hint["output"], file=sys.stderr)
    print(tr('  Session directory: ') + hint["path"], file=sys.stderr)
    print(tr('  Keep the session directory and inputs unchanged; use the same Python environment.'), file=sys.stderr)
    if hint["persistence_failed"]:
        print(tr('  The last checkpoint write failed; resume uses the last successfully saved state.'), file=sys.stderr)
    print(tr('  Run these commands to continue (') + ("PowerShell" if os.name == "nt" else "shell") + "):", file=sys.stderr)
    print("  " + ("Set-Location -LiteralPath " if os.name == "nt" else "cd -- ") + quote(args._working_directory), file=sys.stderr)
    for option, variable in url_variables.items():
        prompt = tr('Enter the same ') + option
        if os.name == "nt":
            print("  $" + variable + " = [System.Net.NetworkCredential]::new('', (Read-Host " + quote(prompt) + " -AsSecureString)).Password", file=sys.stderr)
        else:
            print("  printf '%s' " + quote(prompt + ": "), file=sys.stderr)
            print("  read -r " + variable, file=sys.stderr)
    rendered = [token[0] if isinstance(token, tuple) and os.name == "nt" else
                '"' + token[0] + '"' if isinstance(token, tuple) else quote(token) for token in tokens]
    print("  lladar " + " ".join(rendered), file=sys.stderr)


def _execute(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    runs_root,
    skill_agent_factory,
    browser_target_factory,
    extraction_provider_factory,
    situation_provider_factory,
) -> int:
    try:
        resume = getattr(args, "resume", None)
        if resume is not None:
            if args.force:
                raise ValueError("--resume and --force cannot be combined")
            if getattr(args, "situation_config", None):
                raise ValueError("--resume is not supported for situation mode")
            saved = json.loads((Path(resume) / "session.json").read_text(encoding="utf-8"))
            from .work_session import digest
            if digest(saved["data"]) != saved["sha256"]:
                raise ValueError("checkpoint integrity check failed")
            default_output = {"create": ".", "run-agent": "responses.jsonl",
                              "eval": "evaluation.json", "report": "report.md"}[args.command]
            if args.output == default_output:
                args.output = saved["data"]["configuration"]["output"]
        filenames = {"run-agent": "responses.jsonl", "eval": "evaluation.json", "report": "report.md"}
        if args.command in filenames:
            args.output = str(_resolve_output(args.output, filenames[args.command]))
        elif args.command == "create" and args.create_command == "situation":
            args.output = str(_resolve_output(args.output, "situation.json"))
        if current_operation().stage != "test-dataset" and not current_operation().stage.endswith("-skill"):
            current_operation().set_output(args.output)
        if args.command == "create":
            if args.create_command.endswith("-skill"):
                stage = args.create_command.removesuffix("-skill")
                destination = create_skill_template(stage, args.output, force=args.force)
                uses = {
                    "test-dataset": "lladar create test-dataset --knowledge KNOWLEDGE --skill {skill} --output DATASET.jsonl",
                    "situation": "lladar create situation --instructions TEXT --stop-criteria TEXT --max-turns 3 --skill {skill} --output situation.json",
                    "run-agent": "lladar run-agent DATASET.jsonl --project PROJECT --skill {skill} --output responses.jsonl",
                    "eval": "lladar eval RESPONSES --skill {skill} --output evaluation.json",
                    "report": "lladar report EVALUATION --skill {skill} --output report.md",
                }
                print(tr('Created skill at {p0}', p0=destination))
                print(tr('Author guide: {p0}', p0=destination / 'AUTHORING.md'))
                skill_arg = (subprocess.list2cmdline([str(destination)]) if os.name == "nt"
                             else shlex.quote(str(destination)))
                print(tr('Use: ') + uses[stage].format(skill=skill_arg) + tr(' --lang ') + current_language().lang)
                print(tr('\nWork summary'))
                print(tr('  Work: Created an editable {p0} method template.', p0=stage))
                print(tr('  Output directory: {p0}', p0=destination))
                print(tr('  Files:'))
                print(tr('    {p0}\n      Active method instructions loaded by --skill.', p0=destination / 'SKILL.md'))
                print(tr('    {p0}\n      Tool contracts, customization guidance and usage examples.', p0=destination / 'AUTHORING.md'))
                print(tr('    {p0}\n      Language metadata for presentation and template resources.', p0=destination / 'language.json'))
                return 0
            if args.create_command == "situation":
                instructions = args.instructions if args.instructions is not None else args.observe
                if instructions is None:
                    instructions = Path(args.instructions_file or args.observe_file).read_text(encoding="utf-8")
                stop = args.stop_criteria if args.stop_criteria is not None else Path(args.stop_criteria_file).read_text(encoding="utf-8")
                create_situation(
                    instructions=instructions, stop_criteria=stop, max_turns=args.max_turns,
                    knowledge=args.knowledge, output=args.output, skill=args.skill,
                    model=args.model, env_file=args.env_file,
                    max_input_tokens=args.max_input_tokens,
                    max_output_tokens=args.max_output_tokens,
                    skill_agent_factory=skill_agent_factory, force=args.force,
                )
                print(tr('Created situation configuration at {p0}', p0=args.output))
                _show_work_summary("situation", args.output)
                return 0
            if args.controlled_variant_probes and args.controlled_variant_topics:
                parser.error(tr('--controlled-variant-probes and --controlled-variant-topics cannot be used together'))
            if args.controlled_variant_probes:
                if not sys.stdin.isatty():
                    parser.error(tr('--controlled-variant-probes requires terminal input; use --controlled-variant-topics for scripts'))
                controlled_variant_selector = select_controlled_variant_topics
                controlled_variant_topics = ()
            else:
                controlled_variant_selector = None
                controlled_variant_topics = normalize_controlled_variant_topics(args.controlled_variant_topics)
            output = _resolve_dataset_output(args.output)
            current_operation().set_output(output)
            records = create_test_dataset(
                [Path(path) for path in args.knowledge] if args.knowledge else None,
                from_github=args.from_github,
                resume=resume,
                output=output,
                count=args.count,
                question_type=args.question_type,
                controlled_variant_topics=controlled_variant_topics,
                controlled_variant_selector=controlled_variant_selector,
                seed=args.seed,
                model=args.model,
                skill=args.skill,
                skill_agent_factory=skill_agent_factory,
                env_file=args.env_file,
                temperature=args.temperature,
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=args.max_output_tokens,
                auto_window_ratio=args.auto_window_ratio,
                force=args.force,
                verbose=args.verbose,
            )
            metadata = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
            current_operation().record(count=len(records), status=metadata.get("status"))
            current_operation().record(summary=metadata.get("stats"))
            if isinstance(metadata.get("skipped"), list):
                current_operation().record(summary={"skipped": len(metadata["skipped"])})
            graph_path = Path(str(output) + ".graph.json")
            graph = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else {}
            concept_count = sum(node.get("type") == "concept" for node in graph.get("nodes", []))
            dimensions = graph.get("control_dimensions", [])
            selected_controls = sum(
                line.get("plan_type") == "controlled_invariance"
                for line in metadata["dataset"]["lines"]
            )
            status = (
                f" (status={metadata['status']}; graph_used={str(bool(graph)).lower()}; "
                f"methods={','.join(sorted({line.get('generation_method', 'graph') for line in metadata['dataset']['lines']}))}; "
                f"validation={metadata.get('validation', {}).get('status', 'unknown')}; concepts={concept_count}; "
                f"controlled_dimensions={len(dimensions)}; controlled_records={selected_controls}; "
                f"provenance={output}.generation.json)"
            )
            print(tr('Generated {p0} record(s) at {p1}{p2}', p0=len(records), p1=output, p2=status))
            if args.from_github:
                from .github_dataset import artifact_paths
                report_path, _ = artifact_paths(output)
                print(tr('Generation evidence report: {p0}', p0=report_path.resolve()))
            _show_work_summary("test-dataset", output)
            return 0
        if args.command == "run-agent":
            if args.adapt is not None and args.project is None and args.page_url is None:
                parser.error(tr('--adapt requires an explicit --project or --page-url'))
            if args.demo_question is not None and (args.page_url is None or not args.demo_question.strip()):
                parser.error(tr('--demo-question requires --page-url and a non-empty question'))
            if args.adapter is not None and args.page_url is None:
                parser.error('--adapter requires --page-url')
            if args.adapt is not None and args.page_url is not None:
                parser.error('--adapt is project mode only; use --adapter with --page-url')
            if args.situation_config:
                if args.dataset is not None or args.skill is not None:
                    parser.error(tr('--situation-config cannot be combined with DATASET or --skill'))
                if args.num_scenarios is None:
                    parser.error(tr('--num-scenarios is required with --situation-config'))
                if args.page_url is not None:
                    parser.error(tr('multi-turn browser sessions are not supported yet'))
                if any((args.confirm_browser_run,
                        args.fresh_browser_profile, args.allow_response_model_transfer)):
                    parser.error(tr('browser options do not apply to situation mode'))
                completed = run_situation(
                    args.situation_config, args.output,
                    project=args.project or ".", num_scenarios=args.num_scenarios,
                    model=args.model or DEFAULT_ADAPTER_MODEL, env_file=args.env_file,
                    target_python=args.target_python, timeout=args.timeout,
                    runs_root=runs_root, force=args.force,
                    service_url=args.service_url,
                    verbose=args.verbose,
                    max_input_tokens=args.max_input_tokens, max_output_tokens=args.max_output_tokens,
                    max_tool_calls=args.max_tool_calls, graphify=args.graphify,
                    graphify_python=args.graphify_python, interactive=args.interactive,
                    adapt=args.adapt,
                )
                print(tr('Completed {p0} situation trial(s) at {p1}', p0=completed, p1=args.output))
                current_operation().record(count=completed)
                _show_work_summary("run-agent", args.output, situation=True,
                                   situation_config=args.situation_config)
                return 0
            if args.dataset is None:
                parser.error(tr('DATASET is required unless --situation-config is set'))
            if args.num_scenarios is not None:
                parser.error(tr('--num-scenarios requires --situation-config'))
            project = args.project if args.project is not None else (None if args.page_url else ".")
            completed = run_agent(
                args.dataset,
                args.output,
                project=project,
                page_url=args.page_url,
                probe_question=args.demo_question,
                env_file=args.env_file,
                force=args.force,
                resume=resume,
                verbose=args.verbose,
                runs_root=runs_root,
                model=args.model,
                target_python=args.target_python,
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=args.max_output_tokens,
                timeout=args.timeout,
                max_tool_calls=args.max_tool_calls,
                extend_model_calls=args.extend_model_calls,
                skill=args.skill,
                seed=args.seed,
                interactive=args.interactive,
                graphify=args.graphify,
                graphify_python=args.graphify_python,
                adapt=args.adapter if args.page_url else args.adapt,
                service_url=args.service_url,
                confirm_browser_run=args.confirm_browser_run,
                browser_session_policy=args.conversation_policy if args.page_url else None,
                fresh_browser_profile=args.fresh_browser_profile,
                browser_target_factory=browser_target_factory,
                extraction_provider_factory=extraction_provider_factory,
                allow_response_model_transfer=args.allow_response_model_transfer,
                strategy_agent_factory=skill_agent_factory,
            )
            run_path = Path(str(args.output) + ".run.json")
            try:
                run_metadata = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                run_metadata = {}
            if run_metadata.get("status") == "blocked":
                blocker = run_metadata.get("blocker", {})
                diagnostic = blocker.get("diagnostic", {}) if isinstance(blocker, dict) else {}
                reason = diagnostic.get("reason", "browser_failed")
                print(tr('Browser run blocked after {p0} completed record(s). reason={reason}',
                         p0=completed, reason=reason))
                current_operation().record(count=completed, status="blocked")
                _show_work_summary("run-agent", args.output)
                return 1
            print(tr('Answered {p0} record(s) at {p1}', p0=completed, p1=args.output))
            current_operation().record(count=completed)
            _show_work_summary("run-agent", args.output)
            return 0
        if args.command == "eval":
            if args.situation_config:
                result = evaluate_situation(
                    args.responses, args.situation_config, output=args.output,
                    verbose=args.verbose,
                    max_input_tokens=args.max_input_tokens, max_output_tokens=args.max_output_tokens,
                    model=args.model, env_file=args.env_file, strict=args.strict,
                    force=args.force,
                    criteria=args.criteria, skill=args.skill,
                    skill_agent_factory=skill_agent_factory,
                    provider_factory=situation_provider_factory,
                )
                summary = result["summary"]
                current_operation().record(count=len(result["items"]), summary=summary)
                print(tr('Evaluated {p0} situation trial(s) at {p1} (valid_determinate={p2}, judge_error={p3}, execution_error={p4}, invalid={p5}, indeterminate={p6})', p0=len(result['items']), p1=args.output, p2=summary['valid_determinate'], p3=summary['judge_error'], p4=summary['execution_error'], p5=summary['invalid'], p6=summary['indeterminate']))
                _show_work_summary("eval", args.output, situation=True)
                return 0
            result = evaluate(
                args.responses,
                verbose=args.verbose,
                max_input_tokens=args.max_input_tokens, max_output_tokens=args.max_output_tokens,
                output=args.output,
                skill=args.skill,
                criteria=args.criteria,
                model=args.model,
                env_file=args.env_file,
                strict=args.strict,
                force=args.force,
                resume=resume,
                skill_agent_factory=skill_agent_factory,
            )
            print(tr('Evaluated {p0} record(s) at {p1}', p0=result['summary']['evaluated'], p1=args.output))
            current_operation().record(summary=result["summary"])
            _show_work_summary("eval", args.output)
            return 0
        destination = create_report(
            args.eval_output,
            args.output,
            model=args.model,
            env_file=args.env_file,
            skill=args.skill,
            force=args.force,
            resume=resume, verbose=args.verbose, skill_agent_factory=skill_agent_factory,
            max_input_tokens=args.max_input_tokens,max_output_tokens=args.max_output_tokens,
        )
        print(tr('Created evaluation report at {p0}', p0=destination))
        _show_work_summary("report", destination)
        return 0
    except NeedsConfirmation as error:
        current_operation().error(error)
        print(tr('lladar: {p0}', p0=error), file=sys.stderr)
        return 3
    except ProviderError as error:
        current_operation().error(error)
        print(tr('lladar: provider generation failed'), file=sys.stderr)
        _show_resume_instructions(args, error)
        return 2
    except KeyboardInterrupt as error:
        current_operation().error(error)
        print(tr('lladar: Cancelled.'), file=sys.stderr)
        _show_resume_instructions(args, error)
        return 130
    except Exception as error:
        current_operation().error(error)
        if isinstance(error, ValueError) and str(error) == "--resume and --force cannot be combined":
            message = str(error)
        elif args.command == "run-agent" and args.page_url:
            if isinstance(error, AssertionError) and (
                browser_target_factory is not None or extraction_provider_factory is not None
            ):
                raise
            from .browser_target import browser_error_message

            message = browser_error_message(error)
        elif isinstance(error, (LladarError, FileExistsError, OSError, ValueError, RuntimeError)):
            message = str(error)
        else:
            raise
        print(tr('lladar: {p0}', p0=localize_error(message)), file=sys.stderr)
        _show_resume_instructions(args, error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
