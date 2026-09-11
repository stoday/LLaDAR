from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from .exceptions import LladarError
from .policies import BUILTIN_POLICY_NAME


_TOP_LEVEL_KEYS = {"schema_version", "test_dataset"}
_TEST_DATASET_KEYS = {
    "knowledge",
    "prompt",
    "prompt_file",
    "chunk_size",
    "overlap",
    "count",
    "seed",
    "policies",
    "model",
    "max_input_tokens",
    "max_output_tokens",
    "auto_window_ratio",
    "verbose",
    "trace",
    "trace_console",
    "trace_root",
    "output",
    "env_file",
    "strict",
    "cache",
    "cache_dir",
    "refresh_cache",
}


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


def _string_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _validate_settings(settings: dict[str, Any]) -> None:
    if "knowledge" in settings and not _string_array(settings["knowledge"]):
        raise LladarError(
            "test_dataset.knowledge must be a non-empty array of strings"
        )
    if "policies" in settings and not _string_array(settings["policies"]):
        raise LladarError(
            "test_dataset.policies must be a non-empty array of strings"
        )

    for key in (
        "prompt",
        "prompt_file",
        "model",
        "output",
        "env_file",
        "cache_dir",
        "trace_root",
    ):
        if key in settings and not (
            isinstance(settings[key], str) and settings[key].strip()
        ):
            raise LladarError(f"test_dataset.{key} must be a non-empty string")

    if "prompt" in settings and "prompt_file" in settings:
        raise LladarError("prompt and prompt_file cannot be used together")

    if "chunk_size" in settings:
        chunk_size = settings["chunk_size"]
        if not (
            chunk_size == "auto"
            or (type(chunk_size) is int and chunk_size > 0)
        ):
            raise LladarError(
                "test_dataset.chunk_size must be a positive integer or 'auto'"
            )

    if "overlap" in settings and not (
        _is_number(settings["overlap"]) and 0 <= settings["overlap"] < 1
    ):
        raise LladarError("test_dataset.overlap must satisfy 0 <= value < 1")

    if "count" in settings and not (
        type(settings["count"]) is int and settings["count"] >= 0
    ):
        raise LladarError("test_dataset.count must be a non-negative integer")

    for key in ("max_input_tokens", "max_output_tokens"):
        if key in settings and not (
            type(settings[key]) is int and settings[key] > 0
        ):
            raise LladarError(f"test_dataset.{key} must be a positive integer")
    if "seed" in settings and type(settings["seed"]) is not int:
        raise LladarError("test_dataset.seed must be an integer")

    if "auto_window_ratio" in settings and not (
        _is_number(settings["auto_window_ratio"])
        and 0 < settings["auto_window_ratio"] <= 1
    ):
        raise LladarError(
            "test_dataset.auto_window_ratio must satisfy 0 < value <= 1"
        )

    for key in (
        "verbose",
        "trace",
        "trace_console",
        "strict",
        "cache",
        "refresh_cache",
    ):
        if key in settings and type(settings[key]) is not bool:
            raise LladarError(f"test_dataset.{key} must be a boolean")


def load_test_dataset_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LladarError(f"cannot load config {config_path}: {error}") from error

    unknown_top_level = set(document) - _TOP_LEVEL_KEYS
    if unknown_top_level:
        raise LladarError(f"unknown config key: {sorted(unknown_top_level)[0]}")
    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        if schema_version == 1:
            raise LladarError(
                f"config schema_version 1 is no longer supported; regenerate {config_path}"
            )
        raise LladarError(
            f"unsupported schema_version in config {config_path}; expected 2"
        )
    settings = document.get("test_dataset")
    if not isinstance(settings, dict):
        raise LladarError(f"missing [test_dataset] in config {config_path}")
    unknown_settings = set(settings) - _TEST_DATASET_KEYS
    if unknown_settings:
        raise LladarError(
            f"unknown config key: test_dataset.{sorted(unknown_settings)[0]}"
        )
    _validate_settings(settings)

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
