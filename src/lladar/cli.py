from __future__ import annotations

import argparse
import json
import os
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
from .command_log import command_log
from .controlled_variants import normalize_controlled_variant_topics, select_controlled_variant_topics
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .interfaces import NeedsConfirmation
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .runner import DEFAULT_ADAPTER_MODEL, run_agent
from .skill_templates import create_skill_template
from .situation import create_situation, evaluate_situation, run_situation


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if text.startswith("Additional guidance:"):
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
            return action.help
        return super()._get_help_string(action)


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
    path = Path(destination)
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
        "test-dataset": "Generated questions and source-supported expected answers for testing an Agent.",
        "situation": "Compiled instructions into a reusable multi-turn situation configuration.",
        "run-agent": "Ran the target Agent and saved its responses and execution evidence.",
        "eval": "Evaluated saved responses and recorded judgments and aggregate results.",
        "report": "Rendered the saved evaluation into a readable Markdown report.",
    }
    main_descriptions = {
        "test-dataset": "Test records: questions, expected answers and empty actual_response fields.",
        "situation": "Situation instructions, stopping rules, variation axes, methods and evaluation rubric.",
        "run-agent": ("Multi-turn conversation transcripts for each situation trial." if situation else
                      "Questions, expected answers and actual Agent responses."),
        "eval": "Per-trial judgments, reasons, evaluation settings and summary statistics.",
        "report": "Human-readable findings, result tables and limitations based on saved evaluation evidence.",
    }
    sidecars = {
        "test-dataset": [
            (".generation.json", "Generation methods, source provenance and validation results."),
            (".graph.json", "Knowledge graph and declared control dimensions used for generation."),
        ],
        "report": [(".report.json", "Report section citations and work-session metadata.")],
        "run-agent": ([
            (".turns.jsonl", "Individual conversation turns and their identifiers."),
            (".scenarios.jsonl", "Generated scenarios, starting messages and selected variations."),
            (".calibration.json", "Session adapter calibration evidence."),
            (".run.json", "Run configuration, identity, adapter evidence and execution errors."),
        ] if situation else [
            (".trials.jsonl", "Individual repeated trials, responses and failure diagnostics."),
            (".run.json", "Run configuration, selected cases, target evidence and execution metadata."),
        ]),
    }
    print("\nWork summary")
    print(f"  Work: {descriptions[stage]}")
    print(f"  Output directory: {path.parent}")
    print("  Files:")
    print(f"    {path}\n      {main_descriptions[stage]}")
    for suffix, description in sidecars.get(stage, []):
        artifact = Path(str(path) + suffix)
        if artifact.is_file():
            print(f"    {artifact}\n      {description}")
    metadata_path = {"test-dataset": Path(str(path) + ".generation.json"),
                     "run-agent": Path(str(path) + ".run.json"), "eval": path,
                     "report": Path(str(path) + ".report.json")}.get(stage)
    if metadata_path and metadata_path.is_file():
        try:
            session = json.loads(metadata_path.read_text(encoding="utf-8")).get("session")
        except (json.JSONDecodeError, UnicodeDecodeError):
            session = None
        if session:
            print(f"  Work session: {session['path']}")
            print("    Continue with --resume SESSION_PATH and the same inputs/settings.")
    if stage == "test-dataset":
        next_step = ["lladar", "run-agent", str(path), "--project", "PROJECT"]
    elif stage == "situation":
        next_step = ["lladar", "run-agent", "--situation-config", str(path),
                     "--project", "PROJECT", "--num-scenarios", "N"]
    elif stage == "run-agent":
        next_step = ["lladar", "eval", str(path)]
        if situation_config:
            next_step += ["--situation-config", str(Path(situation_config).resolve())]
    elif stage == "eval":
        next_step = ["lladar", "report", str(path)]
    else:
        print(f"  Next: Open {path} to review the report.")
        return
    print(f"  Next: {_display_command(next_step)}")
    if stage in {"test-dataset", "situation"}:
        print("    Replace PROJECT with your target project directory." +
              (" Replace N with a positive scenario count." if stage == "situation" else ""))


