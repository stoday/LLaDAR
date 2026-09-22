from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .api import DEFAULT_DATASET_MODEL, create_test_dataset
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .interfaces import NeedsConfirmation
from .providers import LLMProvider
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .runner import AdapterController, AkashaAdapterController, run_agent


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    def _get_help_string(self, action: argparse.Action) -> str:
        if action.default is None and action.help is not None:
            return action.help
        return super()._get_help_string(action)


_DEFAULT_DATASET_OUTPUT_DISPLAY = "./test-dataset-YYYYMMDD-HHMMSS/dataset.jsonl"


def _default_dataset_output() -> Path:
    """Choose a new timestamped directory without colliding with an existing run."""
    stem = datetime.now().astimezone().strftime("test-dataset-%Y%m%d-%H%M%S")
    directory = Path(stem)
    suffix = 1
    while directory.exists():
        directory = Path(f"{stem}-{suffix}")
        suffix += 1
    return directory / "dataset.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lladar",
        description="Generate questions, run a target Agent, evaluate responses, and report results.",
        formatter_class=_HelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    create = commands.add_parser("create", help="Create an artifact.", formatter_class=_HelpFormatter)
    create_commands = create.add_subparsers(dest="create_command", required=True, metavar="ARTIFACT")
    dataset = create_commands.add_parser(
        "test-dataset",
        help="Generate question and expected-answer records from knowledge.",
        formatter_class=_HelpFormatter,
    )
    dataset.add_argument("--knowledge", action="append", required=True, metavar="PATH",
                         help="Knowledge file or directory; repeat for multiple inputs.")
    dataset.add_argument("--output", metavar="PATH",
                         help=f"Destination JSONL file (default: {_DEFAULT_DATASET_OUTPUT_DISPLAY}).")
    dataset.add_argument("--count", type=int, default=0, metavar="N", help="Maximum records; zero means all chunks.")
    dataset.add_argument("--chunk-size", type=_chunk_size, default="auto", metavar="AUTO|N",
                         help="Chunk size, or automatic semantic chunking.")
    dataset.add_argument("--overlap", type=float, default=0.1,
                         help="Overlap ratio for fixed-size chunks.")
    dataset.add_argument("--seed", type=int, default=0,
                         help="Deterministic candidate shuffle seed.")
    guidance = dataset.add_mutually_exclusive_group()
    guidance.add_argument("--prompt", metavar="TEXT", help="Additional generation guidance (default: built-in prompt).")
    guidance.add_argument("--prompt-file", metavar="PATH", help="Read additional generation guidance from a file (default: none).")
    dataset.add_argument("--model", default=DEFAULT_DATASET_MODEL, metavar="MODEL",
                         help="Model used for chunking and question generation.")
    dataset.add_argument("--env-file", default=".env", metavar="PATH",
                         help="Environment file supplied to the model provider.")
    dataset.add_argument("--temperature", type=float, default=0.0,
                         help="Model sampling temperature.")
    dataset.add_argument("--max-input-tokens", type=int, metavar="N",
                         help="Override the selected model profile input budget (default: selected model profile).")
    dataset.add_argument("--max-output-tokens", type=int, metavar="N",
                         help="Override the selected model profile output budget (default: selected model profile).")
    dataset.add_argument("--auto-window-ratio", type=float, metavar="RATIO",
                         help="Override the selected model profile automatic-window ratio (default: selected model profile).")
    dataset.add_argument("--strict", action="store_true",
                         help="Fail instead of falling back when semantic chunking fails.")
    dataset.add_argument("--force", action="store_true",
                         help="Allow replacement of an existing output file.")
    dataset.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True,
                         help="Show LLaDAR progress and the dataset-generation Akasha trace.")

    runner = commands.add_parser(
        "run-agent",
        help="Fill actual_response by running a target Agent.",
        formatter_class=_HelpFormatter,
    )
    runner.add_argument("dataset", metavar="DATASET")
    runner.add_argument("--project", default=".", metavar="PATH")
    runner.add_argument("--entrypoint", metavar="PATH")
    runner.add_argument("--output", default="responses.jsonl", metavar="PATH")
    runner.add_argument("--max-cases", type=int, metavar="N")
    runner.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL")
    runner.add_argument("--env-file", default=".env", metavar="PATH")
    runner.add_argument("--target-python", metavar="PATH")
    runner.add_argument("--timeout", type=float, default=120)
    runner.add_argument("--max-tool-calls", type=int, default=100)
    runner.add_argument("--intent", default="")
    runner.add_argument("--graphify", action=argparse.BooleanOptionalAction, default=True)
    runner.add_argument("--graphify-python", metavar="PATH")
    runner.add_argument("--service-url", metavar="URL")
    runner.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=None)
    runner.add_argument("--force", action="store_true")
    runner.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True)

    evaluation = commands.add_parser(
        "eval",
        help="Plan and run an Agent-assisted evaluation.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("responses", metavar="RESPONSES")
    evaluation.add_argument("--output", default="evaluation.json", metavar="PATH")
    eval_guidance = evaluation.add_mutually_exclusive_group()
    eval_guidance.add_argument("--prompt", metavar="TEXT")
    eval_guidance.add_argument("--prompt-file", metavar="PATH")
    evaluation.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL")
    evaluation.add_argument("--env-file", default=".env", metavar="PATH")
    evaluation.add_argument("--strict", action="store_true")
    evaluation.add_argument("--force", action="store_true")

    report = commands.add_parser(
        "report",
        help="Render an evidence-bounded Markdown report.",
        formatter_class=_HelpFormatter,
    )
    report.add_argument("eval_output", metavar="EVALUATION")
    report.add_argument("--output", default="report.md", metavar="PATH")
    report.add_argument("--model", default=DEFAULT_REPORT_MODEL, metavar="MODEL")
    report.add_argument("--env-file", default=".env", metavar="PATH")
    report.add_argument("--force", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: LLMProvider | None = None,
    adapter_controller: AdapterController | None = None,
    runs_root: str | Path | None = None,
) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "create":
            output = Path(args.output) if args.output else _default_dataset_output()
            records = create_test_dataset(
                [Path(path) for path in args.knowledge],
                output=output,
                prompt=args.prompt,
                prompt_file=args.prompt_file,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
                count=args.count,
                seed=args.seed,
                model=args.model,
                provider=provider,
                env_file=args.env_file,
                temperature=args.temperature,
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=args.max_output_tokens,
                auto_window_ratio=args.auto_window_ratio,
                strict=args.strict,
                force=args.force,
                verbose=args.verbose,
            )
            print(f"Generated {len(records)} record(s) at {output}")
            return 0
        if args.command == "run-agent":
            completed = run_agent(
                args.dataset,
                args.output,
                project=args.project,
                entrypoint=args.entrypoint,
                adapter=adapter_controller or AkashaAdapterController(
                    model=args.model, env_file=args.env_file
                ),
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
                graphify=args.graphify,
                graphify_python=args.graphify_python,
                service_url=args.service_url,
            )
            print(f"Answered {completed} record(s) at {args.output}")
            return 0
        if args.command == "eval":
            result = evaluate(
                args.responses,
                output=args.output,
                prompt=args.prompt,
                prompt_file=args.prompt_file,
                model=args.model,
                env_file=args.env_file,
                provider=provider,
                strict=args.strict,
                force=args.force,
            )
            print(f"Evaluated {result['summary']['evaluated']} record(s) at {args.output}")
            return 0
        destination = create_report(
            args.eval_output,
            args.output,
            model=args.model,
            env_file=args.env_file,
            provider=provider,
            force=args.force,
        )
        print(f"Created evaluation report at {destination}")
        return 0
    except NeedsConfirmation as error:
        print(f"lladar: {error}", file=sys.stderr)
        return 3
    except ProviderError:
        print("lladar: provider generation failed", file=sys.stderr)
        return 2
    except (LladarError, FileExistsError, OSError, ValueError, RuntimeError) as error:
        print(f"lladar: {error}", file=sys.stderr)
        return 2


def _chunk_size(value: str) -> int | str:
    if value == "auto":
        return value
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("chunk size must be 'auto' or a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("chunk size must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
