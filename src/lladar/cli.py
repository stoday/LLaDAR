from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Any
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .api import DEFAULT_DATASET_MODEL, create_test_dataset
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .interfaces import NeedsConfirmation
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .runner import run_agent


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
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
    path = Path(destination)
    if path.is_file() or path.suffix.lower() == ".jsonl":
        return path
    path.mkdir(parents=True, exist_ok=True)
    return _timestamped_dataset_output(path)


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
    dataset.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                         help="Use a local method directory containing SKILL.md instead of bundled knowledge-point-qa; only one is supported.")
    dataset.add_argument("--output", default=".", metavar="DIRECTORY",
                         help=f"Directory for the generated dataset; the CLI chooses {_DATASET_FILENAME_DISPLAY} (default: {_DEFAULT_DATASET_OUTPUT_DISPLAY}).")
    dataset.add_argument("--count", type=int, default=0, metavar="N", help="Maximum deduplicated records; zero means all candidates.")
    dataset.add_argument("--seed", type=int, default=0,
                         help="Deterministic candidate shuffle seed.")
    dataset.add_argument("--model", default=DEFAULT_DATASET_MODEL, metavar="MODEL",
                         help="Model used for dataset generation.")
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
    dataset.add_argument("--force", action="store_true",
                         help="Allow replacement of an existing output file.")
    dataset.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=True,
                         help="Show LLaDAR progress and the dataset-generation Akasha trace.")

    runner = commands.add_parser(
        "run-agent",
        help="Fill actual_response by running a target Agent.",
        description=(
            "Run a target Agent through an inspected local project or a calibrated "
            "question website, then fill actual_response in a new JSONL file."
        ),
        epilog=(
            "Browser setup: python -m playwright install chromium. --page-url always uses the guided terminal workflow; "
            "omit --interactive and --no-interactive (project mode only). "
            "Browser mode requires terminal stdin and stderr; without them it stops before opening the browser. With --page-url, "
            "sign in yourself in the isolated browser, submit the exact calibration question "
            "shown in the terminal, and wait for the site's final answer before pressing Enter. "
            "Review the website requests, model destination, real response content transfer and budgets together. "
            "One YES approves BOTH website requests and model transfer; the two approval flags still grant only their named scopes. "
            "Then type MATCH after independent verification in the color terminal review (stderr), not an HTML tab. "
            "Approval flags do not skip review. NO_COLOR or TERM=dumb disables color; redirected review output is refused. "
            "The full verification response appears in terminal scrollback; no review file is created. "
            "Browser sign-in and capture are still required. "
            "At most N+2 model calls for N scheduled trials, each capped at 8192 output tokens, share 60 minutes. "
            "Input is capped at 120 KiB including JSON framing; no truncation, retries or parser cache. "
            "On HTTP 401/403, the run stops; sign in again before starting a newly approved run. "
            "Final answers are saved in responses and trials; cookies, tokens and raw streams are not. "
            "Each captured response is limited to 1 MiB of decoded bytes; --timeout does not raise this limit. "
            "Browser startup and page navigation separately allow 300 seconds (5 minutes); --timeout controls website requests, not navigation. "
            "The local session stays in .lladar/browser-profiles. "
            "--service-url does not discover an arbitrary API; it is for an inspected project."
        ),
        formatter_class=_HelpFormatter,
    )
    runner.add_argument(
        "dataset", metavar="DATASET",
        help="Input test-dataset JSONL whose actual_response values are null.",
    )
    target = runner.add_mutually_exclusive_group()
    target.add_argument(
        "--project", default=None, metavar="PATH",
        help="Target Agent project directory to inspect and copy into the isolated run workspace (default: current directory).",
    )
    target.add_argument(
        "--page-url", metavar="URL",
        help="Question website page opened in a visible isolated browser for manual sign-in, one calibration submission, and replay.",
    )
    runner.add_argument(
        "--output", default="responses.jsonl", metavar="PATH",
        help="Destination responses JSONL; also writes PATH.trials.jsonl and PATH.run.json beside it.",
    )
    runner.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Use a local method directory containing SKILL.md; otherwise use bundled run-agent-stability.")
    runner.add_argument("--seed", type=int, default=0,
                        help="Deterministic random selection seed.")
    runner.add_argument(
        "--model", default=None, metavar="MODEL",
        help="Project coding model (default: gemini:gemini-2.5-flash), or browser answer extraction model (default: gemini:gemini-3.8-flash; Gemini API only).",
    )
    runner.add_argument(
        "--env-file", default=".env", metavar="PATH",
        help="Environment file for the coding model provider and target process; existing environment variables win.",
    )
    runner.add_argument(
        "--target-python", metavar="PATH",
        help="Target project's Python executable (default: discover PROJECT/.venv automatically).",
    )
    runner.add_argument(
        "--timeout", type=float, default=3600, metavar="SECONDS",
        help="Timeout for each adapter, tool subprocess, or browser request (default: 3600 seconds / 60 minutes).",
    )
    runner.add_argument(
        "--max-tool-calls", type=int, default=100, metavar="N",
        help="Maximum tool calls per discovery or adapter-repair agent turn.",
    )
    runner.add_argument(
        "--graphify", action=argparse.BooleanOptionalAction, default=True,
        help="Build an optional static code graph before source exploration; use --no-graphify to disable (default: enabled).",
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
        help="Preapprove only the browser verification and scheduled dataset requests. Model-transfer approval is still required; neither flag skips MATCH review.",
    )
    runner.add_argument(
        "--fresh-browser-profile", action="store_true",
        help="Use a temporary signed-out browser profile for this run instead of the reusable site profile.",
    )
    runner.add_argument(
        "--allow-response-model-transfer", action="store_true",
        help="Approve real captured response content transfer to --model for this run only; N+2 calls for N trials, 8192 output tokens per call, shared 60 minutes. Does not approve website requests or skip local verification.",
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
        help="Show LLaDAR progress and coding-agent traces; use --no-verbose to hide them (default: enabled).",
    )

    evaluation = commands.add_parser(
        "eval",
        help="Plan and run an Agent-assisted evaluation.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("responses", metavar="RESPONSES")
    evaluation.add_argument("--output", default="evaluation.json", metavar="PATH")
    evaluation.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                            help="Use a local method directory containing SKILL.md; otherwise use bundled eval-answer-verdict.")
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
    report.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Use a local method directory containing SKILL.md; otherwise use bundled report-evidence-summary.")
    report.add_argument("--model", default=DEFAULT_REPORT_MODEL, metavar="MODEL")
    report.add_argument("--env-file", default=".env", metavar="PATH")
    report.add_argument("--force", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runs_root: str | Path | None = None,
    skill_agent_factory: Callable[..., Any] | None = None,
    browser_target_factory: Callable[..., Any] | None = None,
    extraction_provider_factory: Callable[..., Any] | None = None,
) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run-agent" and args.page_url:
        if args.interactive is not None:
            parser.error("--interactive and --no-interactive are project mode only; omit both with --page-url.")
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            parser.error(
                "--page-url requires terminal stdin and stderr for sign-in, calibration, consent and MATCH review. "
                "Run directly in a terminal without piping input or redirecting stderr; no interaction flag is needed."
            )
        args.interactive = True
    try:
        if args.command == "create":
            output = _resolve_dataset_output(args.output)
            records = create_test_dataset(
                [Path(path) for path in args.knowledge],
                output=output,
                count=args.count,
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
            status = f" (status={metadata['status']}; provenance={output}.generation.json)"
            print(f"Generated {len(records)} record(s) at {output}{status}")
            return 0
        if args.command == "run-agent":
            project = args.project if args.project is not None else (None if args.page_url else ".")
            completed = run_agent(
                args.dataset,
                args.output,
                project=project,
                page_url=args.page_url,
                env_file=args.env_file,
                force=args.force,
                verbose=args.verbose,
                runs_root=runs_root,
                model=args.model,
                target_python=args.target_python,
                timeout=args.timeout,
                max_tool_calls=args.max_tool_calls,
                skill=args.skill,
                seed=args.seed,
                interactive=args.interactive,
                graphify=args.graphify,
                graphify_python=args.graphify_python,
                service_url=args.service_url,
                confirm_browser_run=args.confirm_browser_run,
                fresh_browser_profile=args.fresh_browser_profile,
                browser_target_factory=browser_target_factory,
                extraction_provider_factory=extraction_provider_factory,
                allow_response_model_transfer=args.allow_response_model_transfer,
                strategy_agent_factory=skill_agent_factory,
            )
            print(f"Answered {completed} record(s) at {args.output}")
            return 0
        if args.command == "eval":
            result = evaluate(
                args.responses,
                output=args.output,
                skill=args.skill,
                model=args.model,
                env_file=args.env_file,
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
            skill=args.skill,
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
    except KeyboardInterrupt:
        print("lladar: Cancelled. Completed browser answers, if any, were preserved.", file=sys.stderr)
        return 130
    except Exception as error:
        if args.command == "run-agent" and args.page_url:
            from .browser_target import browser_error_message

            message = browser_error_message(error)
        elif isinstance(error, (LladarError, FileExistsError, OSError, ValueError, RuntimeError)):
            message = str(error)
        else:
            raise
        print(f"lladar: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
