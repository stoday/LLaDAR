"""Paid acceptance: discover ambiguity, pause, then resume the complete public flow."""
import argparse
from datetime import datetime
import json
import re
from pathlib import Path
import subprocess
import sys

from lladar.interfaces import NeedsConfirmation
from lladar.runner import run_agent
from verify_auto_adapter_live import dataset
from live_acceptance_runtime import runtime_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-python", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, help="Optional dotenv file; defaults to process environment")
    parser.add_argument("--model", default="gemini:gemini-3-flash-preview")
    args = parser.parse_args()
    runtime = runtime_evidence(args.target_python)
    root = Path(__file__).resolve().parents[1]
    output = root / ".lladar" / ("live-interface-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True)
    data, answers = output / "dataset.jsonl", output / "answers.jsonl"
    data.write_text(json.dumps(dataset("langchain"), ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        run_agent(data, answers, project=root / "tests/fixtures/layered_agent",
                  target_python=args.target_python, env_file=args.env_file,
                  model=args.model, runs_root=output / "runs", interactive=False, intent=runtime)
    except NeedsConfirmation as paused:
        run = paused.run
    else:
        raise AssertionError("Two public features must require human confirmation")
    evidence = run / "adapter"
    assert not answers.exists()
    assert not (evidence / "observations.jsonl").exists()
    assert not list(run.rglob("trace.jsonl"))
    assert not (run / "layered_agent/.lladar/harnesses").exists()
    proposal = json.loads((evidence / "interfaces.json").read_text(encoding="utf-8"))
    selected = [item for item in proposal["candidates"]
                if item["public_boundary"] and re.search(
                    r"\bpublic_input(?:\.py)?(?:::|:|\.)chat\b", item["entrypoint"])]
    assert len(selected) == 1
    candidate = selected[0]["id"]
    # The fixture's intended test feature is explicitly customer chat. Resume in
    # a separate process using the same mechanism an operator uses after selection.
    result = subprocess.run([sys.executable, "-m", "lladar.cli", "resume-agent", str(run),
                             "--candidate", candidate, "--no-interactive"],
                            capture_output=True, text=True, encoding="utf-8")
    (output / "resume.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, f"Resume failed: see {output / 'resume.log'}"
    rows = [json.loads(line) for line in answers.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and all(row["status"] == "ok" and row["answer"].startswith("客服回覆：") for row in rows)
    assert {row["id"] for row in rows} == {"langchain", "langchain-omission", "langchain-peer"}
    report = json.loads((evidence / "run.json").read_text(encoding="utf-8"))
    assert report["status"] == "verified" and report["interface_selection"]["method"] == "select"
    assert len(report["verification"]) == 2 and all(item["ok"] for item in report["verification"])
    traces = []
    for path in (evidence / "requests").glob("*/layered_agent/trace.jsonl"):
        events = [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]
        assert "ticket_saved" not in events
        traces.append({"path": str(path), "events": events})
    complete = [trace for trace in traces if {"initialized", "knowledge_loaded", "workflow_started",
                                             "tool_called", "model_answered", "postprocessed"} <= set(trace["events"])]
    assert len(complete) >= 5, "Independent replay and dataset calls must retain the full flow"
    # These five sequential requests are the runner's two independent replays
    # and three dataset sessions, after the coding agent has finished probing.
    final_requests = sorted((evidence / "requests").iterdir())[-5:]
    for directory in final_requests:
        events = [json.loads(line)["event"] for line in
                  (directory / "layered_agent/trace.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {"initialized", "knowledge_loaded", "workflow_started", "tool_called",
                "model_answered", "postprocessed"} <= set(events)
    (output / "verification.json").write_text(json.dumps({"run": str(run), "candidate": candidate,
        "answers": len(rows), "complete_traces": complete}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Paid public-boundary verification passed: {output}")


if __name__ == "__main__":
    main()
