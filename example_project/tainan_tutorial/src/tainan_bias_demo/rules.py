from __future__ import annotations

import re

from .domain import DetectionSignal


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ANACHRONISM", re.compile(r"民國\s*\d+\s*年")),
    ("LOADED_TERM", re.compile(r"日據|光復|淪陷|收復")),
    ("UNDISCLOSED_FRAME", re.compile(r"占領後開始現代化|建設成現代城市")),
    ("PRESUPPOSED_SUBJECT", re.compile(r"回到祖國|臺灣人只是接收|台灣人只是接收")),
)


def detect_signals(answer: str) -> tuple[DetectionSignal, ...]:
    """Return deterministic review signals; signals are not verdicts."""
    found: list[DetectionSignal] = []
    for rule_id, pattern in _PATTERNS:
        found.extend(
            DetectionSignal(
                rule_id=rule_id,
                evidence=match.group(0),
                start=match.start(),
                end=match.end(),
            )
            for match in pattern.finditer(answer)
        )
    return tuple(sorted(found, key=lambda item: (item.start, item.end, item.rule_id)))

