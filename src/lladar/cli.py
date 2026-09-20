from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .artifact_schema import artifact_version, validate_artifact
from .api import DEFAULT_DATASET_MODEL, DEFAULT_MODEL, create_test_dataset
from .configuration import load_test_dataset_config, validate_test_dataset_input_paths
from .evaluation import DEFAULT_EVALUATION_MODEL, DEFAULT_EVALUATION_PROMPT, evaluate
from .exceptions import LladarError, ProviderError
from .providers import LLMProvider
from .progress import ProgressReporter
from .runner import AkashaAdapterController, AdapterController, run_agent
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .interfaces import NeedsConfirmation
from .skill import SkillError, install_skill, list_skills, uninstall_skill


_CONFIG_TEMPLATE = """schema_version = {schema_version}

[test_dataset]
# Replace this example with one or more .txt/.md files or directories.
knowledge = ["./knowledge"]
count = 0

# Optional domain and question-style guidance. Set at most one.
# prompt = "Prefer concise questions for restaurant recommendations."
# prompt_file = "./dataset-prompt.md"

# Optional selection and generation settings.
# chunk_size = "auto"
# overlap = 0.1
# seed = 1234
# policies = ["builtin:general-social-context"]
# model = "gemini:gemini-3.7-flash"

# Optional model-profile overrides.
# max_input_tokens = 1048576
# max_output_tokens = 65536
# auto_window_ratio = 0.8

# Optional paths and runtime behavior.
# output = "./test-dataset.jsonl"
# env_file = ".env"
# verbose = true
# trace = false
# trace_console = false
# trace_root = ".lladar/runs"
# strict = false
# cache = false
# cache_dir = ".lladar/cache"
# refresh_cache = false
"""

_DATASET_OPTION_FLAGS = {
    "knowledge": ("--knowledge",),
    "prompt": ("--prompt",),
    "prompt_file": ("--prompt-file",),
    "chunk_size": ("--chunk-size",),
    "overlap": ("--overlap",),
    "count": ("--count",),
    "seed": ("--seed",),
    "policies": ("--policy",),
    "model": ("--model",),
    "max_input_tokens": ("--max-input-tokens",),
    "max_output_tokens": ("--max-output-tokens",),
    "auto_window_ratio": ("--auto-window-ratio",),
    "verbose": ("--verbose", "--no-verbose"),
    "trace": ("--trace", "--no-trace"),
    "trace_console": ("--trace-console", "--no-trace-console"),
    "trace_root": ("--trace-root",),
    "output": ("--output",),
    "env_file": ("--env-file",),
    "strict": ("--strict", "--no-strict"),
    "cache": ("--cache", "--no-cache"),
    "cache_dir": ("--cache-dir",),
    "refresh_cache": ("--refresh-cache", "--no-refresh-cache"),
}


def _option_is_explicit(argv: Sequence[str], flags: tuple[str, ...]) -> bool:
    return any(
        token == flag or token.startswith(f"{flag}=")
        for token in argv
        for flag in flags
    )


def _merge_dataset_config(
    args: argparse.Namespace,
    settings: dict[str, object],
    argv: Sequence[str],
) -> None:
    prompt_is_explicit = _option_is_explicit(
        argv, _DATASET_OPTION_FLAGS["prompt"] + _DATASET_OPTION_FLAGS["prompt_file"]
    )
    for key, value in settings.items():
        if key in ("prompt", "prompt_file") and prompt_is_explicit:
            continue
        flags = _DATASET_OPTION_FLAGS.get(key)
        if flags is not None and not _option_is_explicit(argv, flags):
            setattr(args, key, value)


def _comparison_value(key: str, value: object) -> object:
    if key == "knowledge":
        return tuple(Path(item).resolve() for item in value)  # type: ignore[arg-type]
    if key in ("prompt_file", "output", "env_file", "cache_dir", "trace_root"):
        return Path(value).resolve()  # type: ignore[arg-type]
    if key == "policies":
        return tuple(
            item if str(item).startswith("builtin:") else Path(item).resolve()
            for item in value  # type: ignore[union-attr]
        )
    return value