def build_parser() -> argparse.ArgumentParser:
    log_options = argparse.ArgumentParser(add_help=False)
    log_options.add_argument(
        "--log", metavar="PATH", default=argparse.SUPPRESS,
        help="Also save emitted progress, traces, summaries and errors to a new UTF-8 log file (default: off; existing files are kept).",
    )
    parser = argparse.ArgumentParser(
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
        usage="%(prog)s [-h] [--log PATH] ARTIFACT ...",
        description="Create a test dataset, situation configuration, or editable skill template.",
        formatter_class=_HelpFormatter,
        parents=[log_options],
        epilog="Available commands:\n\n" + "\n\n".join(
            f"{title}:\n" + "\n".join(
                f"  {name:<20} {description}"
                for name, description in entries.items()
            )
            for title, entries in (
                ("Main workflows", workflow_commands),
                ("Skill templates (advanced)", skill_commands),
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
            help="Overwrite SKILL.md and AUTHORING.md in an existing directory; keep other files (default: off).",
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
            "Run one-question DATASET records through an inspected project or calibrated website, "
            "or use --situation-config for generated multi-turn conversations with a project session driver."
        ),
        epilog=(
            "Additional guidance:\n\n"
            "Browser setup:\n"
            "Install Chromium: python -m playwright install chromium. --page-url always uses the guided terminal workflow; "
            "omit --interactive and --no-interactive (project mode only). "
            "Browser mode requires terminal stdin and stderr; without them it stops before opening the browser.\n\n"
            "Sign-in and calibration:\n"
            "With --page-url, "
            "sign in yourself in the isolated browser, submit the exact calibration question "
            "shown in the terminal, and wait for the site's final answer before pressing Enter.\n\n"
            "Review and approval:\n"
            "Review the website requests, model destination, real response content transfer and budgets together. "
            "One YES approves BOTH website requests and model transfer; the two approval flags still grant only their named scopes. "
            "Then type MATCH after independent verification in the color terminal review (stderr), not an HTML tab. "
            "Approval flags do not skip review. NO_COLOR or TERM=dumb disables color; redirected review output is refused. "
            "The full verification response appears in terminal scrollback; no review file is created. "
            "Browser sign-in and capture are still required.\n\n"
            "Execution limits:\n"
            "At most N+2 model calls: calibration + verification + N scheduled dataset trials (including repeats). "
            "Each call is capped at 8192 output tokens. A shared 60-minute deadline starts after approval, "
            "before calibration extraction, and also includes verification, MATCH review and dataset execution. "
            f"Input is capped at {MAX_INPUT_BYTES // 1024} KiB including JSON framing; no truncation, chunking or automatic extraction retries. "
            "On HTTP 401/403, further browser requests are blocked; sign in again before starting a newly approved run. "
            f"Each captured response is limited to {MAX_CAPTURE_BYTES // (1024 * 1024)} MiB of decoded bytes; --timeout does not raise this limit. "
            "Browser startup and page navigation separately allow 300 seconds (5 minutes); --timeout controls website response waiting, not navigation. "
            "Verification and dataset requests are also bounded by the remaining shared deadline.\n\n"
            "Outputs and local session:\n"
            "Final answers are saved in responses and trials; these output files exclude cookies, tokens and raw streams. "
            "By default, the isolated browser profile retains local sign-in state in .lladar/browser-profiles "
            "under the working directory. --fresh-browser-profile uses a temporary signed-out profile removed when the browser target closes.\n\n"
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
        help="Question website opened in a visible browser for manual sign-in, calibration and replay (default: project mode; unavailable with --situation-config).",
    )
    runner.add_argument(
        "--calibration-question", metavar="TEXT",
        help="Use this exact question for both manual browser calibration and automatic verification; requires --page-url (default: separate random LLaDAR calibration/verification questions).",
    )
    runner.add_argument(
        "--output", default="responses.jsonl", metavar="PATH",
        help="Output directory or responses JSONL file path; a directory gets responses.jsonl (default: responses.jsonl). Single-question mode also writes PATH.trials.jsonl and PATH.run.json; situation mode writes PATH.turns.jsonl, PATH.scenarios.jsonl, PATH.calibration.json and PATH.run.json.",
    )
    runner.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Use a local method directory containing SKILL.md for single-question runs (default: bundled run-agent-stability; unavailable with --situation-config).")
    runner.add_argument("--situation-config", metavar="PATH",
                        help="Use a frozen situation JSON for multi-turn project sessions (default: single-question DATASET mode; cannot combine with DATASET or --skill).")
    runner.add_argument("--adapt", metavar="PATH",
                        help="Reuse an existing Python adapter file with an explicit --project. Skip discovery/generation and verify it in project copies using the selected DATASET or situation protocol; stop on failure without rewriting the supplied file (default: automatic adapter discovery/generation).")
    runner.add_argument("--num-scenarios", type=int, metavar="N",
                        help="Number of distinct scenarios to generate and run; required with --situation-config, unused otherwise (no default).")
    runner.add_argument("--seed", type=int, default=0,
                        help="Deterministic random selection seed for single-question trials (default: 0).")
    runner.add_argument(
        "--model", default=None, metavar="MODEL",
        help=f"Project coding model (default: {DEFAULT_ADAPTER_MODEL}), or browser answer extraction model (default: {DEFAULT_EXTRACTION_MODEL}; Gemini API only).",
    )
    runner.add_argument(
        "--max-input-tokens", type=int, metavar="N",
        help="Override the project auto-adapter input token budget for discovery, generation and repair (default: selected model profile). Does not change the target Agent or browser extraction budgets.",
    )
    runner.add_argument(
        "--max-output-tokens", type=int, metavar="N",
        help="Override the project auto-adapter output token budget for discovery, generation and repair (default: selected model profile). Does not change the target Agent or browser extraction budgets.",
    )
    runner.add_argument(
        "--env-file", default=".env", metavar="PATH",
        help="Environment file for the model provider and target process; existing environment variables win (default: .env).",
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
        "--max-tool-calls", type=int, default=100, metavar="N",
        help="Maximum tool calls per single-question discovery or adapter-repair agent turn (default: 100).",
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
    runner.add_argument(
        "--confirm-browser-run", action="store_true",
        help="Preapprove only the browser verification and scheduled dataset requests; model-transfer approval is separate and MATCH review remains required (default: off).",
    )
    runner.add_argument(
        "--fresh-browser-profile", action="store_true",
        help="Use a temporary signed-out browser profile for this run instead of the reusable site profile (default: off).",
    )
    runner.add_argument(
        "--allow-response-model-transfer", action="store_true",
        help="Approve real captured response content transfer to --model for this run only; at most N+2 calls for N scheduled dataset trials including repeats, 8192 output tokens per call, shared 60 minutes from post-approval calibration extraction. Website requests and local verification remain separate (default: off).",
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
        help="Show LLaDAR progress, questions/available responses, coding-agent exploration and repair traces in both project modes, situation calibration and turn progress, and safe browser diagnostics; use --no-verbose to hide them (default: enabled).",
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
            help="Continue a saved work session with identical inputs and settings; cannot combine with --force (default: start a new session).")
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
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    invocation = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(invocation)
    args._invocation = invocation
    args._working_directory = str(Path.cwd().resolve())
    if args.command == "run-agent" and args.page_url and not args.situation_config:
        if args.interactive is not None:
            parser.error("--interactive and --no-interactive are project mode only; omit both with --page-url.")
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            parser.error(
                "--page-url requires terminal stdin and stderr for sign-in, calibration, consent and MATCH review. "
                "Run directly in a terminal without piping input or redirecting stderr; no interaction flag is needed."
            )
        args.interactive = True
    try:
        with command_log(getattr(args, "log", None)):
            return _execute(
                args, parser, runs_root=runs_root,
                skill_agent_factory=skill_agent_factory,
                browser_target_factory=browser_target_factory,
                extraction_provider_factory=extraction_provider_factory,
                situation_provider_factory=situation_provider_factory,
            )
    except OSError as error:
        print(f"lladar: cannot write log: {error}", file=sys.stderr)
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
        if token in {"--output", "--resume"}:
            index += 2
            continue
        if token == "--force" or token.startswith(("--output=", "--resume=")):
            index += 1
            continue
        tokens.append(token)
        index += 1
    tokens.extend(["--output", hint["output"], "--resume", hint["path"]])
    print("\nResume interrupted work", file=sys.stderr)
    print("  Working directory: " + args._working_directory, file=sys.stderr)
    for key, label in (("knowledge", "Knowledge input"), ("dataset", "Dataset"),
                       ("responses", "Responses"), ("eval_output", "Evaluation"),
                       ("project", "Target project"), ("adapt", "Adapter file"), ("skill", "Skill directory"),
                       ("config", "Configuration file"), ("env_file", "Environment file")):
        value = getattr(args, key, None)
        if value:
            for location in value if isinstance(value, list) else [value]:
                print(f"  {label}: {Path(location).resolve()}", file=sys.stderr)
    print("  Output: " + hint["output"], file=sys.stderr)
    print("  Session directory: " + hint["path"], file=sys.stderr)
    print("  Keep the session directory and inputs unchanged; use the same Python environment.", file=sys.stderr)
    if hint["persistence_failed"]:
        print("  The last checkpoint write failed; resume uses the last successfully saved state.", file=sys.stderr)
    print("  Run these commands to continue (" + ("PowerShell" if os.name == "nt" else "shell") + "):", file=sys.stderr)
    print("  " + ("Set-Location -LiteralPath " if os.name == "nt" else "cd -- ") + quote(args._working_directory), file=sys.stderr)
    for option, variable in url_variables.items():
        prompt = "Enter the same " + option
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
                print(f"Created skill at {destination}")
                print(f"Author guide: {destination / 'AUTHORING.md'}")
                skill_arg = (subprocess.list2cmdline([str(destination)]) if os.name == "nt"
                             else shlex.quote(str(destination)))
                print("Use: " + uses[stage].format(skill=skill_arg))
                print("\nWork summary")
                print(f"  Work: Created an editable {stage} method template.")
                print(f"  Output directory: {destination}")
                print("  Files:")
                print(f"    {destination / 'SKILL.md'}\n      Active method instructions loaded by --skill.")
                print(f"    {destination / 'AUTHORING.md'}\n      Tool contracts, customization guidance and usage examples.")
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
                print(f"Created situation configuration at {args.output}")
                _show_work_summary("situation", args.output)
                return 0
            if args.controlled_variant_probes and args.controlled_variant_topics:
                parser.error("--controlled-variant-probes and --controlled-variant-topics cannot be used together")
            if args.controlled_variant_probes:
                if not sys.stdin.isatty():
                    parser.error("--controlled-variant-probes requires terminal input; use --controlled-variant-topics for scripts")
                controlled_variant_selector = select_controlled_variant_topics
                controlled_variant_topics = ()
            else:
                controlled_variant_selector = None
                controlled_variant_topics = normalize_controlled_variant_topics(args.controlled_variant_topics)
            output = _resolve_dataset_output(args.output)
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
            print(f"Generated {len(records)} record(s) at {output}{status}")
            if args.from_github:
                from .github_dataset import artifact_paths
                report_path, _ = artifact_paths(output)
                print(f"Generation evidence report: {report_path.resolve()}")
            _show_work_summary("test-dataset", output)
            return 0
        if args.command == "run-agent":
            if args.adapt is not None and (args.project is None or args.page_url is not None):
                parser.error("--adapt requires an explicit --project and cannot be combined with --page-url")
            if args.calibration_question is not None and args.page_url is None:
                parser.error("--calibration-question requires --page-url")
            if args.situation_config:
                if args.dataset is not None or args.skill is not None:
                    parser.error("--situation-config cannot be combined with DATASET or --skill")
                if args.num_scenarios is None:
                    parser.error("--num-scenarios is required with --situation-config")
                if args.page_url is not None:
                    parser.error("multi-turn browser sessions are not supported yet")
                if any((args.confirm_browser_run,
                        args.fresh_browser_profile, args.allow_response_model_transfer)):
                    parser.error("browser options do not apply to situation mode")
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
                print(f"Completed {completed} situation trial(s) at {args.output}")
                _show_work_summary("run-agent", args.output, situation=True,
                                   situation_config=args.situation_config)
                return 0
            if args.dataset is None:
                parser.error("DATASET is required unless --situation-config is set")
            if args.num_scenarios is not None:
                parser.error("--num-scenarios requires --situation-config")
            project = args.project if args.project is not None else (None if args.page_url else ".")
            completed = run_agent(
                args.dataset,
                args.output,
                project=project,
                page_url=args.page_url,
                calibration_question=args.calibration_question,
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
                skill=args.skill,
                seed=args.seed,
                interactive=args.interactive,
                graphify=args.graphify,
                graphify_python=args.graphify_python,
                adapt=args.adapt,
                service_url=args.service_url,
                confirm_browser_run=args.confirm_browser_run,
                fresh_browser_profile=args.fresh_browser_profile,
                browser_target_factory=browser_target_factory,
                extraction_provider_factory=extraction_provider_factory,
                allow_response_model_transfer=args.allow_response_model_transfer,
                strategy_agent_factory=skill_agent_factory,
            )
            print(f"Answered {completed} record(s) at {args.output}")
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
                print(f"Evaluated {len(result['items'])} situation trial(s) at {args.output} "
                      f"(valid_determinate={summary['valid_determinate']}, "
                      f"judge_error={summary['judge_error']}, "
                      f"execution_error={summary['execution_error']}, "
                      f"invalid={summary['invalid']}, indeterminate={summary['indeterminate']})")
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
            print(f"Evaluated {result['summary']['evaluated']} record(s) at {args.output}")
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
        print(f"Created evaluation report at {destination}")
        _show_work_summary("report", destination)
        return 0
    except NeedsConfirmation as error:
        print(f"lladar: {error}", file=sys.stderr)
        return 3
    except ProviderError as error:
        print("lladar: provider generation failed", file=sys.stderr)
        _show_resume_instructions(args, error)
        return 2
    except KeyboardInterrupt as error:
        print("lladar: Cancelled.", file=sys.stderr)
        _show_resume_instructions(args, error)
        return 130
    except Exception as error:
        if args.command == "run-agent" and args.page_url:
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
        print(f"lladar: {message}", file=sys.stderr)
        _show_resume_instructions(args, error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
