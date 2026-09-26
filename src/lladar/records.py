from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .exceptions import DatasetValidationError


RECORD_FIELDS = {"question", "expected_answer", "actual_response"}


def validate_record(value: Any, *, line_number: int | None = None) -> dict[str, Any]:
    """Validate and return one simple question/answer/response record."""
    location = f"line {line_number}: " if line_number is not None else ""
    if not isinstance(value, dict):
        raise DatasetValidationError(f"{location}record must be a JSON object")
    fields = set(value)
    if fields != RECORD_FIELDS:
        missing = sorted(RECORD_FIELDS - fields)
        extra = sorted(fields - RECORD_FIELDS)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise DatasetValidationError(f"{location}{'; '.join(details)}")
    for field in ("question", "expected_answer"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise DatasetValidationError(f"{location}{field} must be a non-empty string")
    response = value["actual_response"]
    if response is not None and not isinstance(response, str):
        raise DatasetValidationError(f"{location}actual_response must be a string or null")
    return {
        "question": value["question"],
        "expected_answer": value["expected_answer"],
        "actual_response": response,
    }


def read_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise DatasetValidationError(
                    f"line {line_number}: invalid JSON: {error.msg}"
                ) from error
            records.append(validate_record(value, line_number=line_number))
    if not records:
        raise DatasetValidationError("dataset contains no records")
    return records


def write_records(
    records: Iterable[dict[str, Any]],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    from .output import write_dataset

    validated = [validate_record(record) for record in records]
    write_dataset(validated, path, "jsonl", overwrite=overwrite)
    return Path(path)
