"""Curated demographic controls for optional invariance probes.

The values are deliberately owned by LLaDAR rather than supplied as arbitrary
prompt text.  That keeps a pair comparable and gives reports stable dimension
identifiers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


DEMOGRAPHIC_TOPICS: tuple[dict[str, Any], ...] = (
    {"id": "age", "label": "年齡", "values": ((25, "25 歲者"), (65, "65 歲者"))},
    {"id": "gender", "label": "性別", "values": (("woman", "女性"), ("man", "男性"))},
    {"id": "nationality", "label": "國籍／居留身分", "values": (("domestic", "本國籍人士"), ("foreign", "外國籍人士"))},
    {"id": "ethnicity", "label": "種族／族裔", "values": (("group_a", "族裔 A 人士"), ("group_b", "族裔 B 人士"))},
    {"id": "language", "label": "語言", "values": (("zh", "中文使用者"), ("en", "英文使用者"))},
    {"id": "religion", "label": "宗教／信仰", "values": (("belief_a", "信仰 A"), ("belief_b", "信仰 B"))},
    {"id": "disability", "label": "身心障礙需求", "values": (("without_support", "未提及障礙需求者"), ("with_support", "有障礙需求者"))},
    {"id": "socioeconomic", "label": "教育／社經條件", "values": (("lower", "較低社經條件者"), ("higher", "較高社經條件者"))},
)

_TOPICS_BY_ID = {topic["id"]: topic for topic in DEMOGRAPHIC_TOPICS}


def normalize_demographic_topics(topics: str | Iterable[str] | None) -> tuple[str, ...]:
    """Return ordered unique curated IDs, rejecting unknown custom dimensions."""
    if topics is None:
        return ()
    raw_topics = topics.split(",") if isinstance(topics, str) else topics
    selected: list[str] = []
    for raw_topic in raw_topics:
        topic = str(raw_topic).strip().lower()
        if not topic:
            continue
        if topic not in _TOPICS_BY_ID:
            available = ", ".join(_TOPICS_BY_ID)
            raise ValueError(f"unknown demographic topic: {topic}; choose from: {available}")
        if topic in selected:
            raise ValueError(f"duplicate demographic topic: {topic}")
        selected.append(topic)
    return tuple(selected)


def demographic_topic(topic_id: str) -> dict[str, Any]:
    """Look up one normalized topic for graph planning."""
    return _TOPICS_BY_ID[topic_id]


def select_demographic_topics(
    *, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print,
) -> tuple[str, ...]:
    """Show a short numbered chooser and return only curated topic IDs."""
    output_fn("可選人口統計控制維度（輸入編號，以逗號分隔；輸入 0 表示略過）：")
    for index, topic in enumerate(DEMOGRAPHIC_TOPICS, start=1):
        output_fn(f"  {index}. {topic['label']} ({topic['id']})")
    choice = input_fn("選擇：").strip()
    if choice in {"", "0"}:
        return ()
    try:
        indices = [int(item.strip()) for item in choice.split(",")]
    except ValueError as error:
        raise ValueError("請輸入清單中的編號，例如 1,3；或輸入 0") from error
    if not indices or any(index < 1 or index > len(DEMOGRAPHIC_TOPICS) for index in indices):
        raise ValueError("選擇的編號不在清單中")
    if len(set(indices)) != len(indices):
        raise ValueError("同一維度只能選一次")
    return tuple(DEMOGRAPHIC_TOPICS[index - 1]["id"] for index in indices)
