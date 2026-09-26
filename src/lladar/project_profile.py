"""Source-grounded description of a target project for later reporting."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .runner import SandboxTools


_PROMPT = """Inspect the target project's README and relevant code/configuration using the tools.
Return ONLY a JSON object with these keys:
{"overview": string, "project_name": string or null,
 "agent_name": string or null, "model_name": string or null,
 "identity_status": "confirmed" or "inferred" or "unknown",
 "candidates": array of strings, "evidence": array of relative file paths}.
Describe what the project actually does. Distinguish configured choices from the model
actually used during this run. If multiple runtime models are possible and none is
verified, set model_name to null, identity_status to unknown, and list candidates.
Every non-null identity must have supporting file evidence. Never infer from the
adapter discovery model, evaluation judge model, or project folder name alone.
Do not read secret files. Do not claim to have run code or verified a provider.
"""


def _object(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    raw = str(response).strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        raw = raw.split("\n", 1)[1].rsplit(fence, 1)[0].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "overview" in value:
            return value
    raise ValueError("project profile must be a JSON object")


def describe_project(workspace: Path, *, model: str, env_file: str | Path | None) -> dict[str, Any]:
    """Ask Akasha to summarize a managed project copy, preserving cited paths."""
    import akasha

    explorer = SandboxTools(workspace)
    def safe_read(name: str) -> str:
        parts = [part.casefold() for part in Path(name).parts]
        if any(part.startswith(".env") or part in {"secrets", "credentials.json",
                                                     "id_rsa", ".ssh"} for part in parts):
            raise ValueError("project profile cannot read credential files")
        path = explorer._path(name)
        if path.stat().st_size > 128_000:
            raise ValueError("project profile read exceeds size limit")
        return explorer.read_file(name)

    tools = [
        akasha.create_tool("List project files.", explorer.list_directory, "list_files"),
        akasha.create_tool("Read a bounded UTF-8 project file.", safe_read, "read_file"),
        akasha.create_tool("Search project source text.", explorer.search, "search_files"),
    ]
    agent = akasha.agents(model=model, env_file=str(env_file) if env_file else None,
                          tools=tools, stream=False, thinking=True, verbose=False,
                          keep_logs=False, max_round=30,
                          max_input_tokens=24000, max_output_tokens=4096)
    result = _object(agent(_PROMPT))
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(path, str) for path in evidence):
        raise ValueError("project profile has no valid evidence list")
    for name in evidence:
        path = explorer._path(name)
        if not path.is_file():
            raise ValueError(f"project profile evidence is not a file: {name}")
    overview = result.get("overview")
    if not isinstance(overview, str) or not overview.strip():
        raise ValueError("project profile has no overview")
    status = result.get("identity_status")
    if status not in {"confirmed", "inferred", "unknown"}:
        raise ValueError("project profile has invalid identity status")
    for field in ("project_name", "agent_name", "model_name"):
        if result.get(field) is not None and (not isinstance(result[field], str) or not evidence):
            raise ValueError(f"project profile has unsupported {field}")
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list) or not all(isinstance(x, str) for x in candidates):
        raise ValueError("project profile candidates must be strings")
    return {"status": "ready", "overview": overview.strip(),
            "project_name": result.get("project_name"),
            "agent_name": result.get("agent_name"),
            "model_name": result.get("model_name"),
            "identity_status": status,
            "candidates": candidates, "evidence": evidence,
            "evidence_sha256": {name: hashlib.sha256(explorer._path(name).read_bytes()).hexdigest()
                                for name in evidence},
            "profile_model": model}
