"""Shared workload budgets; override with LLADAR_<NAME> environment variables.

These bound memory, capture and network work rather than claiming model token
capacity. Model calls use model_profiles. Protocol validation and authorization
counts remain separate from these configurable workload budgets.
"""
from __future__ import annotations

import os

DEFAULT_LIMITS = {
    "DEFAULT_MODEL_INPUT_TOKENS": 131072,
    "DEFAULT_MODEL_OUTPUT_TOKENS": 65536,
    "MAX_INPUT_BYTES": 4 * 1024 * 1024,
    "MAX_CAPTURE_BYTES": 16 * 1024 * 1024,
    "MAX_RECORDING_BYTES": 500 * 1024 * 1024,
    "MAX_OUTPUT_BYTES": 4 * 1024 * 1024,
    "PROVIDER_RESPONSE_BYTES": 32 * 1024 * 1024,
    "PROVIDER_WORKER_BYTES": 64 * 1024 * 1024,
    "REPAIR_SOURCE_BYTES": 128 * 1024,
    "PROJECT_READ_BYTES": 4 * 1024 * 1024,
    "DOM_NODE_LIMIT": 65536,
    "DOM_CAPTURE_CHARS": 32 * 1024 * 1024,
    "EXTRACTION_TIMEOUT_SECONDS": 3600,
    "BROWSER_STARTUP_TIMEOUT_SECONDS": 300,
    "BROWSER_NAVIGATION_TIMEOUT_SECONDS": 3600,
    "GITHUB_FILE_BYTES": 16 * 1024 * 1024,
    "GITHUB_TOTAL_BYTES": 128 * 1024 * 1024,
    "GITHUB_STREAM_FILE_BYTES": 128 * 1024 * 1024,
    "GITHUB_REPOSITORY_FILES": 10000,
    "GITHUB_SAMPLE_ROWS": 1000000,
    "GITHUB_SAMPLE_SIZE": 1000,
    "GITHUB_SELECTED_SOURCE_CHARS": 4 * 1024 * 1024,
    "GITHUB_REQUESTS": 1000,
    "GITHUB_TIMEOUT_SECONDS": 120,
    "GITHUB_REDIRECTS": 5,
    "GITHUB_HOST_TOOL_CALLS": 1000,
    "GITHUB_MODEL_ROUNDS": 100,
    "MANUAL_VERIFICATION_TIMEOUT_SECONDS": 3600,
    "CSV_FIELD_CHARS": 16 * 1024 * 1024,
    "SKILL_TOOL_CALLS": 1000,
    "SKILL_ROUNDS": 100,
    "AUTO_ADAPTER_REPAIR_ATTEMPTS": 50,
    "EXPLORATION_TOOL_CALLS": 1000,
    "EXPLORATION_READ_CHARACTERS": 4 * 1024 * 1024,
    "PROJECT_LIST_FILES": 10000,
    "PROJECT_PREVIEW_CHARS": 65536,
    "PROJECT_NOTEBOOK_PAGE_CHARS": 65536,
    "PROJECT_SEARCH_RESULTS": 1000,
    "PROJECT_SEARCH_CONTEXT_LINES": 200,
    "PROJECT_SEARCH_SNIPPET_CHARS": 65536,
    "SITUATION_KNOWLEDGE_CHARS": 4 * 1024 * 1024,
    "GRAPH_FILES": 10000,
    "GRAPH_TOTAL_BYTES": 256 * 1024 * 1024,
    "REPORT_METADATA_BYTES": 32 * 1024 * 1024,
    "CHECKPOINT_REPLACE_TIMEOUT_SECONDS": 5,
    "PROJECT_AGENT_ROUNDS": 100,
    "PROTOCOL_SOURCE_BYTES": 128 * 1024,
    "PROTOCOL_INPUT_BYTES": 4 * 1024 * 1024,
    "PROTOCOL_OUTPUT_BYTES": 16 * 1024 * 1024,
    "PROTOCOL_TIMEOUT_SECONDS": 30,
    "PROTOCOL_MEMORY_BYTES": 64 * 1024 * 1024,
    "PROTOCOL_RECURSION_DEPTH": 1000,
    "PROTOCOL_SUSPENSIONS": 1000,
    "PROTOCOL_DEPENDENCY_BYTES": 128 * 1024 * 1024,
    "BROWSER_DISCOVERY_PROBES": 100,
    "BROWSER_DISCOVERY_VERIFICATIONS": 1,
    "BROWSER_DISCOVERY_MODEL_CALLS": 1000,
    "BROWSER_DISCOVERY_TOOL_CALLS": 1000,
    "BROWSER_DISCOVERY_ROUNDS": 20,
    "BROWSER_DISCOVERY_STALL_MODEL_CALLS": 5,
    "BROWSER_DISCOVERY_REQUESTS": 10000,
    "BROWSER_DISCOVERY_ACTIONS": 10000,
    "BROWSER_DISCOVERY_REPAIRS": 100,
    "BROWSER_EVENT_PAGE_SIZE": 4096,
    "INDEPENDENT_AGENT_ROUNDS": 100,
    "INDEPENDENT_PREPARE_STEPS": 12,
    "INDEPENDENT_TURN_STEPS": 1000,
}

# Evidence requirements are minimums, not ceilings on model or browser capacity.
MINIMUM_LIMITS = {"BROWSER_DISCOVERY_VERIFICATIONS": 1}


def resource_limit(name: str) -> int:
    value = os.environ.get("LLADAR_" + name)
    if value is None:
        return DEFAULT_LIMITS[name]
    try:
        limit = int(value)
    except ValueError:
        raise ValueError("LLADAR_" + name + " must be a positive integer") from None
    if limit <= 0:
        raise ValueError("LLADAR_" + name + " must be a positive integer")
    minimum = MINIMUM_LIMITS.get(name)
    if minimum is not None and limit < minimum:
        raise ValueError("LLADAR_" + name + " must be at least " + str(minimum))
    return limit


def configured_resource_limits() -> dict[str, int]:
    """Explicit overrides only, for resume identity without recording env secrets."""
    return {name: resource_limit(name) for name in DEFAULT_LIMITS if 'LLADAR_' + name in os.environ}
