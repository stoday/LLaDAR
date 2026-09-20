"""Load and validate the packaged public artifact contract."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match


class ArtifactSchemaError(ValueError):
    """A public artifact does not match its declared shape."""


@lru_cache(maxsize=1)
def artifact_contract() -> dict[str, Any]:
    path = files("lladar").joinpath("schemas", "v2.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(document)
    return document


@lru_cache(maxsize=1)
def benchmark_contract() -> dict[str, Any]:
    path = files("lladar").joinpath("schemas", "v3.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(document)
    return document

@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    contract = benchmark_contract() if name.startswith("Benchmark") else artifact_contract()
    if name not in contract["$defs"]:
        raise ValueError(f"unknown artifact schema: {name}")
    return Draft202012Validator({**contract, "$ref": f"#/$defs/{name}"})


def artifact_version() -> int:
    return artifact_contract()["$defs"]["TestDatasetConfig"]["properties"]["schema_version"]["const"]


def validate_artifact(name: str, value: Any) -> Any:
    """Validate one parsed TOML/JSON document or one JSONL record."""
    selected = name
    if isinstance(value, dict):
        if name == "DatasetRecord" and value.get("status") in {"ready", "skipped"}:
            selected = "DatasetReady" if value["status"] == "ready" else "DatasetSkipped"
        if name == "ObservedAnswerRecord" and value.get("status") in {"ok", "execution_error"}:
            selected = "ObservedAnswerOk" if value["status"] == "ok" else "ObservedAnswerError"
    error = best_match(_validator(selected).iter_errors(value))
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        if error.validator == "required":
            detail = error.message
        elif error.validator == "additionalProperties":
            detail = error.message
        elif error.validator == "oneOf":
            detail = "does not match an allowed record variant"
        elif error.validator == "const":
            detail = f"expected {error.validator_value!r}"
        elif error.validator == "type":
            detail = f"expected type {error.validator_value!r}"
        elif error.validator in {"minLength", "minItems"}:
            detail = "must not be empty"
        else:
            detail = error.message
        raise ArtifactSchemaError(f"{name}.{location}: {detail}")
    return value
