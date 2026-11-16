"""Value-free diagnostics for CLI model credentials and target inheritance."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from .language import tr


def _location(path: Path) -> str:
    absolute = Path(os.path.abspath(path))
    try:
        location = absolute.relative_to(Path.cwd()).as_posix()
    except ValueError:
        location = "<external>/" + path.name
    # Paths must not inject terminal escape sequences or extra log lines.
    return json.dumps(location, ensure_ascii=False)[1:-1]


def environment_settings(model: str, env_file: str | Path | None, *,
                         include_target: bool = False) -> dict[str, str]:
    """Describe credential inputs without modifying the environment or building clients.

    Akasha selects an existing env file exclusively; target_environment instead
    fills missing process variables from that file. Target applications may load
    more configuration themselves, which cannot be inferred here.
    """
    process = dict(os.environ)
    path = Path(env_file) if env_file else None
    rows = {"env_file": _location(path) if path else tr("Not configured")}
    file_selected = False
    values, raw = {}, {}
    try:
        file_selected = path is not None and path.exists()
        rows["env_file_status"] = tr("Exists") if file_selected else tr("Does not exist")
        if file_selected:
            if not path.is_file():
                raise OSError
            # Do not let dependency diagnostics become an alternate disclosure path.
            with redirect_stderr(io.StringIO()):
                raw = dotenv_values(path, interpolate=False)
                values = dotenv_values(path)
    except (OSError, UnicodeError):
        rows["env_file_status"] = tr("Cannot inspect environment file")
        rows["model_env"] = tr("Credential sources could not be inspected")
        return rows
    selected = values if file_selected else process
    rows["model_env_policy"] = tr("Akasha: environment file only") if file_selected else tr("Akasha: process environment")
    provider = model.split(":", 1)[0].lower()
    if provider in {"google", "gemini", "gemi"}:
        required = ["GEMINI_API_KEY"]
    elif provider in {"azure", "azure-openai", "azure_openai"}:
        required = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL"]
    elif provider in {"openai", "gpt", "gpt-3.5"}:
        azure = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL"]
        required = azure if all(selected.get(key) for key in azure) and not selected.get("OPENAI_API_KEY") else ["OPENAI_API_KEY"]
        # Akasha also validates a partially configured Azure pair for OpenAI.
        if any(selected.get(key) for key in azure):
            required = list(dict.fromkeys([*required, *azure]))
    elif provider in {"anthropic", "anthropicai", "claude", "anthro"}:
        required = ["ANTHROPIC_API_KEY"]
    elif provider in {"ollama", "llama-cpu", "llama-gpu", "llama", "llama2", "llama-cpp"}:
        required = []
        rows["model_env"] = tr("No required credential variables")
    else:
        required = []
        rows["model_env"] = tr("Provider requirements not inspected")

    def describe(key, effective, source):
        status = tr("Set") if effective.get(key) else tr("Unset or empty")
        return tr("{status}; source: {source}", status=status, source=source)

    for key in required:
        source = tr("Environment file") if file_selected else tr("Process environment")
        if key not in selected:
            source = tr("None")
        elif file_selected and "${" in (raw.get(key) or ""):
            source = tr("Environment file (interpolated)")
        rows["model_env." + key] = describe(key, selected, source)
    if include_target:
        rows["target_env_policy"] = tr("Target: process environment first, file fills missing variables; application loading not inspected")
        effective = {**{name: value for name, value in values.items() if value is not None}, **process}
        for key in required:
            source = tr("Process environment") if key in process else tr("Environment file") if values.get(key) is not None else tr("None")
            if key not in process and "${" in (raw.get(key) or ""):
                source = tr("Environment file (interpolated)")
            rows["target_env." + key] = describe(key, effective, source)
    return rows
