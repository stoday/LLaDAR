from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


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
    else:
        raise ValueError("format must be 'jsonl'")
    reservation_owned = False
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        if not overwrite:
            with path.open("x", encoding="utf-8"):
                pass
            reservation_owned = True
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
        reservation_owned = False
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            if reservation_owned:
                path.unlink(missing_ok=True)
        raise
