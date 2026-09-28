"""Generic selection helpers for planner-declared controlled dimensions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def normalize_controlled_variant_topics(topics: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize caller-selected IDs without owning a domain-specific taxonomy."""
    if topics is None:
        return ()
    raw_topics = topics.split(",") if isinstance(topics, str) else topics
    selected: list[str] = []
    for raw_topic in raw_topics:
        topic = str(raw_topic).strip()
        if not topic:
            continue
        if topic in selected:
            raise ValueError(f"duplicate controlled-variant topic: {topic}")
        selected.append(topic)
    return tuple(selected)


def select_controlled_variant_topics(
    dimensions: Iterable[dict[str, Any]], *, input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[str, ...]:
    """Let a terminal user choose only dimensions accepted for this graph."""
    available = [dimension for dimension in dimensions if isinstance(dimension, dict)]
    if not available:
        return ()
    output_fn("Available controlled-variant dimensions (enter numbers separated by commas; 0 skips):")
    for index, dimension in enumerate(available, start=1):
        output_fn(f"  {index}. {dimension['label']} ({dimension['id']})")
    choice = input_fn("Selection: ").strip()
    if choice in {"", "0"}:
        return ()
    try:
        indexes = [int(item.strip()) for item in choice.split(",")]
    except ValueError as error:
        raise ValueError("enter listed numbers, such as 1,2, or 0") from error
    if not indexes or any(index < 1 or index > len(available) for index in indexes):
        raise ValueError("selection is outside the available dimensions")
    if len(set(indexes)) != len(indexes):
        raise ValueError("a controlled-variant dimension may be selected once")
    return tuple(available[index - 1]["id"] for index in indexes)
