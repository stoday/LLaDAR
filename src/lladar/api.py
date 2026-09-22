from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

from .chunking import KnowledgeChunk, chunk_text, fallback_chunks, semantic_chunk_text
from .exceptions import DatasetValidationError, KnowledgeLoadError
from .loaders import KnowledgeInput, load_knowledge
from .model_profiles import resolve_model_profile
from .progress import ProgressReporter
from .prompts import build_question_prompt
from .providers import AkashaProvider, LLMProvider, generate_structured
from .records import validate_record, write_records


DEFAULT_DATASET_MODEL = "gemini:gemini-3.7-flash"


def create_test_dataset(
    knowledge: KnowledgeInput,
    *,
    output: str | Path | None = None,
    prompt: str | None = None,
    prompt_file: str | Path | None = None,
    chunk_size: int | str = "auto",
    overlap: float = 0.1,
    count: int = 0,
    seed: int = 0,
    model: str = DEFAULT_DATASET_MODEL,
    provider: LLMProvider | None = None,
    env_file: str | Path = ".env",
    temperature: float = 0.0,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    auto_window_ratio: float | None = None,
    strict: bool = False,
    force: bool = False,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Generate simple source-grounded question records from knowledge files."""
    if count < 0:
        raise ValueError("count must be zero or greater")
    if not 0 <= overlap < 1:
        raise ValueError("overlap must satisfy 0 <= overlap < 1")
    if prompt is not None and prompt_file is not None:
        raise ValueError("provide only one of prompt or prompt_file")
    if prompt_file is not None:
        prompt = Path(prompt_file).read_text(encoding="utf-8")

    profile = resolve_model_profile(
        model,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        auto_window_ratio=auto_window_ratio,
    )
    active_provider = provider or AkashaProvider(
        env_file=str(env_file),
        max_input_tokens=profile.max_input_tokens,
        max_output_tokens=profile.max_output_tokens,
        verbose=verbose,
    )
    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {
            "knowledge": _knowledge_paths(knowledge),
            "count": count,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "seed": seed,
            "model": model,
            "output": output,
        }
    )

    sources = load_knowledge(knowledge)
    if not sources:
        raise KnowledgeLoadError("no supported knowledge files were found")
    candidates: list[tuple[Path, KnowledgeChunk]] = []
    for source_path, source_text in sources:
        if not source_text.strip():
            continue
        if chunk_size == "auto":
            try:
                chunks = semantic_chunk_text(
                    source_text,
                    active_provider,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=profile.max_output_tokens,
                    auto_window_ratio=profile.auto_window_ratio,
                )
            except Exception:
                if strict:
                    raise
                chunks = fallback_chunks(source_text)
        elif isinstance(chunk_size, int) and not isinstance(chunk_size, bool) and chunk_size > 0:
            chunks = [
                KnowledgeChunk(text, -1, -1, (), "character")
                for text in chunk_text(source_text, chunk_size, overlap)
            ]
        else:
            raise ValueError('chunk_size must be a positive integer or "auto"')
        reporter.emit("CHUNK", f"source={source_path} count={len(chunks)}")
        candidates.extend((source_path, chunk) for chunk in chunks)

    random.Random(seed).shuffle(candidates)
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate_number, (source_path, chunk) in enumerate(candidates, 1):
        if count and len(records) >= count:
            break
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                generated = generate_structured(
                    active_provider,
                    build_question_prompt(chunk.text, guidance=prompt or ""),
                    model=model,
                    temperature=temperature,
                )
                if set(generated) != {"question", "expected_answer"}:
                    raise DatasetValidationError(
                        "generation must contain exactly question and expected_answer"
                    )
                record = validate_record(
                    {
                        "question": generated["question"],
                        "expected_answer": generated["expected_answer"],
                        "actual_response": None,
                    }
                )
                identity = (
                    _normalize(record["question"]),
                    _normalize(record["expected_answer"]),
                )
                if identity not in seen:
                    seen.add(identity)
                    records.append(record)
                break
            except (DatasetValidationError, TypeError, KeyError, ValueError) as error:
                last_error = error
                reporter.emit(
                    "RETRY",
                    f"source={source_path} candidate={candidate_number} attempt={attempt}/3",
                )
        else:
            reporter.emit(
                "WARN",
                f"source={source_path} candidate={candidate_number} skipped error={type(last_error).__name__}",
            )
        reporter.group(candidate_number, len(candidates), f"generated={len(records)}")

    if not records:
        raise DatasetValidationError("no valid question records were generated")
    if output is not None:
        reporter.emit("WRITE", f"path={output} items={len(records)}")
        write_records(records, output, overwrite=force)
    reporter.done(len(records))
    return records


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _knowledge_paths(knowledge: KnowledgeInput) -> list[str]:
    if isinstance(knowledge, (str, Path)):
        return [str(knowledge)]
    return [str(path) for path in knowledge]
