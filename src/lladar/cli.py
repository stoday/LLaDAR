from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .api import DEFAULT_MODEL, create_test_dataset
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .providers import LLMProvider
from .skill import SkillError, install_skill, list_skills, uninstall_skill


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

class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32, width=100)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lladar",
        description="Generate datasets for detecting unsupported assumptions in LLM agents.",
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
    dataset = create_commands.add_parser(
        "test-dataset",
        help="Generate a contrastive unsupported-assumption test dataset.",
        description=(
            "Generate complete and underspecified question pairs from .txt and .md "
            "knowledge sources."
        ),
        epilog="""Examples:
  lladar create test-dataset --knowledge ./knowledge
  lladar create test-dataset --knowledge guide.md --chunk-size auto --strict
  lladar create test-dataset --knowledge ./knowledge --format json --output dataset.json
  lladar create test-dataset --knowledge guide.md --chunk-size auto --max-output-tokens 32768""",
        formatter_class=_HelpFormatter,
    )
    dataset.add_argument(
        "--knowledge",
        nargs="+",
        required=True,
        metavar="PATH",
        help=(
            "Files or directories containing knowledge documents. Directories are "
            "searched recursively for .txt and .md files; multiple paths are allowed."
        ),
    )
    strategy = dataset.add_mutually_exclusive_group()
    strategy.add_argument(
        "--prompt",
        metavar="STRATEGY_OR_TEXT",
        help=(
            "Built-in strategy name or custom generation instructions. "
            "Defaults to the built-in 'ambiguity' strategy."
        ),
    )
    strategy.add_argument(
        "--prompt-file",
        metavar="PATH",
        help=(
            "UTF-8 file containing custom generation instructions. "
            "Cannot be combined with --prompt."
        ),
    )
    dataset.add_argument(
        "--chunk-size",
        type=_parse_chunk_size,
        default=2000,
        metavar="N|auto",
        help=(
            "Positive character count for fixed chunks, or 'auto' for Semantic "
            "chunking with the language model. Default: 2000."
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
        "--num-pairs",
        type=int,
        default=1,
        metavar="N",
        help="Number of question pairs generated per chunk. Default: 1.",
    )
    dataset.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=(
            "Akasha model identifier used for semantic chunking and question generation. "
            f"Default: {DEFAULT_MODEL}."
        ),
    )
    dataset.add_argument(
        "--max-input-tokens",
        type=_positive_int,
        metavar="N",
        help=(
            "Override the model profile's input-token budget for the built-in Akasha "
            "provider. Default: selected model profile (Gemini 2.5 Flash: 1,048,576)."
        ),
    )
    dataset.add_argument(
        "--max-output-tokens",
        type=_positive_int,
        metavar="N",
        help=(
            "Override the model profile's output-token budget. Auto chunking also uses "
            "this value to size semantic windows. Default: selected model profile (Gemini 2.5 Flash: 65,536)."
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
        default="test-dataset.jsonl",
        metavar="PATH",
        help=(
            "Destination dataset file. Protects an existing output file unless --force "
            "is used. Default: test-dataset.jsonl."
        ),
    )
    dataset.add_argument(
        "--format",
        choices=("jsonl", "json"),
        default="jsonl",
        help=(
            "Output format: JSONL writes one object per line; JSON writes one array. "
            "Default: jsonl."
        ),
    )
    dataset.add_argument(
        "--env-file",
        default=".env",
        metavar="PATH",
        help="Environment file used by Akasha for provider credentials. Default: .env.",
    )
    dataset.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail the run when chunking or generation remains invalid after retries, "
            "instead of skipping or falling back."
        ),
    )
    dataset.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing output file.",
    )
    dataset.add_argument(
        "--cache",
        action="store_true",
        help=(
            "Reuse semantic chunks and generated pairs from the cache, and save new "
            "successful results."
        ),
    )
    dataset.add_argument(
        "--cache-dir",
        default=".lladar/cache",
        metavar="PATH",
        help="Directory for semantic and pair cache files. Default: .lladar/cache.",
    )
    dataset.add_argument(
        "--refresh-cache",
        action="store_true",
        help=(
            "Regenerate entries even when cache files exist; refreshed results are saved "
            "when --cache is enabled."
        ),
    )
    evaluation = commands.add_parser(
        "eval",
        help="Evaluate agent answers and write a report.",
        description="Compare answer JSONL records with a LLaDAR test dataset by id.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("dataset", metavar="DATASET", help="Original test dataset JSONL.")
    evaluation.add_argument("answers", metavar="ANSWERS", help="Agent answer JSONL.")
    evaluation.add_argument("--prompt", required=True, help="Evaluation rubric sent to the judge.")
    evaluation.add_argument("--output", default="evaluation-report.json", metavar="PATH")
    evaluation.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL")
    evaluation.add_argument("--env-file", default=".env", metavar="PATH")
    evaluation.add_argument("--strict", action="store_true", help="Fail on alignment or judge errors.")
    evaluation.add_argument(
        "--include-raw-answers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include answer text in the report. Default: enabled.",
    )
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
) -> int:
    args = build_parser().parse_args(argv)
    try:
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
            )
            print(f"Evaluated {report['summary']['total']} item(s) at {args.output}")
            return 0
        dataset = create_test_dataset(
            knowledge=[Path(value) for value in args.knowledge],
            prompt=args.prompt,
            prompt_file=args.prompt_file,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            num_pairs=args.num_pairs,
            model=args.model,
            output=args.output,
            format=args.format,
            provider=provider,
            env_file=args.env_file,
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
            auto_window_ratio=args.auto_window_ratio,
            strict=args.strict,
            force=args.force,
            cache=args.cache,
            cache_dir=args.cache_dir,
            refresh_cache=args.refresh_cache,
            verbose=args.verbose,
        )
    except SkillError as error:
        print(f"lladar: {error}", file=sys.stderr)
        return 2
    except ProviderError:
        print("lladar: provider generation failed", file=sys.stderr)
        return 2
    except (LladarError, FileExistsError, OSError, ValueError) as error:
        print(f"lladar: {error}", file=sys.stderr)
        return 2
    print(f"Generated {len(dataset)} dataset item(s) at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())