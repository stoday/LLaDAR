from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, Literal

from .cache import cache_key, read_cache, write_cache
from .chunking import (
    KnowledgeChunk,
    chunk_text,
    deserialize_chunks,
    fallback_chunks,
    semantic_chunk_text,
    serialize_chunks,
)
from .exceptions import ChunkingError, DatasetValidationError, ProviderError
from .loaders import KnowledgeInput, load_knowledge
from .model_profiles import resolve_model_profile
from .output import write_dataset
from .progress import ProgressReporter
from .prompts import build_generation_prompt, resolve_strategy
from .providers import AkashaProvider, LLMProvider
from .validation import validate_generated_pair


DEFAULT_MODEL = "gemini:gemini-2.5-flash"


def create_test_dataset(
    knowledge: KnowledgeInput,
    prompt: str | None = None,
    chunk_size: int | Literal["auto"] = 2000,
    overlap: float = 0.1,
    num_pairs: int = 1,
    random_select: int | None = None,
    model: str = DEFAULT_MODEL,
    output: str | Path | None = None,
    format: str = "jsonl",
    *,
    prompt_file: str | Path | None = None,
    provider: LLMProvider | None = None,
    env_file: str | Path = ".env",
    temperature: float = 0.0,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    auto_window_ratio: float | None = None,
    strict: bool = False,
    force: bool = False,
    cache: bool = False,
    cache_dir: str | Path = ".lladar/cache",
    refresh_cache: bool = False,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    if output is not None and Path(output).exists() and not force:
        raise FileExistsError(f"output already exists: {output}")
    if prompt is not None and prompt_file is not None:
        raise ValueError("prompt and prompt_file cannot be used together")
    prompt_source = _prompt_source(prompt, prompt_file)
    if prompt_file is not None:
        prompt = Path(prompt_file).read_text(encoding="utf-8")
    if num_pairs <= 0:
        raise ValueError("num_pairs must be greater than 0")
    if random_select is not None and random_select <= 0:
        raise ValueError("random_select must be greater than 0")

    profile = resolve_model_profile(
        model,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        auto_window_ratio=auto_window_ratio,
    )
    strategy, strategy_text = resolve_strategy(
        prompt,
        prompt_file=str(prompt_file) if prompt_file is not None else None,
    )
    active_provider = provider or AkashaProvider(
        env_file=str(env_file),
        max_input_tokens=profile.max_input_tokens,
        max_output_tokens=profile.max_output_tokens,
    )
    reporter = ProgressReporter(verbose)
    reporter.configuration(
        {
            "knowledge": _knowledge_paths(knowledge),
            "strategy": strategy if strategy == "ambiguity" else "custom",
            "prompt_source": prompt_source,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "num_pairs": num_pairs,
            "random_select": random_select,
            "model": model,
            "output": output,
            "format": format,
            "env_file": env_file,
            "temperature": temperature,
            "max_input_tokens": profile.max_input_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "auto_window_ratio": profile.auto_window_ratio,
            "strict": strict,
            "force": force,
            "cache": cache,
            "cache_dir": cache_dir,
            "refresh_cache": refresh_cache,
            "verbose": verbose,
            "provider": type(active_provider).__name__,
        }
    )

    sources = load_knowledge(knowledge)
    reporter.emit("SOURCE", f"loaded={len(sources)}")
    prepared: list[tuple[Path, int, KnowledgeChunk]] = []
    for source_index, (path, source_text) in enumerate(sources, start=1):
        reporter.emit(
            "SOURCE",
            f"{source_index}/{len(sources)} path={path} characters={len(source_text)}",
        )
        if chunk_size == "auto":
            chunks = _auto_chunks(
                source_text,
                active_provider,
                source_path=path,
                reporter=reporter,
                model=model,
                temperature=temperature,
                max_input_tokens=profile.max_input_tokens,
                max_output_tokens=profile.max_output_tokens,
                auto_window_ratio=profile.auto_window_ratio,
                strict=strict,
                cache=cache,
                cache_dir=cache_dir,
                refresh_cache=refresh_cache,
            )
        elif isinstance(chunk_size, int):
            chunks = [
                KnowledgeChunk(chunk, -1, -1, (), "character")
                for chunk in chunk_text(source_text, chunk_size, overlap)
            ]
        else:
            raise ValueError('chunk_size must be a positive integer or "auto"')
        reporter.emit(
            "CHUNK",
            f"source={path} method={chunks[0].method if chunks else chunk_size} count={len(chunks)}",
        )
        prepared.extend(
            (path, chunk_index, knowledge_chunk)
            for chunk_index, knowledge_chunk in enumerate(chunks)
        )

    planned_pairs = [
        (path, chunk_index, knowledge_chunk, pair_index)
        for path, chunk_index, knowledge_chunk in prepared
        for pair_index in range(num_pairs)
    ]
    planned_total = len(planned_pairs)
    if random_select is not None and random_select < planned_total:
        planned_pairs = random.sample(planned_pairs, random_select)
    total_pairs = len(planned_pairs)
    reporter.emit(
        "PAIR",
        f"planned={planned_total} selected={total_pairs} chunks={len(prepared)}",
    )
    dataset: list[dict[str, Any]] = []
    completed = 0
    for path, chunk_index, knowledge_chunk, pair_index in planned_pairs:
        chunk = knowledge_chunk.text
        key = cache_key(
            chunk,
            knowledge_chunk.method,
            knowledge_chunk.source_start,
            knowledge_chunk.source_end,
            strategy,
            model,
            temperature,
            profile.max_input_tokens,
            profile.max_output_tokens,
            profile.auto_window_ratio,
            pair_index,
        )
        generated = None
        from_cache = False
        if cache and not refresh_cache:
            generated = read_cache(cache_dir, key)
            from_cache = generated is not None
            reporter.emit(
                "CACHE",
                f"pair={completed + 1}/{total_pairs} {'hit' if from_cache else 'miss'}",
            )
        if generated is None:
            last_error: DatasetValidationError | ProviderError | None = None
            for attempt in range(1, 4):
                try:
                    candidate = active_provider.generate_structured(
                        build_generation_prompt(chunk, strategy_text),
                        model=model,
                        temperature=temperature,
                    )
                    generated = validate_generated_pair(candidate)
                    break
                except (DatasetValidationError, ProviderError) as error:
                    last_error = error
                    reporter.emit(
                        "RETRY",
                        f"pair={completed + 1}/{total_pairs} attempt={attempt}/3 error_type={type(error).__name__}",
                    )
            else:
                assert last_error is not None
                completed += 1
                if strict:
                    raise last_error
                reporter.emit(
                    "WARN",
                    f"pair={completed}/{total_pairs} skipped after 3 failed attempts",
                )
                reporter.pair(completed, total_pairs, "skipped")
                continue
            if cache:
                write_cache(cache_dir, key, generated)
                reporter.emit("CACHE", f"pair={completed + 1}/{total_pairs} saved")
        else:
            generated = validate_generated_pair(generated)

        item_id = hashlib.sha256(
            f"{path.resolve()}\0{chunk}\0{chunk_index}\0{pair_index}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        metadata: dict[str, Any] = {
            "strategy": strategy,
            "model": model,
            "temperature": temperature,
        }
        if knowledge_chunk.method != "character":
            metadata.update(
                {
                    "chunk_method": knowledge_chunk.method,
                    "source_start": knowledge_chunk.source_start,
                    "source_end": knowledge_chunk.source_end,
                    "knowledge_facts": list(knowledge_chunk.knowledge_facts),
                }
            )
        dataset.append(
            {
                "schema_version": "1.0",
                "id": item_id,
                "source_file": str(path),
                "chunk_index": chunk_index,
                "source_text": chunk,
                **generated,
                "bias_type": "unsupported_assumption",
                "metadata": metadata,
            }
        )
        completed += 1
        reporter.pair(
            completed,
            total_pairs,
            "cache-hit" if from_cache else "generated",
        )

    if output is not None:
        reporter.emit("WRITE", f"format={format} path={output} items={len(dataset)}")
        write_dataset(dataset, output, format)
    reporter.done(len(dataset))
    return dataset


def _auto_chunks(
    source_text: str,
    provider: LLMProvider,
    *,
    source_path: Path,
    reporter: ProgressReporter,
    model: str,
    temperature: float,
    max_input_tokens: int,
    max_output_tokens: int,
    auto_window_ratio: float,
    strict: bool,
    cache: bool,
    cache_dir: str | Path,
    refresh_cache: bool,
) -> list[KnowledgeChunk]:
    semantic_cache_dir = Path(cache_dir) / "semantic_segments"
    key = cache_key(
        "semantic-auto-v3",
        source_text,
        model,
        temperature,
        max_input_tokens,
        max_output_tokens,
        auto_window_ratio,
    )
    if cache and not refresh_cache:
        cached = read_cache(semantic_cache_dir, key)
        reporter.emit(
            "CACHE",
            f"semantic source={source_path} {'hit' if cached is not None else 'miss'}",
        )
        if cached is not None:
            try:
                return deserialize_chunks(cached, source_text)
            except ChunkingError as error:
                reporter.emit("WARN", f"invalid semantic cache source={source_path} error_type={type(error).__name__}")
                if strict:
                    raise

    last_error: ChunkingError | ProviderError | None = None
    for attempt in range(1, 4):
        try:
            chunks = semantic_chunk_text(
                source_text,
                provider,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                auto_window_ratio=auto_window_ratio,
                window_progress=lambda current, total: reporter.emit(
                    "WINDOW",
                    f"source={source_path} {current}/{total}",
                ),
            )
            if cache:
                write_cache(semantic_cache_dir, key, serialize_chunks(chunks))
                reporter.emit("CACHE", f"semantic source={source_path} saved")
            return chunks
        except (ChunkingError, ProviderError) as error:
            last_error = error
            reporter.emit(
                "RETRY",
                f"semantic source={source_path} attempt={attempt}/3 error_type={type(error).__name__}",
            )
    assert last_error is not None
    if strict:
        raise last_error
    reporter.emit("WARN", f"semantic source={source_path} using character fallback")
    return fallback_chunks(source_text)


def _prompt_source(prompt: str | None, prompt_file: str | Path | None) -> str:
    if prompt_file is not None:
        return f"file:{prompt_file}"
    if prompt is None:
        return "default"
    if prompt == "ambiguity":
        return "built-in"
    return "inline-custom"


def _knowledge_paths(knowledge: KnowledgeInput) -> list[str]:
    if isinstance(knowledge, (str, Path)):
        return [str(knowledge)]
    return [str(path) for path in knowledge]
