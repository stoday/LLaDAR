"""Expose the resume console's public workflow to LLaDAR experiments."""

from __future__ import annotations

import json
import os

import console


def _candidate(candidate: console.CandidateView, rank: int | None = None) -> dict:
    record = {
        "id": candidate.id,
        "age": candidate.age,
        "age_group": candidate.age_group,
    }
    if rank is not None:
        record["rank"] = rank
    return record


def main() -> None:
    case = json.loads(os.environ["LLADAR_CASE"])
    reply = console.ChatSession().respond(case["input"]["message"])
    if reply.kind == "error":
        raise RuntimeError(reply.text)
    print(
        json.dumps(
            {
                "text": reply.text,
                "selected_candidates": [
                    _candidate(candidate, rank)
                    for rank, candidate in enumerate(reply.candidates, start=1)
                ],
                "eligible_candidates": [
                    _candidate(candidate) for candidate in console.DEMO_POOL
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
