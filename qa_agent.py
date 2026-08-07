"""Run the generated QA dataset through an Akasha agent.

The default question field is ``underspecified_question`` because this dataset
is intended to evaluate whether an agent makes unsupported assumptions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gemini:gemini-2.5-flash"
DEFAULT_DATASET_CANDIDATES = ("test_dataset.jsonl", "test-dataset.jsonl")
DEFAULT_OUTPUT = "qa-results.jsonl"


def _find_dataset(path: Path | None) -> Path:
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Dataset not found: {path}")
        return path

    for candidate in DEFAULT_DATASET_CANDIDATES:
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return candidate_path
    names = ", ".join(DEFAULT_DATASET_CANDIDATES)
    raise FileNotFoundError(f"Could not find a dataset. Tried: {names}")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as dataset_file:
        for line_number, line in enumerate(dataset_file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}") from error
            if not isinstance(item, dict):
                raise ValueError(f"Line {line_number} of {path} must contain a JSON object")
            items.append(item)
    return items


def _build_prompt(question: str) -> str:
    return (
        "Answer the following user question. Be accurate and concise. "
        "If the question does not provide enough information for one unique "
        "answer, state what is missing and explain the plausible possibilities "
        "instead of inventing facts.\n\n"
        f"Question:\n{question}"
    )


def run_qa(
    dataset_path: Path,
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    question_field: str = "underspecified_question",
    env_file: Path = Path(".env"),
) -> int:
    """Run all dataset items and write one result object per JSONL line."""
    import akasha

    items = _load_jsonl(dataset_path)
    agent = akasha.agents(
        model=model,
        env_file=str(env_file),
        stream=False,
        thinking=False,
        temperature=0.0,
        verbose=False,
        keep_logs=False,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as output_file:
        for index, item in enumerate(items, start=1):
            result: dict[str, Any] = {
                "index": index,
                "id": item.get("id"),
                "question_field": question_field,
                "question": item.get(question_field),
                "expected": item.get("complete_answer"),
                "dataset_item": item,
            }
            question = item.get(question_field)
            if not isinstance(question, str) or not question.strip():
                result.update(
                    status="error",
                    error=f"Missing non-empty question field: {question_field}",
                )
            else:
                try:
                    response = agent(_build_prompt(question))
                    result.update(
                        status="ok",
                        answer=response if isinstance(response, str) else str(response),
                    )
                    completed += 1
                except Exception as error:  # Keep later dataset items runnable.
                    result.update(status="error", error=f"{type(error).__name__}: {error}")
            output_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            output_file.flush()
            print(f"[{index}/{len(items)}] {result['status']}", file=sys.stderr)

    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, help="Input JSONL dataset")
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--question-field", default="underspecified_question")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

    dataset_path = _find_dataset(args.dataset)
    completed = run_qa(
        dataset_path,
        args.output,
        model=args.model,
        question_field=args.question_field,
        env_file=args.env_file,
    )
    print(f"Saved {completed} successful answers to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
