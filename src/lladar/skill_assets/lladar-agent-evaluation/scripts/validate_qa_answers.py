#!/usr/bin/env python3
"""Preflight-check LLaDAR dataset and answer JSONL files by id."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number}: expected a JSON object")
            records.append(value)
    return records


def index(records: list[dict], label: str) -> tuple[dict[str, dict], list[str]]:
    result: dict[str, dict] = {}
    errors: list[str] = []
    for number, record in enumerate(records, 1):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append(f"{label}:{number}: missing id")
        elif record_id in result:
            errors.append(f"{label}:{number}: duplicate id {record_id}")
        else:
            result[record_id] = record
    return result, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("answers", type=Path)
    args = parser.parse_args()

    dataset, errors = index(read_records(args.dataset), "dataset")
    answers, answer_errors = index(read_records(args.answers), "answers")
    errors.extend(answer_errors)
    errors.extend(f"missing answer: {record_id}" for record_id in sorted(set(dataset) - set(answers)))
    errors.extend(f"answer without dataset item: {record_id}" for record_id in sorted(set(answers) - set(dataset)))
    errors.extend(
        f"answers:{record_id}: missing non-empty answer"
        for record_id, record in answers.items()
        if not isinstance(record.get("answer"), str) or not record["answer"].strip()
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"OK: {len(dataset)} dataset item(s) and {len(answers)} answer(s) matched by id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
