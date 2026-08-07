from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".txt"}
KnowledgeInput = str | Path | Sequence[str | Path]


def load_knowledge(knowledge: KnowledgeInput) -> list[tuple[Path, str]]:
    roots = [knowledge] if isinstance(knowledge, (str, Path)) else list(knowledge)
    paths: list[Path] = []
    for root in roots:
        path = Path(root)
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.suffix.lower() in SUPPORTED_SUFFIXES
            )
        else:
            paths.append(path)
    unique_paths = sorted(set(paths), key=lambda item: item.as_posix())
    return [(source, source.read_text(encoding="utf-8")) for source in unique_paths]