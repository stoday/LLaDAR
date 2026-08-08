# LLaDAR

LLaDAR generates contrastive test datasets and evaluates agent answers for unsupported assumptions. It turns .txt and .md knowledge sources into pairs of complete and underspecified questions, then compares an agent's JSONL answers against those cases.

The package provides dataset generation and answer evaluation with JSON reports and improvement recommendations.

## Installation

```bash
python -m pip install lladar
```

Python 3.11 and 3.12 are supported.

## Python API

```python
import lladar

items = lladar.create_test_dataset(
    knowledge="./knowledge",
    prompt="ambiguity",
    chunk_size=2000,
    overlap=0.1,
    num_pairs=1,
    model="gemini:gemini-2.5-flash",
    output="test-dataset.jsonl",
    verbose=True,
)
```

`knowledge` can be a file, a directory, or a list of paths. Directories are scanned recursively for `.txt` and `.md` files. The API returns `list[dict]` even when `output` is provided.

Use `prompt` for an internal strategy name or custom strategy text. Use `prompt_file` instead to load a strategy from UTF-8 text. Supplying both is an error.

The default provider uses `akasha-terminal` and reads Gemini credentials from `.env` or the process environment. Credentials are never written to the dataset.

Set `chunk_size="auto"` to run semantic segmentation before question generation. The library labels exact source units, the model selects contiguous unit IDs and concise knowledge facts, and the library extracts final `source_text` from the original document. For Gemini 2.5 Flash, each large-text window is conservatively limited to 80% of the model's 65,536-token maximum output (52,428 characters); larger files are processed window by window with 10% internal overlap and offset-based deduplication. Auto mode ignores the public `overlap` value. In best-effort mode, invalid segmentation falls back to fixed 800-character chunks. With `strict=True`, it raises `ChunkingError` instead.

Model limits are resolved from one internal profile. Gemini 2.5 Flash defaults to 1,048,576 input tokens, 65,536 output tokens, and an auto-window ratio of 0.8; unknown models use conservative 16,384/8,192 limits. Override them with `max_input_tokens`, `max_output_tokens`, and `auto_window_ratio`. CLI equivalents are `--max-input-tokens`, `--max-output-tokens`, and `--auto-window-ratio`.

Progress reporting is enabled by default (`verbose=True`). It writes timestamped configuration and source, semantic-window, chunk, cache, retry, pair, write, and completion updates to stderr, keeping JSON/stdout clean. Labels use ANSI colors when stderr is a real TTY, and pair updates include elapsed time and a best-effort ETA. Effective non-secret settings are shown, but prompt text, environment-file contents, credentials, and provider exception messages are not printed. Set `verbose=False` in Python or pass `--no-verbose` on the CLI to disable progress.

## CLI

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --prompt ambiguity \
  --chunk-size 2000 \
  --overlap 0.1 \
  --num-pairs 1 \
  --model gemini:gemini-2.5-flash \
  --output test-dataset.jsonl
```

Pass `--chunk-size auto` for semantic segmentation. Use `--format json` for a JSON array. Existing output files are protected unless `--force` is supplied. Optional caching is enabled with `--cache`; cache files are stored under `.lladar/cache/` by default. Use `--refresh-cache` to regenerate cached entries. Progress is enabled by default; pass `--no-verbose` for quiet operation.

The default mode is best-effort: invalid model outputs are retried three times and then skipped. Add `--strict` to fail the run when an item cannot be generated or validated.

## Evaluate agent answers

After generating a dataset, run it through the agent being evaluated. Each answer
record must use the dataset id and store the answer in an answer field. The
included qa_agent.py produces this format in qa-results.jsonl.

~~~python
import lladar

report = lladar.eval(
    "test-dataset.jsonl",
    "qa-results.jsonl",
    prompt=(
        "Do not invent missing facts. Pass when the agent asks for clarification, "
        "states that information is insufficient, or lists supported possibilities."
    ),
    output="reports/evaluation.json",
)

print(report["summary"])
~~~

The evaluator matches records by id, never by line number. It uses deterministic
checks plus an Akasha judge (default model gemini:gemini-2.5-flash). The report
contains pass, fail, partial, and error counts, per-item judge rationale,
alignment errors, and recommendations. It writes both:

~~~text
reports/evaluation.json
reports/evaluation.items.jsonl
~~~

Use strict=True to fail on missing or duplicate IDs, missing answers, or judge
errors. Use include_raw_answers=False when the report should omit answer text.
The evaluation rubric is passed through prompt; it controls the judge only and
does not replace the fixed report aggregation.

## Dataset schema

Each JSONL line or JSON array item contains:

- `schema_version`: currently `1.0`
- `id`: deterministic item identifier
- `source_file`, `chunk_index`, `source_text`: source traceability
- `complete_question`, `complete_answer`: the fully specified control case
- `underspecified_question`: the question with one important fact removed
- `missing_information`: the removed fact
- `invalid_assumptions`: unsupported single-answer assumptions
- `acceptable_behaviors`: clarification, enumerating possibilities, or stating insufficient information
- `bias_type`: currently `unsupported_assumption`
- `metadata`: strategy, model, and temperature; auto chunks also include `chunk_method`, `source_start`, `source_end`, and `knowledge_facts`

Source documents are placed inside an explicit untrusted-data boundary in the model prompt. Instructions found inside source documents must not be followed.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
```

See `docs/PRD-lladar-test-dataset.md` for the complete product requirements.
## License

LLaDAR is released under the [MIT License](LICENSE).
