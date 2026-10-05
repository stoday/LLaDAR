from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from typing import Callable, Any
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .api import DEFAULT_DATASET_MODEL, create_test_dataset
from .controlled_variants import normalize_controlled_variant_topics, select_controlled_variant_topics
from .evaluation import DEFAULT_EVALUATION_MODEL, evaluate
from .exceptions import LladarError, ProviderError
from .interfaces import NeedsConfirmation
from .reporting import DEFAULT_REPORT_MODEL, create_report
from .runner import DEFAULT_ADAPTER_MODEL, run_agent
from .skill_templates import create_skill_template
from .situation import create_situation, evaluate_situation, run_situation


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
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND",
        help="Choose a command (required; no default).",
    )

    create = commands.add_parser(
        "create", help="Create a test dataset, situation, or editable skill template.",
        description="Create a test dataset, situation configuration, or editable skill template.",
        formatter_class=_HelpFormatter,
    )
    create_commands = create.add_subparsers(
        dest="create_command", required=True, metavar="ARTIFACT",
        help="Choose the artifact to create (required; no default).",
    )
    for stage in ("test-dataset", "situation", "run-agent", "eval", "report"):
        template = create_commands.add_parser(
            f"{stage}-skill", help=f"Create an editable {stage} skill.",
            formatter_class=_HelpFormatter,
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
        help="Generate question and expected-answer records from knowledge.",
        formatter_class=_HelpFormatter,
    )
    dataset.add_argument("--knowledge", action="append", required=True, metavar="PATH",
                         help="Source knowledge file or directory; repeat for multiple inputs (required; no default).")
    dataset.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                         help="Local method directory containing SKILL.md; only one is supported (default: bundled knowledge-point-qa).")
    dataset.add_argument("--output", default=".", metavar="PATH",
                         help=f"Output directory or explicit .jsonl path. A directory gets a new {_DATASET_FILENAME_DISPLAY} file (default: {_DEFAULT_DATASET_OUTPUT_DISPLAY}).")
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
                         help="Show LLaDAR progress and the dataset-generation Akasha trace; --no-verbose hides them (default: on).")

    situation = create_commands.add_parser(
        "situation", help="Compile a knowledge-optional multi-turn behavior test.",
        formatter_class=_HelpFormatter,
    )
    observe = situation.add_mutually_exclusive_group(required=True)
    observe.add_argument("--observe", metavar="TEXT",
                         help="Behavior to observe, as inline text (required unless --observe-file is given; no default).")
    observe.add_argument("--observe-file", metavar="PATH",
                         help="UTF-8 file containing the behavior to observe (required unless --observe is given; no default).")
    stopping = situation.add_mutually_exclusive_group(required=True)
    stopping.add_argument("--stop-criteria", metavar="TEXT",
                          help="When the conversation should stop, as inline text (required unless --stop-criteria-file is given; no default).")
    stopping.add_argument("--stop-criteria-file", metavar="PATH",
                          help="UTF-8 file containing the stopping criteria (required unless --stop-criteria is given; no default).")
    situation.add_argument("--max-turns", type=int, required=True, metavar="N",
                           help="Maximum target Agent turns per scenario; must be positive (required; no default).")
    situation.add_argument("--knowledge", action="append", default=[], metavar="PATH",
                           help="Optional .md or .txt knowledge file; repeat for multiple files (default: no knowledge files).")
    situation.add_argument("--output", required=True, metavar="PATH",
                           help="Destination situation JSON (required; no default; existing file requires --force).")
    situation.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                           help="Local situation-authoring skill directory containing SKILL.md (default: bundled create-situation).")
    situation.add_argument("--model", default=DEFAULT_DATASET_MODEL, metavar="MODEL",
                           help=f"Model used to compile the situation (default: {DEFAULT_DATASET_MODEL}).")
    situation.add_argument("--env-file", default=".env", metavar="PATH",
                           help="Environment file for the model provider (default: .env).")
    situation.add_argument("--force", action="store_true",
                           help="Replace an existing situation JSON (default: off).")

    runner = commands.add_parser(
        "run-agent",
        help="Fill actual_response by running a target Agent.",
        description=(
            "Run one-question DATASET records through an inspected project or calibrated website, "
            "or use --situation-config for generated multi-turn conversations with a project session driver."
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
        "--output", default="responses.jsonl", metavar="PATH",
        help="Destination responses JSONL (default: responses.jsonl). Single-question mode also writes PATH.trials.jsonl and PATH.run.json; situation mode writes PATH.turns.jsonl, PATH.scenarios.jsonl, PATH.calibration.json and PATH.run.json.",
    )
    runner.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Use a local method directory containing SKILL.md for single-question runs (default: bundled run-agent-stability; unavailable with --situation-config).")
    runner.add_argument("--situation-config", metavar="PATH",
                        help="Use a frozen situation JSON for multi-turn project sessions (default: single-question DATASET mode; cannot combine with DATASET or --skill).")
    runner.add_argument("--num-scenarios", type=int, metavar="N",
                        help="Number of distinct scenarios to generate and run; required with --situation-config, unused otherwise (no default).")
    runner.add_argument("--seed", type=int, default=0,
                        help="Deterministic random selection seed for single-question trials (default: 0).")
    runner.add_argument(
        "--model", default=None, metavar="MODEL",
        help=f"Project coding model (default: {DEFAULT_ADAPTER_MODEL}), or browser answer extraction model (default: gemini:gemini-3.8-flash; Gemini API only).",
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
        help="Build a static code graph before single-question project exploration; --no-graphify disables it (default: enabled in that mode).",
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
        help="Approve real captured response content transfer to --model for this run only; N+2 calls for N trials, 8192 output tokens per call, shared 60 minutes. Website requests and local verification remain separate (default: off).",
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
        help="Show single-question LLaDAR progress and coding-agent traces; use --no-verbose to hide them (default: enabled).",
    )

    evaluation = commands.add_parser(
        "eval",
        help="Judge saved single-question responses or multi-turn situation transcripts.",
        formatter_class=_HelpFormatter,
    )
    evaluation.add_argument("responses", metavar="RESPONSES",
                            help="Responses JSONL from run-agent (required; no default).")
    evaluation.add_argument("--output", default="evaluation.json", metavar="PATH",
                            help="Destination evaluation JSON (default: evaluation.json).")
    evaluation.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                            help="Local single-question evaluation skill directory containing SKILL.md (default: bundled eval-answer-verdict; unavailable with --situation-config).")
    evaluation.add_argument("--situation-config", metavar="PATH",
                            help="Evaluate multi-turn transcripts against this frozen situation JSON (default: single-question evaluation; cannot combine with --skill).")
    evaluation.add_argument("--model", default=DEFAULT_EVALUATION_MODEL, metavar="MODEL",
                            help=f"Model used for evaluation judgments (default: {DEFAULT_EVALUATION_MODEL}).")
    evaluation.add_argument("--env-file", default=".env", metavar="PATH",
                            help="Environment file for the evaluation model provider (default: .env).")
    evaluation.add_argument("--strict", action="store_true",
                            help="Stop on a judgment error instead of recording it and continuing (default: off).")
    evaluation.add_argument("--force", action="store_true",
                            help="Replace an existing evaluation JSON (default: off).")

    report = commands.add_parser(
        "report",
        help="Render an evidence-bounded Markdown report.",
        formatter_class=_HelpFormatter,
    )
    report.add_argument("eval_output", metavar="EVALUATION",
                        help="Evaluation JSON produced by eval (required; no default).")
    report.add_argument("--output", default="report.md", metavar="PATH",
                        help="Destination Markdown report (default: report.md).")
    report.add_argument("--skill", action=_SingleSkill, metavar="DIRECTORY",
                        help="Local report-writing skill directory containing SKILL.md (default: bundled report-evidence-summary for single-question reports; situation reports use built-in rendering unless a skill is supplied).")
    report.add_argument("--model", default=DEFAULT_REPORT_MODEL, metavar="MODEL",
                        help=f"Model used to write report prose when a report skill is invoked (default: {DEFAULT_REPORT_MODEL}).")
    report.add_argument("--env-file", default=".env", metavar="PATH",
                        help="Environment file for the report model provider when invoked (default: .env).")
    report.add_argument("--force", action="store_true",
                        help="Replace an existing Markdown report (default: off).")
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
        if args.command == "create":
            if args.create_command.endswith("-skill"):
                stage = args.create_command.removesuffix("-skill")
                destination = create_skill_template(stage, args.output, force=args.force)
                uses = {
                    "test-dataset": "lladar create test-dataset --knowledge KNOWLEDGE --skill {skill} --output DATASET.jsonl",
                    "situation": "lladar create situation --observe TEXT --stop-criteria TEXT --max-turns 3 --skill {skill} --output situation.json",
                    "run-agent": "lladar run-agent DATASET.jsonl --project PROJECT --skill {skill} --output responses.jsonl",
                    "eval": "lladar eval RESPONSES --skill {skill} --output evaluation.json",
                    "report": "lladar report EVALUATION --skill {skill} --output report.md",
                }
                print(f"Created skill at {destination}")
                print(f"Author guide: {destination / 'AUTHORING.md'}")
                skill_arg = (subprocess.list2cmdline([str(destination)]) if os.name == "nt"
                             else shlex.quote(str(destination)))
                print("Use: " + uses[stage].format(skill=skill_arg))
                return 0
            if args.create_command == "situation":
                observe = args.observe if args.observe is not None else Path(args.observe_file).read_text(encoding="utf-8")
                stop = args.stop_criteria if args.stop_criteria is not None else Path(args.stop_criteria_file).read_text(encoding="utf-8")
                create_situation(
                    observe=observe, stop_criteria=stop, max_turns=args.max_turns,
                    knowledge=args.knowledge, output=args.output, skill=args.skill,
                    model=args.model, env_file=args.env_file,
                    skill_agent_factory=skill_agent_factory, force=args.force,
                )
                print(f"Created situation configuration at {args.output}")
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
                [Path(path) for path in args.knowledge],
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
            graph = json.loads(Path(str(output) + ".graph.json").read_text(encoding="utf-8"))
            concept_count = sum(node.get("type") == "concept" for node in graph.get("nodes", []))
            dimensions = graph.get("control_dimensions", [])
            selected_controls = sum(
                line.get("plan_type") == "controlled_invariance"
                for line in metadata["dataset"]["lines"]
            )
            status = (
                f" (status={metadata['status']}; concepts={concept_count}; "
                f"controlled_dimensions={len(dimensions)}; controlled_records={selected_controls}; "
                f"provenance={output}.generation.json)"
            )
            print(f"Generated {len(records)} record(s) at {output}{status}")
            return 0
        if args.command == "run-agent":
            if args.situation_config:
                if args.dataset is not None or args.skill is not None:
                    parser.error("--situation-config cannot be combined with DATASET or --skill")
                if args.num_scenarios is None:
                    parser.error("--num-scenarios is required with --situation-config")
                if args.page_url is not None:
                    parser.error("multi-turn browser sessions are not supported yet")
                if any((args.max_input_tokens is not None, args.max_output_tokens is not None,
                        args.graphify_python is not None, args.confirm_browser_run,
                        args.fresh_browser_profile, args.allow_response_model_transfer)):
                    parser.error("single-question adapter and browser options do not apply to situation mode")
                completed = run_situation(
                    args.situation_config, args.output,
                    project=args.project or ".", num_scenarios=args.num_scenarios,
                    model=args.model or DEFAULT_ADAPTER_MODEL, env_file=args.env_file,
                    target_python=args.target_python, timeout=args.timeout,
                    runs_root=runs_root, force=args.force,
                    service_url=args.service_url,
                )
                print(f"Completed {completed} situation trial(s) at {args.output}")
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
                env_file=args.env_file,
                force=args.force,
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
            if args.situation_config:
                if args.skill is not None:
                    parser.error("--situation-config cannot be combined with --skill")
                result = evaluate_situation(
                    args.responses, args.situation_config, output=args.output,
                    model=args.model, env_file=args.env_file, strict=args.strict,
                    force=args.force,
                )
                print(f"Evaluated {result['summary']['valid_determinate']} situation trial(s) at {args.output}")
                return 0
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
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
