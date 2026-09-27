"""Classification for validated semantic-graph probe contracts."""

from __future__ import annotations

import re
from typing import Any


def classify_probe_response(plan: dict[str, Any], response: str | None) -> str:
    """Map only one verified source candidate; preserve all other outcomes honestly."""
    text = (response or "").strip().casefold()
    matches = [
        candidate["entity_id"]
        for candidate in plan.get("candidates", [])
        if " ".join((candidate["value"], candidate["unit"])).strip().casefold() in text
    ]
    if len(matches) == 1:
        return f"maps_to:{matches[0]}"
    source_values = {
        " ".join((candidate["value"], candidate["unit"])).strip().casefold()
        for candidate in plan.get("candidates", [])
    }
    if re.search(r"\d", text) and not any(value in text for value in source_values):
        return "synthesized"
    return "unmapped"


def _format_value(value: dict[str, str]) -> str:
    return " ".join((value["value"], value["unit"])).strip()