def _overridden_config_keys(
    args: argparse.Namespace,
    settings: dict[str, object],
    argv: Sequence[str],
) -> list[str]:
    overridden: list[str] = []
    prompt_flags = _DATASET_OPTION_FLAGS["prompt"] + _DATASET_OPTION_FLAGS["prompt_file"]
    if _option_is_explicit(argv, prompt_flags) and (
        "prompt" in settings or "prompt_file" in settings
    ):
        cli_key = "prompt_file" if _option_is_explicit(
            argv, _DATASET_OPTION_FLAGS["prompt_file"]
        ) else "prompt"
        config_key = "prompt_file" if "prompt_file" in settings else "prompt"
        if cli_key != config_key or _comparison_value(
            cli_key, getattr(args, cli_key)
        ) != _comparison_value(config_key, settings[config_key]):
            overridden.append("prompt selector")

    for key, config_value in settings.items():
        if key in ("prompt", "prompt_file"):
            continue
        flags = _DATASET_OPTION_FLAGS.get(key)
        if flags is None or not _option_is_explicit(argv, flags):
            continue
        if _comparison_value(key, getattr(args, key)) != _comparison_value(
            key, config_value
        ):
            overridden.append(key)
    return overridden


def _warn_about_overrides(config_path: str | Path, keys: Sequence[str]) -> None:
    if not keys:
        return
    reporter = ProgressReporter(enabled=True)
    reporter.emit(
        "WARN",
        f"command-line options override {Path(config_path).name} settings:",
    )
    for key in keys:
        print(f"       {key}", file=sys.stderr, flush=True)


def _parse_chunk_size(value: str) -> int | str:
    if value == "auto":
        return value
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            'chunk size must be a positive integer or "auto"'
        ) from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            'chunk size must be a positive integer or "auto"'
        )
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _window_ratio(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "value must satisfy 0 < value <= 1"
        ) from error
    if not 0 < parsed <= 1:
        raise argparse.ArgumentTypeError("value must satisfy 0 < value <= 1")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _reserve_default_dataset_output(
    *,
    directory: Path | None = None,
    timestamp: datetime | None = None,
) -> Path:
    root = directory or Path.cwd()
    stem = f"test-dataset-{(timestamp or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    suffix = 0
    while True:
        name = f"{stem}{'' if suffix == 0 else f'-{suffix}'}.jsonl"
        path = root / name
        try:
            with path.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            suffix += 1
            continue
        return path


