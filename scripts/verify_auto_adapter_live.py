"""Paid acceptance run against the existing example Agent projects.

Requires installed LLaDAR, each target's existing .venv, and valid provider
credentials. The hand-authored three-field records isolate target execution and
evaluation from dataset-generation quality. Run from the repository root.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from lladar.records import validate_record


def dataset(name: str) -> dict:
    if name == "langchain":
        question, answer = "商品退款期限是多少？", "到貨後七天內。"
    else:
        question, answer = "鍵盤庫存多少，放在哪裡？", "十二個，A3 貨架。"
    return validate_record(
        {"question": question, "expected_answer": answer, "actual_response": None}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("../VIDE-TESTING/examples"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini:gemini-3-flash-preview")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path(".lladar") / (
        "live-auto-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    output.mkdir(parents=True, exist_ok=False)
    summary = {}
    for name in ("langchain", "llamaindex"):
        data = output / f"{name}-dataset.jsonl"
        responses = output / f"{name}-responses.jsonl"
        evaluation = output / f"{name}-evaluation.json"
        data.write_text(json.dumps(dataset(name), ensure_ascii=False) + "\n", encoding="utf-8")
        common = ["--env-file", str(args.env_file.resolve()), "--model", args.model]
        commands = [
            [sys.executable, "-m", "lladar.cli", "run-agent", str(data), "--project",
             str(args.examples / f"{name}_agent"), "--output", str(responses), *common],
            [sys.executable, "-m", "lladar.cli", "eval", str(responses),
             "--output", str(evaluation), "--strict", *common],
        ]
        for index, command in enumerate(commands):
            print(f"{name}: {'execution' if index == 0 else 'evaluation'} (paid API)", flush=True)
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            (output / f"{name}-{index}.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            if result.returncode:
                raise RuntimeError(f"{name} stage {index} failed; see {output}")
        rows = [json.loads(line) for line in responses.read_text(encoding="utf-8").splitlines()]
        if len(rows) != 1 or not isinstance(rows[0]["actual_response"], str):
            raise RuntimeError(f"{name} did not produce one real response; see {responses}")
        summary[name] = json.loads(evaluation.read_text(encoding="utf-8"))["summary"]
        if summary[name]["evaluated"] != 1 or summary[name]["judge_error"]:
            raise RuntimeError(f"{name} evaluation was incomplete; see {evaluation}")
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Verified real execution and evaluation. Evidence: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
