from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_dataset(
    dataset: list[dict[str, Any]],
    output: str | Path,
    format: str,
    *,
    overwrite: bool = False,
) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == "jsonl":
        content = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in dataset
        )
    elif format == "json":
        content = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
    else:
        raise ValueError("format must be 'jsonl' or 'json'")
    with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
        handle.write(content)
