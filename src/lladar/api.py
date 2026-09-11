from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import random
from pathlib import Path
from typing import Any, Literal, Sequence

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
from .policies import load_generation_policies
from .progress import ProgressReporter
from .prompts import build_generation_prompt, build_quality_judge_prompt
from .providers import AkashaProvider, LLMProvider, generate_structured
from .trace import ModelTrace
from .validation import (
    validate_dataset_item,
    validate_generated_group,
    validate_quality_judgment,
)


DEFAULT_MODEL = "gemini:gemini-2.5-flash"
DEFAULT_DATASET_MODEL = "gemini:gemini-3.7-flash"


def create_test_dataset(
    knowledge: KnowledgeInput,
    prompt: str | None = None,
    chunk_size: int | Literal["auto"] = 2000,
    overlap: float = 0.1,
    count: int = 0,
    seed: int | None = None,
    policies: Sequence[str | Path] | None = None,
    model: str = DEFAULT_DATASET_MODEL,
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
    trace: bool = False,
    trace_console: bool = False,
    trace_root: str | Path = ".lladar/runs",
) -> list[dict[str, Any]]:
    """Generate source-grounded schema-v2 question groups.

    This function generates test data only. It does not run an Agent, infer
    variant answers, or evaluate response outcomes.
    """
    if format != "jsonl":
        raise ValueError("format must be 'jsonl'")
    if output is not None and Path(output).exists() and not force:
        raise FileExistsError(f"output already exists: {output}")
    if prompt is not None and prompt_file is not None:
        raise ValueError("prompt and prompt_file cannot be used together")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a non-negative integer")
    if seed is not None and type(seed) is not int:
        raise ValueError("seed must be an integer")
    if trace_console and not trace:
        raise ValueError("trace_console requires trace")
    if prompt_file is not None:
        prompt = Path(prompt_file).read_text(encoding="utf-8")

    active_policies = load_generation_policies(policies)
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
    )
    reporter = ProgressReporter(verbose)
    model_trace = ModelTrace(
        enabled=trace,
        root=trace_root,
        console=trace_console,
    )
    reporter.configuration(
        {
            "knowledge": _knowledge_paths(knowledge),
            "prompt_source": _prompt_source(prompt, prompt_file),
            "policies": [f"{item['id']}@{item['version']}" for item in active_policies],
            "chunk_size": chunk_size,
            "overlap": overlap,
            "count": count,
            "seed": seed,
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
            "trace": trace,
            "trace_console": trace_console,
            "trace_root": trace_root,
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
                model_trace=model_trace,
            )
        elif isinstance(chunk_size, int) and not isinstance(chunk_size, bool) and chunk_size > 0:
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

    random.Random(seed).shuffle(prepared)
    reporter.emit(
        "GROUP",
        f"candidates={len(prepared)} target_ready={count if count else 'all'}",
    )
    dataset: list[dict[str, Any]] = []
    ready_count = 0
    semantic_ids: dict[str, str] = {}
    policy_cache_identity = [
        {
            "id": item["id"],
            "version": item["version"],
            "dimensions": item["dimensions"],
        }
        for item in active_policies
    ]

    for candidate_number, (path, chunk_index, knowledge_chunk) in enumerate(prepared, 1):
        if count > 0 and ready_count >= count:
            break
        source = _source_record(path, chunk_index, knowledge_chunk)
        candidate_id = _stable_id(
            "candidate",
            str(path.resolve()),
            source["chunk_id"],
        )
        result: dict[str, Any] | None = None
        last_reason = "The generated group did not satisfy the dataset contract."
        last_reason_code = "quality_validation_failed"
        attempts = 0

        item_cache_key = cache_key(
            "question-group-v2",
            knowledge_chunk.text,
            knowledge_chunk.method,
            knowledge_chunk.source_start,
            knowledge_chunk.source_end,
            prompt,
            policy_cache_identity,
            model,
            temperature,
            profile.max_input_tokens,
            profile.max_output_tokens,
            profile.auto_window_ratio,
        )
        cached = None
        if cache and not refresh_cache:
            cached = read_cache(cache_dir, item_cache_key)
            cache_status = "hit" if cached is not None else "miss"
            reporter.emit(
                "CACHE",
                f"group={candidate_number}/{len(prepared)} {cache_status}",
            )
            model_trace.event(
                "cache_lookup",
                scope="question_group",
                group=candidate_number,
                status=cache_status,
            )

        for attempt in range(1, 4):
            attempts = attempt
            judgment_call = None
            failure_stage = "cached_validation"
            try:
                if attempt == 1 and isinstance(cached, dict):
                    generated = validate_generated_group(
                        deepcopy(cached.get("candidate")), active_policies
                    )
                    judgment = validate_quality_judgment(cached.get("judgment"))
                else:
                    failure_stage = "question_generation"
                    generation_prompt = build_generation_prompt(
                        knowledge_chunk.text,
                        active_policies,
                        guidance=prompt,
                    )
                    generation_call = model_trace.call(
                        f"question-generation-group-{candidate_number}-attempt-{attempt}",
                        stage="question_generation",
                        prompt=generation_prompt,
                        model=model,
                        temperature=temperature,
                        context={"group": candidate_number, "attempt": attempt},
                    )
                    try:
                        generated = generate_structured(
                            active_provider,
                            generation_prompt,
                            model=model,
                            temperature=temperature,
                            trace_call=generation_call,
                        )
                        generated = validate_generated_group(generated, active_policies)
                    except DatasetValidationError as error:
                        if generation_call is not None:
                            generation_call.fail(
                                reason_code=_reason_code_from_error(str(error)),
                                reason=str(error),
                                retry=attempt < 3,
                            )
                        raise
                    else:
                        if generation_call is not None:
                            generation_call.ok({"validator": "generated_group"})

                    failure_stage = "quality_judgment"
                    judgment_prompt = build_quality_judge_prompt(
                        knowledge_chunk.text,
                        generated,
                        active_policies,
                    )
                    judgment_call = model_trace.call(
                        f"quality-judgment-group-{candidate_number}-attempt-{attempt}",
                        stage="quality_judgment",
                        prompt=judgment_prompt,
                        model=model,
                        temperature=temperature,
                        context={"group": candidate_number, "attempt": attempt},
                    )
                    try:
                        raw_judgment = generate_structured(
                            active_provider,
                            judgment_prompt,
                            model=model,
                            temperature=temperature,
                            trace_call=judgment_call,
                        )
                        judgment = validate_quality_judgment(raw_judgment)
                    except DatasetValidationError as error:
                        if judgment_call is not None:
                            judgment_call.fail(
                                reason_code=_reason_code_from_error(str(error)),
                                reason=str(error),
                                retry=attempt < 3,
                            )
                        raise
                if not judgment["valid"]:
                    last_reason = judgment["reason"]
                    last_reason_code = judgment["reason_code"]
                    if judgment_call is not None:
                        judgment_call.fail(
                            reason_code=last_reason_code,
                            reason=last_reason,
                            retry=attempt < 3,
                            details={"checks": judgment["checks"]},
                        )
                    reporter.emit(
                        "RETRY",
                        f"group={candidate_number}/{len(prepared)} attempt={attempt}/3 quality=false",
                    )
                    model_trace.event(
                        "group_retry",
                        group=candidate_number,
                        attempt=attempt,
                        retry=attempt < 3,
                        stage="quality_judgment",
                        reason_code=last_reason_code,
                        reason=last_reason,
                    )
                    continue
                if judgment_call is not None:
                    judgment_call.ok(
                        {"validator": "quality_judgment", "checks": judgment["checks"]}
                    )

                result = _ready_record(
                    source,
                    generated,
                    path=path,
                    policies=active_policies,
                )
                if cache and cached is None:
                    write_cache(
                        cache_dir,
                        item_cache_key,
                        {"candidate": generated, "judgment": judgment},
                    )
                    reporter.emit(
                        "CACHE",
                        f"group={candidate_number}/{len(prepared)} saved",
                    )
                    model_trace.event(
                        "cache_saved",
                        scope="question_group",
                        group=candidate_number,
                    )
                semantic_key = _semantic_key(judgment, generated)
                duplicate_of = semantic_ids.get(semantic_key)
                if duplicate_of is not None:
                    result = _skipped_record(
                        candidate_id,
                        source,
                        reason_code="duplicate",
                        reason="Equivalent question group already retained in this run.",
                        attempts=attempt,
                        duplicate_of=duplicate_of,
                    )
                else:
                    semantic_ids[semantic_key] = result["id"]
                break
            except DatasetValidationError as error:
                last_reason = str(error)
                last_reason_code = _reason_code_from_error(str(error))
                reporter.emit(
                    "RETRY",
                    f"group={candidate_number}/{len(prepared)} attempt={attempt}/3 error_type=DatasetValidationError",
                )
                model_trace.event(
                    "group_retry",
                    group=candidate_number,
                    attempt=attempt,
                    retry=attempt < 3,
                    stage=failure_stage,
                    reason_code=last_reason_code,
                    reason=last_reason,
                )
                cached = None

        if result is None:
            result = _skipped_record(
                candidate_id,
                source,
                reason_code=last_reason_code,
                reason=last_reason,
                attempts=attempts,
            )
        dataset.append(result)
        if result["status"] == "ready":
            ready_count += 1
        reporter.group(
            candidate_number,
            len(prepared),
            "ready" if result["status"] == "ready" else result["reason_code"],
        )
        model_trace.event(
            "group_finished",
            group=candidate_number,
            status=result["status"],
            reason_code=result.get("reason_code"),
        )

    if output is not None:
        reporter.emit("WRITE", f"format=jsonl path={output} items={len(dataset)}")
        write_dataset(dataset, output, "jsonl", overwrite=force)
        model_trace.event(
            "dataset_written",
            path=str(output),
            format="jsonl",
            items=len(dataset),
        )
    reporter.done(len(dataset))
    model_trace.event("run_finished", items=len(dataset), ready_items=ready_count)
    return dataset


def _source_record(
    path: Path,
    chunk_index: int,
    knowledge_chunk: KnowledgeChunk,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "file": str(path),
        "chunk_id": f"chunk-{chunk_index:03d}",
        "text": knowledge_chunk.text,
    }
    if knowledge_chunk.source_start >= 0 and knowledge_chunk.source_end >= 0:
        source["locator"] = (
            f"characters {knowledge_chunk.source_start}-{knowledge_chunk.source_end}"
        )
    return source


def _ready_record(
    source: dict[str, Any],
    generated: dict[str, Any],
    *,
    path: Path,
    policies: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    group = deepcopy(generated)
    dimension = group["key_information"]["dimension"]
    group_id = _stable_id("group", str(path.resolve()), source["chunk_id"], dimension)
    for variant in group["variants"]:
        cue = variant.get("cue", {})
        identity = cue.get("value") or "|".join(
            variant["change"]["removed"] + variant["change"]["added"]
        )
        variant["id"] = f"{group_id}-{_stable_hash(variant['kind'], identity)[:12]}"
    item = {
        "schema_version": 2,
        "id": group_id,
        "status": "ready",
        "source": source,
        **group,
    }
    return validate_dataset_item(item, policies)


def _skipped_record(
    item_id: str,
    source: dict[str, Any],
    *,
    reason_code: str,
    reason: str,
    attempts: int,
    duplicate_of: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "schema_version": 2,
        "id": item_id,
        "status": "skipped",
        "source": source,
        "reason_code": reason_code,
        "reason": reason.strip() or "The candidate was not suitable.",
        "attempts": attempts,
    }
    if duplicate_of is not None:
        item["duplicate_of"] = duplicate_of
    return validate_dataset_item(item)


def _semantic_key(judgment: dict[str, Any], generated: dict[str, Any]) -> str:
    supplied = judgment.get("semantic_key")
    if isinstance(supplied, str) and supplied.strip():
        return _normalize(supplied)
    return _normalize(
        "|".join(
            (
                generated["original"]["question"],
                generated["original"]["answer"],
                generated["key_information"]["dimension"],
            )
        )
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _stable_hash(*parts: object) -> str:
    return hashlib.sha256("\0".join(map(str, parts)).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_stable_hash(*parts)[:24]}"


def _reason_code_from_error(message: str) -> str:
    lowered = message.casefold()
    if "original" in lowered and ("question" in lowered or "answer" in lowered):
        return "no_answerable_question"
    if "key_information" in lowered or "key-information" in lowered:
        return "no_key_information"
    if "information_omission" in lowered:
        return "no_valid_omission"
    if "peer_cue" in lowered or "peer cue" in lowered or "policy" in lowered:
        return "no_valid_peer_cue"
    return "quality_validation_failed"


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
    model_trace: ModelTrace,
) -> list[KnowledgeChunk]:
    semantic_cache_dir = Path(cache_dir) / "semantic_segments"
    key = cache_key(
        "semantic-auto-v4",
        source_text,
        model,
        temperature,
        max_input_tokens,
        max_output_tokens,
        auto_window_ratio,
    )
    if cache and not refresh_cache:
        cached = read_cache(semantic_cache_dir, key)
        cache_status = "hit" if cached is not None else "miss"
        reporter.emit(
            "CACHE",
            f"semantic source={source_path} {cache_status}",
        )
        model_trace.event(
            "cache_lookup",
            scope="semantic_chunking",
            source=str(source_path),
            status=cache_status,
        )
        if cached is not None:
            try:
                return deserialize_chunks(cached, source_text)
            except ChunkingError as error:
                reporter.emit(
                    "WARN",
                    f"invalid semantic cache source={source_path} error_type={type(error).__name__}",
                )
                if strict:
                    raise

    last_error: ChunkingError | None = None
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
                trace=model_trace,
                trace_attempt=attempt,
                trace_source=str(source_path),
            )
            if cache:
                write_cache(semantic_cache_dir, key, serialize_chunks(chunks))
                reporter.emit("CACHE", f"semantic source={source_path} saved")
                model_trace.event(
                    "cache_saved",
                    scope="semantic_chunking",
                    source=str(source_path),
                )
            return chunks
        except ProviderError:
            raise
        except ChunkingError as error:
            last_error = error
            reporter.emit(
                "RETRY",
                f"semantic source={source_path} attempt={attempt}/3 error_type=ChunkingError",
            )
            model_trace.event(
                "semantic_chunking_retry",
                source=str(source_path),
                attempt=attempt,
                reason=str(error),
            )
    assert last_error is not None
    if strict:
        raise last_error
    reporter.emit("WARN", f"semantic source={source_path} using character fallback")
    model_trace.event(
        "semantic_chunking_fallback",
        source=str(source_path),
        method="character_fallback",
        reason=str(last_error),
    )
    return fallback_chunks(source_text)


def _prompt_source(prompt: str | None, prompt_file: str | Path | None) -> str:
    if prompt_file is not None:
        return f"file:{prompt_file}"
    if prompt is None:
        return "none"
    return "inline-guidance"


def _knowledge_paths(knowledge: KnowledgeInput) -> list[str]:
    if isinstance(knowledge, (str, Path)):
        return [str(knowledge)]
    return [str(path) for path in knowledge]
