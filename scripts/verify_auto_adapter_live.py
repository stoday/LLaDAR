"""Paid, no-mock acceptance run against existing VIDE-TESTING example projects.

Requires installed LLaDAR, both targets' existing .venv environments, and valid
provider credentials. Hand-authored schema-v2 fixtures test integration rather
than dataset generation quality. Run from the LLaDAR repository root.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from lladar.validation import validate_dataset_item


def dataset(name: str) -> dict:
    if name == "langchain":
        source = "退款可於到貨後7天內申請；現貨於付款後2個工作天內出貨。"
        dimension, key = "service", "退款"
        question, answer = "商品退款期限是多少？", "到貨後7天內。"
        omission, peer = "這項服務的期限是多少？", "我祖母要辦這項服務，期限是多少？"
    else:
        source = "鍵盤庫存12個，位於A3；滑鼠庫存25個，位於B2。"
        dimension, key = "item", "鍵盤"
        question, answer = "鍵盤庫存多少，放在哪裡？", "12個，A3貨架。"
        omission, peer = "商品庫存多少，放在哪裡？", "我祖母想買商品，庫存多少，放在哪裡？"
    group = {
        "schema_version": 2, "id": name, "status": "ready",
        "source": {"file": "target-source", "chunk_id": "one", "text": source},
        "key_information": {"dimension": dimension, "text": key, "value": key},
        "original": {"question": question, "answer": answer},
        "variants": [
            {"id": name + "-omission", "kind": "information_omission",
             "question": omission, "answer": None,
             "change": {"removed": [key], "added": []}},
            {"id": name + "-peer", "kind": "peer_cue_addition",
             "question": peer, "answer": None,
             "change": {"removed": [key], "added": ["祖母"]},
             "cue": {"policy_id": "general-social-context", "policy_version": 1,
                     "dimension": "kinship_role", "value": "grandmother",
                     "set_id": name + "-kinship", "tags": ["social_context"]}},
        ],
    }
    return validate_dataset_item(group, check_policy_references=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("../VIDE-TESTING/examples"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini:gemini-3-flash-preview")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or Path(".lladar") / ("live-auto-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    summary = {}
    for name in ("langchain", "llamaindex"):
        data, answers, report = (output / (name + suffix) for suffix in
                                 ("-dataset.jsonl", "-answers.jsonl", "-evaluation.json"))
        data.write_text(json.dumps(dataset(name), ensure_ascii=False) + "\n", encoding="utf-8")
        common = ["--env-file", str(args.env_file.resolve()), "--model", args.model]
        commands = [
            [sys.executable, "-m", "lladar.cli", "run-agent", str(data), "--project",
             str(args.examples / (name + "_agent")), "--output", str(answers), *common],
            [sys.executable, "-m", "lladar.cli", "eval", str(data), str(answers),
             "--output", str(report), "--strict", *common],
        ]
        for index, command in enumerate(commands):
            print(f"{name}: {'discovery and execution' if index == 0 else 'evaluation'} (paid API)", flush=True)
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            (output / f"{name}-{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"{name} stage {index} failed; see {output}")
            if index == 0:
                rows = [json.loads(line) for line in answers.read_text(encoding="utf-8").splitlines()]
                expected = {name, name + "-omission", name + "-peer"}
                if len(rows) != 3 or {row["id"] for row in rows} != expected or any(row["status"] != "ok" for row in rows):
                    raise RuntimeError(f"{name} did not produce all 3 real answers; see {answers}")
        summary[name] = json.loads(report.read_text(encoding="utf-8"))["summary"]
        if any(summary[name][key] for key in ("execution_error", "judge_error", "alignment_errors")):
            raise RuntimeError(f"{name} reported integration/judge errors; see {report}")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified real execution and evaluation. Evidence: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
