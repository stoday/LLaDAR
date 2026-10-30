"""Opt-in acceptance check against the real planning model; never executes a target."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from lladar.plan import DEFAULT_PLAN_MODEL, resolve_plan_output, run_plan
from lladar.language import Language, language_scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("document", "situation", "conversation"), default="situation")
    parser.add_argument("--model", default=DEFAULT_PLAN_MODEL)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    destination = resolve_plan_output(args.output)
    inputs = destination.with_name(destination.name + "-inputs")
    inputs.mkdir(parents=True, exist_ok=False)
    with nullcontext(inputs) as directory:
        root = Path(directory)
        source = root / "refund.md"
        source.write_text("Refund requests are accepted within seven days with a receipt.", encoding="utf-8")
        project = root / "agent"
        project.mkdir()
        (project / "README.md").write_text("Synthetic target placeholder. Do not execute.", encoding="utf-8")
        requirement = (
            "Only plan a reusable behavior_situation configuration. No knowledge documents, target, eval or report. "
            "Goal: observe whether the Agent asks for missing booking details. Observe one response, max_turns=1. "
            "Instructions: vary requests missing the date, time or number of people; observe whether the reply asks for the missing information. "
            "Stop criteria: stop after one reply. Only stage create. Do not execute anything. "
            "Use user_goal='Observe requests for missing booking details', observable_behavior='Reply asks for missing booking details', "
            "method.kind=behavior_situation. Inputs are empty and no target is needed."
        ) if args.case == "situation" else (
            f"Plan document_qa from knowledge {source.as_posix()} against project {project.as_posix()}. "
            "Use 3 questions, method.kind=source_grounded_qa. All four stages: create, run-agent, eval, report. "
            "Evaluate expected_answer correctness. Goal: check answers against the refund policy. "
            "Observable behavior: answer agrees with the refund document; this cannot prove a real refund happened. "
            "No extra evaluation criteria or report formatting required. This is planning only; do not call the target."
        )
        answers = 0

        def answer(_prompt):
            nonlocal answers
            answers += 1
            if answers > 4:
                raise AssertionError("Planner did not converge after four clarification answers")
            return requirement

        with language_scope(Language("zh-TW")):
            destination = run_plan(requirement=None if args.case == "conversation" else requirement, output=destination, model=args.model,
                                   env_file=args.env_file, input_fn=answer, debug=True)
        payload = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
        spec = payload["plan"]
        assert spec["status"] == "ready", spec["diagnostics"]
        expected = ["create"] if args.case == "situation" else ["create", "run-agent", "eval", "report"]
        assert [command[1] for command in spec["commands"]] == expected
        assert (destination / "commands.ps1").is_file()
        assert not (destination / "dataset.jsonl").exists()
        assert not (destination / "situation.json").exists()
        if args.case == "conversation":
            assert answers >= 1, "Expected a real clarification question"
        print(f"LIVE_PLAN_PASS case={args.case} questions={answers} output={destination}")


if __name__ == "__main__":
    main()
