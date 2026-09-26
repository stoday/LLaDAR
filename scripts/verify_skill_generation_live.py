"""Explicit, paid-provider acceptance run; never part of offline CI.

Run from a checkout with: python scripts/verify_skill_generation_live.py
Only the synthetic fixture is sent as knowledge. Credentials stay in .env.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lladar import create_test_dataset
from lladar.records import read_records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/lladar-skill-live/dataset.jsonl"))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default="gemini:gemini-2.5-flash")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "tests/fixtures/skill_generation/knowledge.md"
    try:
        rows = create_test_dataset(
            source, output=args.output,
            model=args.model, env_file=args.env_file, max_input_tokens=32000,
            max_output_tokens=4096, verbose=False, force=args.force,
        )
        provenance = json.loads(Path(str(args.output) + ".generation.json").read_text(encoding="utf-8"))
        assert provenance["status"] == "complete"
        assert read_records(args.output) == rows
        assert provenance["dataset"]["sha256"] == hashlib.sha256(args.output.read_bytes()).hexdigest()
        assert len(provenance["knowledge_points"]) >= 3
        assert len(rows) >= 3
        for execution in provenance["executions"]:
            assert execution["loaded_skills"] == ["knowledge-point-qa"]
            assert execution["skill_files"]["SKILL.md"]
            assert execution["tool_events"]
            assert "python_execute" not in execution["tool_names"]
        original = source.read_text(encoding="utf-8")
        for point in provenance["knowledge_points"]:
            for evidence in point["evidence"]:
                assert original[evidence["start_char"]:evidence["end_char"]] == evidence["quote"]
        print(json.dumps({"status": "automated_checks_passed", "records": len(rows),
                          "output": str(args.output), "semantic_review_required": True}))
        return 0
    except Exception as error:
        # Provider exception strings can include request details or credentials.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
