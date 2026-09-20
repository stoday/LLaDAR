from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from .exceptions import LladarError
from .artifact_schema import ArtifactSchemaError, artifact_version, validate_artifact
from .policies import BUILTIN_POLICY_NAME


def load_test_dataset_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LladarError(f"cannot load config {config_path}: {error}") from error

    if document.get("schema_version") != artifact_version():
        raise LladarError(f"config schema_version must be 2; regenerate {config_path}")
    try:
        validate_artifact("TestDatasetConfig", document)
    except ArtifactSchemaError as error:
        raise LladarError(str(error)) from error
    settings = document["test_dataset"]

    resolved = dict(settings)
    base = config_path.resolve().parent
    if "knowledge" in resolved:
        resolved["knowledge"] = [base / value for value in resolved["knowledge"]]
    for key in ("prompt_file", "output", "env_file", "cache_dir", "trace_root"):
        if key in resolved:
            resolved[key] = base / resolved[key]
    if "policies" in resolved:
        selected = []
        for value in resolved["policies"]:
            if value == BUILTIN_POLICY_NAME:
                selected.append(value)
            elif "://" in value:
                raise LladarError("generation policies must be local files only")
            else:
                selected.append(base / value)
        resolved["policies"] = selected
    return resolved


def validate_test_dataset_input_paths(
    knowledge: list[str | Path], prompt_file: str | Path | None
) -> None:
    for knowledge_path in (Path(value) for value in knowledge):
        if not knowledge_path.exists():
            raise LladarError(f"knowledge path does not exist: {knowledge_path}")
    if prompt_file is not None and not Path(prompt_file).is_file():
        raise LladarError(f"prompt_file does not exist: {prompt_file}")