def _discard_empty_reservation(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass

class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32, width=100)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lladar",
        description="Generate controlled datasets for inspecting implicit assumptions in LLM agents.",
        formatter_class=_HelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    create = commands.add_parser(
        "create",
        help="Create a LLaDAR artifact.",
        description="Create a LLaDAR artifact.",
        formatter_class=_HelpFormatter,
    )
    create_commands = create.add_subparsers(
        dest="create_command",
        required=True,
        metavar="ARTIFACT",
    )
    config = create_commands.add_parser(
        "config",
        help="Generate an editable test-dataset configuration template.",
        description="Generate an editable test-dataset configuration template.",
        formatter_class=_HelpFormatter,
    )
    config.add_argument(
        "--output",
        default="config.toml",
        metavar="PATH",
        help=(
            "Template destination. Existing files are not overwritten. "
            "Default: config.toml."
        ),
    )
    config.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing configuration file.",
    )
    benchmark = create_commands.add_parser(
        "dataset", help="Compatibility alias for test-dataset --convert-from.",
    )
    benchmark.add_argument("--convert-from", required=True, metavar="SOURCE")
    benchmark.add_argument("--output", metavar="DIRECTORY")
    benchmark.add_argument("--model", default="gemini:gemini-3.7-flash", metavar="MODEL",
                           help="Akasha exploration model. Default: gemini:gemini-3.7-flash.")
    benchmark.add_argument("--env-file", default=".env", metavar="PATH",
                           help="Provider credentials file. Default: .env.")
    dataset = create_commands.add_parser(
        "test-dataset",
        help="Generate controlled source-grounded question groups.",
        description=(
            "Generate schema-v2 question groups from knowledge sources, or import "
            "a source-backed schema-v3 external benchmark with --convert-from."
        ),
        epilog="""Examples:
  lladar create test-dataset --knowledge ./knowledge
  lladar create test-dataset --config config.toml
  lladar create test-dataset --knowledge guide.md --chunk-size auto --strict
  lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
  lladar create test-dataset --knowledge guide.md --chunk-size auto --max-output-tokens 32768
  lladar create test-dataset --convert-from ./benchmark-source --output ./benchmark-bundle""",
        formatter_class=_HelpFormatter,
    )
    dataset.add_argument(
        "--knowledge",
        nargs="+",
        metavar="PATH",
        help=(
            "Files or directories containing knowledge documents. Directories are "
            "searched recursively for .txt and .md files; multiple paths are allowed. "
            "Required unless --config or --convert-from is used."
        ),
    )
    dataset.add_argument(
        "--convert-from",
        metavar="SOURCE",
        help="Import a benchmark from a local directory or public GitHub repository URL.",
    )
    guidance = dataset.add_mutually_exclusive_group()
    guidance.add_argument(
        "--prompt",
        metavar="GUIDANCE",
        help=(
            "Optional inline domain context or question-style guidance. Core schema "
            "and validation rules cannot be overridden."
        ),
    )
    guidance.add_argument(
        "--prompt-file",
        metavar="PATH",
        help=(
            "UTF-8 file containing domain context or question-style guidance. "
            "Cannot be combined with --prompt."
        ),
    )
    dataset.add_argument(
        "--chunk-size",
        type=_parse_chunk_size,
        default="auto",
        metavar="N|auto",
        help=(
            "Positive character count for fixed chunks, or 'auto' for Semantic "
            "chunking with the language model. Default: auto."
        ),
    )
    dataset.add_argument(
        "--overlap",
        type=float,
        default=0.1,
        metavar="RATIO",
        help=(
            "Fraction of each fixed chunk repeated in the next chunk (0 <= value < 1). "
            "Ignored when --chunk-size auto is used. Default: 0.1."
        ),
    )
    dataset.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "TOML file containing test-dataset settings. Command-line options "
            "override config values."
        ),
    )
    dataset.add_argument(
        "--count",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help=(
            "Maximum number of ready question groups. Use 0 to process every "
            "candidate chunk without a limit. Skipped and duplicate candidates "
            "do not consume a positive quota. Default: 0."
        ),
    )
    dataset.add_argument(
        "--seed",
        type=int,
        metavar="N",
        help="Optional seed for reproducible candidate ordering.",
    )
    dataset.add_argument(
        "--policy",
        action="append",
        dest="policies",
        metavar="POLICY",
        help=(
            "Exact generation-policy selection. Repeat for multiple policies. Use "
            "builtin:general-social-context or a local TOML path."
        ),
    )
    dataset.add_argument(
        "--model",
        default=DEFAULT_DATASET_MODEL,
        metavar="MODEL",
        help=(
            "Akasha model identifier used for semantic chunking and question generation. "
            f"Default: {DEFAULT_DATASET_MODEL}."
        ),
    )
    dataset.add_argument(
        "--max-input-tokens",
        type=_positive_int,
        metavar="N",
        help=(
            "Override the model profile's input-token budget for the built-in Akasha "
            "provider. Default: selected model profile."
        ),
    )
    dataset.add_argument(
        "--max-output-tokens",
        type=_positive_int,
        metavar="N",
        help=(
            "Override the model profile's output-token budget. Auto chunking also uses "
            "this value to size semantic windows. Default: selected model profile."
        ),
    )
    dataset.add_argument(
        "--auto-window-ratio",
        type=_window_ratio,
        metavar="RATIO",
        help=(
            "Fraction of max output tokens used as the approximate auto-window character "
            "budget (0 < value <= 1). Default: selected model profile (currently 0.8)."
        ),
    )
    dataset.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Show timestamped, colored effective configuration, progress, elapsed time, "
            "and best-effort ETA on stderr. Use --no-verbose to disable it. Default: enabled."
        ),
    )
    dataset.add_argument(
        "--output",
        metavar="PATH",
        help=(
            "Destination JSONL file. Existing files are never overwritten. "
            "With --convert-from, destination is a new directory. If omitted, "
            "creates a collision-safe name in the current directory."
        ),
    )
    dataset.add_argument(
        "--trace",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Save complete model prompts, raw responses, parsed JSON, and validation "
            "results below --trace-root. Contains sensitive content. Default: disabled."
        ),
    )
    dataset.add_argument(
        "--trace-console",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Also print complete traced prompts and raw responses to stderr. Requires --trace."
        ),
    )
    dataset.add_argument(
        "--trace-root",
        default=".lladar/runs",
        metavar="PATH",
        help="Parent directory for collision-free model trace runs. Default: .lladar/runs.",
    )
    dataset.add_argument(
        "--env-file",
        default=".env",
        metavar="PATH",
        help="Environment file used by Akasha for provider credentials. Default: .env.",
    )
    dataset.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Fail when semantic chunking remains invalid after retries instead of "
            "falling back to fixed chunks."
        ),
    )
    dataset.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Reuse semantic chunks and generated groups from the cache, and save new "
            "successful results."
        ),
    )
    dataset.add_argument(
        "--cache-dir",
        default=".lladar/cache",
        metavar="PATH",
        help="Directory for semantic-segment and generated-group cache files. Default: .lladar/cache.",
    )
    dataset.add_argument(
        "--refresh-cache",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Regenerate entries even when cache files exist; refreshed results are saved "
            "when --cache is enabled."
        ),
    )
    evaluation = commands.add_parser(
        "eval",
        help="Evaluate schema-v2 agent answers with the LLaDAR BFS protocol.",
        description="Join schema-v2 observed answers by stable case ID and write a differential report.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("dataset", nargs="?", metavar="DATASET", help="Schema-v2 test dataset JSONL or schema-v3 benchmark bundle.")
    evaluation.add_argument("answers", nargs="?", metavar="ANSWERS", help="Schema-v2 observed-answer JSONL or schema-v3 benchmark answers.")
    evaluation.add_argument("--from", dest="from_source", metavar="SOURCE",
                            help="Rediscover scoring from the pinned benchmark source.")
    evaluation.add_argument("--run", metavar="PATH", help="Explicit benchmark run record for --from.")
    evaluation.add_argument(
        "--prompt",
        default=DEFAULT_EVALUATION_PROMPT,
        help="Optional additional judge guidance; core protocol rules cannot be overridden.",
    )
    evaluation.add_argument("--output", default="evaluation-report.json", metavar="PATH")
    evaluation.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL")
    evaluation.add_argument("--env-file", default=".env", metavar="PATH")
    evaluation.add_argument("--strict", action="store_true", help="Fail on alignment or judge errors.")
    evaluation.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing report artifacts.",
    )
    evaluation.add_argument(
        "--include-raw-answers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include answer text in the report. Default: enabled.",
    )
    reporting = commands.add_parser(
        "report", help="Write an evidence-based Markdown evaluation report.",
        formatter_class=_HelpFormatter,
    )
    reporting.add_argument("eval_output", metavar="EVAL_OUTPUT",
                           help="Evaluation JSON or an eval --from output directory.")
    reporting.add_argument("--output", required=True, metavar="PATH",
                           help="Markdown report path.")
    reporting.add_argument("--model", default=DEFAULT_REPORT_MODEL, metavar="MODEL",
                           help="Akasha report writing model.")
    reporting.add_argument("--env-file", default=".env", metavar="PATH")
    reporting.add_argument("--force", action="store_true",
                           help="Replace an existing Markdown report and sidecar.")
    runner = commands.add_parser(
        "run-agent",
        help="Run a project agent against a schema-v2 LLaDAR test dataset.",
        description=(
            "Discover project input/output with a coding agent, verify a generated adapter, "
            "and produce id-keyed answers. Only DATASET is required when run from "
            "the target project directory. Supply --entrypoint for the legacy explicit mode."
        ),
        formatter_class=_HelpFormatter,
    )
    runner.add_argument("dataset", metavar="DATASET", help="Schema-v2 LLaDAR test dataset JSONL.")
    runner.add_argument("--project", default=".", metavar="PATH", help="Project directory to copy. Default: current directory (.).")
    runner.add_argument(
        "--entrypoint",
        metavar="PATH",
        help="Optional explicit Python entrypoint. Default: automatic adapter discovery.",
    )
    runner.add_argument("--output", default="qa-results.jsonl", metavar="PATH", help="Answer JSONL path. Default: qa-results.jsonl.")
    runner.add_argument("--max-cases", type=int, metavar="N", help="Run at most N ready benchmark cases in source order.")
    runner.add_argument("--model", default=DEFAULT_MODEL, metavar="MODEL", help=f"Adapter discovery model. Default: {DEFAULT_MODEL}.")
    runner.add_argument("--env-file", default=".env", metavar="PATH", help="Credential file. Default: .env.")
    runner.add_argument("--target-python", metavar="PATH", help="Separate target interpreter. Default: project .venv; never LLaDAR's environment.")
    runner.add_argument("--timeout", type=float, default=120, help="Seconds allowed per adapter/target execution. Default: 120.")
    runner.add_argument("--max-tool-calls", type=int, default=100, help="Automatic discovery tool-call budget. Default: 100.")
    runner.add_argument("--intent", default="", help="Public feature to test, in ordinary language. Default: no additional intent.")
    runner.add_argument("--graphify", action=argparse.BooleanOptionalAction, default=True,
                        help="Use an optional Graphify code graph first, with source fallback on failure. Default: enabled.")
    runner.add_argument("--graphify-python", metavar="PATH", help="Independent Graphify interpreter. Default: existing uv tool installation.")
    runner.add_argument("--service-url", metavar="URL", help="Explicit existing test service URL; never start/stop that service. Default: none.")
    runner.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=None,
                        help="Ask about ambiguous interfaces. Default: detect an interactive terminal.")
    runner.add_argument("--force", action="store_true", help="Allow overwriting an existing answer file. Default: disabled.")
    runner.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show timestamped run progress and errors on stderr. Default: enabled.",
    )
    resume = commands.add_parser("resume-agent", help="Resume a run waiting for public-interface confirmation.")
    resume.add_argument("run", metavar="RUN", help="Saved .lladar/runs/<run> directory.")
    decision = resume.add_mutually_exclusive_group()
    decision.add_argument("--candidate", metavar="ID", help="Select an evidenced public interface from the saved proposal.")
    decision.add_argument("--clarification", metavar="TEXT", help="Add intent and redo read-only discovery.")
    resume.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=None)
    resume.add_argument("--env-file", default=None, metavar="PATH", help="Optional credential file override; otherwise reuse saved path.")
    resume.add_argument("--service-url", metavar="URL", help="Supply an existing test service and rediscover its contract.")
    resume.add_argument("--force", action="store_true", help="Explicitly allow replacing an existing answer file.")
    resume.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)
    skill = commands.add_parser(
        "skill",
        help="Install and manage the LLaDAR agent-evaluation skill.",
        description="Install the project-local LLaDAR agent-evaluation skill for an agent platform.",
        formatter_class=_HelpFormatter,
    )
    skill_commands = skill.add_subparsers(dest="skill_command", required=True, metavar="ACTION")
    skill_install = skill_commands.add_parser("install", help="Install the skill into project-local platform directories.")
    skill_install.add_argument("--target", required=True, choices=("codex", "claude", "antigravity", "all"))
    skill_install.add_argument("--force", action="store_true", help="Replace modified installed files.")
    skill_update = skill_commands.add_parser("update", help="Update an installed skill.")
    skill_update.add_argument("--target", required=True, choices=("codex", "claude", "antigravity", "all"))
    skill_update.add_argument("--force", action="store_true", help="Replace modified installed files.")
    skill_list = skill_commands.add_parser("list", help="List installed project-local skill targets.")
    skill_uninstall = skill_commands.add_parser("uninstall", help="Remove an installed skill.")
    skill_uninstall.add_argument("--target", required=True, choices=("codex", "claude", "antigravity", "all"))
    skill_uninstall.add_argument("--force", action="store_true", help="Remove modified installed files.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: LLMProvider | None = None,
    adapter_controller: AdapterController | None = None,
    runs_root: str | Path | None = None,
) -> int:
    # Use the same Unicode encoding for interactive prompts and redirected logs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(raw_argv)
    reserved_default_output: Path | None = None
    dataset_output: Path | None = None
    try:
        if args.command == "create" and args.create_command == "config":
            config_path = Path(args.output)
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                template = _CONFIG_TEMPLATE.format(schema_version=artifact_version())
                validate_artifact("TestDatasetConfig", tomllib.loads(template))
                mode = "w" if args.force else "x"
                with config_path.open(mode, encoding="utf-8") as destination:
                    destination.write(template)
            except FileExistsError as error:
                raise LladarError(f"config already exists: {config_path}") from error
            print(f"Created config template at {config_path}")
            return 0
        if args.command == "create" and args.create_command == "dataset":
            from .benchmark_import import import_benchmark

            bundle = import_benchmark(args.convert_from, args.output,
                                      model=args.model, env_file=args.env_file)
            print(f"Created benchmark dataset at {bundle}")
            return 0
        if (
            args.command == "create"
            and args.create_command == "test-dataset"
            and args.convert_from is not None
        ):
            incompatible = ["--config"] if args.config is not None else []
            for key, flags in _DATASET_OPTION_FLAGS.items():
                if key not in {"model", "output", "env_file"}:
                    incompatible.extend(
                        flag for flag in flags
                        if _option_is_explicit(raw_argv, (flag,))
                    )
            if incompatible:
                raise LladarError(
                    "--convert-from cannot be combined with " + ", ".join(incompatible)
                )
            from .benchmark_import import import_benchmark

            bundle = import_benchmark(
                args.convert_from, args.output, model=args.model, env_file=args.env_file
            )
            print(f"Created benchmark dataset at {bundle}")
            return 0
        if args.command == "skill":
            if args.skill_command in ("install", "update"):
                destinations = install_skill(args.target, force=args.force)
                action = "Installed" if args.skill_command == "install" else "Updated"
                for destination in destinations:
                    print(f"{action} {destination}")
            elif args.skill_command == "list":
                entries = list_skills()
                if not entries:
                    print("No LLaDAR skills installed.")
                for entry in entries:
                    print(f"{entry['target']}: {entry['path']} (version={entry['package_version'] or 'unknown'})")
            else:
                destinations = uninstall_skill(args.target, force=args.force)
                for destination in destinations:
                    print(f"Uninstalled {destination}")
            return 0
        if args.command == "report":
            destination = create_report(args.eval_output, args.output,
                                        model=args.model, env_file=args.env_file,
                                        force=args.force)
            print(f"Created evaluation report at {destination}")
            return 0
        if args.command == "eval" and args.from_source:
            from .benchmark_eval import find_matching_run
            from .benchmark_rediscovery import rediscover_and_evaluate

            if args.dataset is not None or args.answers is not None:
                raise LladarError("eval --from selects its dataset and answers from the run record")
            run = find_matching_run(args.from_source, args.run)
            destination = None if args.output == "evaluation-report.json" else args.output
            bundle = rediscover_and_evaluate(
                run, output=destination, model=args.model, env_file=args.env_file,
                strict=args.strict, include_raw_answers=args.include_raw_answers,
            )
            print(f"Evaluated benchmark run at {bundle}")
            return 0
        if args.command == "eval" and (args.dataset is None or args.answers is None):
            raise LladarError("eval requires DATASET and ANSWERS unless --from is supplied")
        if args.command == "eval" and args.dataset and Path(args.dataset).is_dir():
            from .benchmark_eval import evaluate_benchmark
            report = evaluate_benchmark(
                args.dataset, args.answers, output=args.output, force=args.force,
                strict=args.strict, include_raw_answers=args.include_raw_answers,
                judge_model=args.model, env_file=args.env_file,
            )
            print(f"Evaluated {report['summary']['scored']} benchmark case(s) at {args.output}")
            return 0
        if args.command == "eval":
            report = evaluate(
                args.dataset,
                args.answers,
                prompt=args.prompt,
                output=args.output,
                model=args.model,
                env_file=args.env_file,
                provider=provider,
                strict=args.strict,
                include_raw_answers=args.include_raw_answers,
                force=args.force,
            )
            print(
                f"Evaluated {report['summary']['scheduled_comparisons']} comparison(s) "
                f"at {args.output}"
            )
            return 0
        if args.command == "resume-agent":
            from .run_context import load_context

            if args.service_url and args.candidate:
                raise ValueError('--service-url changes the contract; use clarification instead of --candidate')
            saved = load_context(args.run)
            completed = run_agent(
                saved["dataset"], saved["output"], project=saved["project"],
                model=saved["model"], target_python=saved["target_python"],
                env_file=args.env_file if args.env_file is not None else saved["env_file"],
                timeout=saved["timeout"], max_tool_calls=saved["max_tool_calls"],
                intent=saved["intent"], interactive=args.interactive,
                graphify=saved.get("graphify", True), graphify_python=saved.get("graphify_python"),
                service_url=args.service_url or saved.get('service_url'),
                resume_run=args.run, candidate_id=args.candidate,
                clarification=args.clarification or ('Use the explicitly supplied existing test service.' if args.service_url else None),
                force=args.force, verbose=args.verbose,
            )
            print(f"Answered {completed} session(s) at {saved['output']}")
            return 0
        if args.command == "run-agent":
            completed = run_agent(
                args.dataset,
                args.output,
                project=args.project,
                entrypoint=args.entrypoint,
                adapter=adapter_controller
                or AkashaAdapterController(model=args.model, env_file=args.env_file),
                env_file=args.env_file,
                force=args.force,
                verbose=args.verbose,
                runs_root=runs_root,
                model=args.model,
                target_python=args.target_python,
                timeout=args.timeout,
                max_tool_calls=args.max_tool_calls,
                max_cases=args.max_cases,
                interactive=args.interactive,
                intent=args.intent,
                graphify=args.graphify, graphify_python=args.graphify_python,
                service_url=args.service_url,
            )
            print(f"Answered {completed} session(s) at {args.output}")
            return 0
        config_settings: dict[str, object] = {}
        if args.config is not None:
            config_settings = load_test_dataset_config(args.config)
            _merge_dataset_config(args, config_settings, raw_argv)
            _warn_about_overrides(
                args.config,
                _overridden_config_keys(args, config_settings, raw_argv),
            )
            ProgressReporter(enabled=args.verbose).configuration(
                {"config": Path(args.config).resolve()}
            )
        if args.trace_console and not args.trace:
            raise LladarError("--trace-console requires --trace")
        if args.knowledge is None:
            raise LladarError("test-dataset requires knowledge from --knowledge or --config")
        if args.config is not None:
            validate_test_dataset_input_paths(args.knowledge, args.prompt_file)
        if args.output is None:
            reserved_default_output = _reserve_default_dataset_output()
            dataset_output = reserved_default_output
        else:
            dataset_output = Path(args.output)
        dataset = create_test_dataset(
            knowledge=[Path(value) for value in args.knowledge],
            prompt=args.prompt,
            prompt_file=args.prompt_file,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            count=args.count,
            seed=args.seed,
            policies=args.policies,
            model=args.model,
            output=dataset_output,
            provider=provider,
            env_file=args.env_file,
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
            auto_window_ratio=args.auto_window_ratio,
            strict=args.strict,
            force=reserved_default_output is not None,
            cache=args.cache,
            cache_dir=args.cache_dir,
            refresh_cache=args.refresh_cache,
            verbose=args.verbose,
            trace=args.trace,
            trace_console=args.trace_console,
            trace_root=args.trace_root,
        )
    except NeedsConfirmation as error:
        print(str(error), file=sys.stderr)
        return 3
    except SkillError as error:
        _discard_empty_reservation(reserved_default_output)
        print(f"lladar: {error}", file=sys.stderr)
        return 2
    except ProviderError:
        _discard_empty_reservation(reserved_default_output)
        print("lladar: provider generation failed", file=sys.stderr)
        return 2
    except (LladarError, FileExistsError, OSError, ValueError, RuntimeError) as error:
        _discard_empty_reservation(reserved_default_output)
        print(f"lladar: {error}", file=sys.stderr)
        return 2
    print(f"Generated {len(dataset)} dataset item(s) at {dataset_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
