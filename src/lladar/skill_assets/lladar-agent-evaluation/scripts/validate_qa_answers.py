#!/usr/bin/env python3
"""Deterministically preflight schema-v2 LLaDAR dataset and answer JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lladar.artifact_schema import ArtifactSchemaError, validate_artifact


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


def expected_cases(records: list[dict]) -> tuple[dict[str, dict], list[str]]:
    expected: dict[str, dict] = {}
    errors: list[str] = []
    for number, group in enumerate(records, 1):
        try:
            validate_artifact("DatasetRecord", group)
        except ArtifactSchemaError as error:
            errors.append(f"dataset:{number}: {error}")
            continue
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id:
            errors.append(f"dataset:{number}: missing id")
            continue
        if group.get("status") == "skipped":
            continue
        original = group.get("original")
        variants = group.get("variants")
        if group.get("status") != "ready" or not isinstance(original, dict) or not isinstance(variants, list):
            errors.append(f"dataset:{number}: invalid ready group")
            continue
        cases = [(group_id, "original", original.get("question"))]
        cases.extend((item.get("id"), item.get("kind"), item.get("question")) for item in variants if isinstance(item, dict))
        for case_id, kind, question in cases:
            if not isinstance(case_id, str) or not case_id:
                errors.append(f"dataset:{number}: missing case id")
            elif case_id in expected:
                errors.append(f"dataset:{number}: duplicate case id {case_id}")
            else:
                expected[case_id] = {
                    "group_id": group_id,
                    "kind": kind,
                    "question": question,
                }
    return expected, errors


def answer_index(records: list[dict]) -> tuple[dict[str, dict], list[str]]:
    indexed: dict[str, dict] = {}
    errors: list[str] = []
    for number, record in enumerate(records, 1):
        try:
            validate_artifact("ObservedAnswerRecord", record)
        except ArtifactSchemaError as error:
            errors.append(f"answers:{number}: {error}")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append(f"answers:{number}: missing id")
        elif record_id in indexed:
            errors.append(f"answers:{number}: duplicate id {record_id}")
        else:
            indexed[record_id] = record
    return indexed, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("answers", type=Path)
    args = parser.parse_args()

    expected, errors = expected_cases(read_records(args.dataset))
    answers, answer_errors = answer_index(read_records(args.answers))
    errors.extend(answer_errors)
    errors.extend(f"missing answer: {case_id}" for case_id in sorted(set(expected) - set(answers)))
    errors.extend(f"answer without dataset case: {case_id}" for case_id in sorted(set(answers) - set(expected)))
    for case_id in sorted(set(expected) & set(answers)):
        case = expected[case_id]
        record = answers[case_id]
        for field in ("group_id", "kind", "question"):
            if record.get(field) != case[field]:
                errors.append(f"answers:{case_id}: mismatched {field}")

    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"OK: {len(expected)} schema-v2 case(s) matched by id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
