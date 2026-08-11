# LLaDAR

This repository is used to test LLaDAR and to provide examples of how to use it.

LLaDAR is a tool for automatically testing LLM agents against a knowledge base. It reads knowledge-base text, generates question-and-answer test cases, and then evaluates an agent's answers to identify unsupported assumptions. This makes it possible to test whether an agent knows when the available information is insufficient instead of inventing an answer.

LLaDAR generates contrastive test datasets from `.txt` and `.md` knowledge sources. Each dataset contains complete questions and answers as well as underspecified questions, where an important fact has been removed. An agent can then be tested against these cases and its JSONL answers can be evaluated.

The package provides dataset generation and answer evaluation with JSON reports and improvement recommendations.

## What this repository demonstrates

- Generating question-and-answer test datasets from knowledge-base documents
- Testing an agent with complete and underspecified questions
- Evaluating agent answers for unsupported assumptions
- Producing JSON reports and recommendations for improving agent behavior

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

Generated natural-language fields follow the dominant language of the source
knowledge. Questions, answers, missing-information descriptions, and
unsupported-assumption examples are not intentionally translated into English.
The `acceptable_behaviors` values remain fixed machine-readable tokens such as
`ask_clarification` and `list_possibilities`.

### Dataset generation options

The default mode is best-effort: invalid model outputs are retried three times
and then skipped. Add `--strict` to fail the run when an item cannot be
generated or validated.

- `--random-select N`: randomly select at most N question pairs. If N is larger than the available pair count, all pairs are selected. Selection happens before provider calls.
- `--verbose` / `--no-verbose`: enable or disable timestamped progress on stderr. Verbose mode is enabled by default and reports source loading, chunking, retries, cache activity, pair progress, elapsed time, and ETA.
- `--prompt NAME_OR_TEXT`: select the built-in strategy or provide inline generation instructions.
- `--prompt-file PATH`: load generation instructions from a UTF-8 file. Cannot be combined with `--prompt`.
- `--chunk-size N`: split knowledge files into fixed-size character chunks.
- `--chunk-size auto`: use semantic segmentation before question generation.
- `--num-pairs N`: generate N question pairs per chunk.
- `--format jsonl` / `--format json`: choose one JSON object per line or one JSON array.
- `--force`: allow overwriting an existing output dataset.
- `--cache`: reuse semantic chunks and generated pairs from `.lladar/cache/`.
- `--refresh-cache`: regenerate cached entries when `--cache` is enabled.
- `--strict`: stop instead of skipping invalid generation or chunking results.

## Run a project agent

`run-agent` connects a generated dataset to an existing project agent without
modifying the original project. It copies the project to a managed workspace
under `.lladar/runs/`, uses an Akasha tool-calling controller to adapt the copy
when the entrypoint has no question input seam, and runs each dataset item in
an independent process:

~~~bash
lladar run-agent test-dataset.jsonl \
  --project ./example_project \
  --entrypoint main.py \
  --output qa-results.jsonl
~~~

The copy excludes `.env`, `.git`, virtual environments, caches, cookies, and
browser profiles. The runner reuses the original project's `.venv` Python
interpreter when it exists; it does not copy the virtual environment. Provider
settings from `--env-file` are injected into child processes instead of copied
into the workspace. If the adapter cannot prove
that `LLADAR_QUESTION` reaches the copied entrypoint, the run fails instead of
repeating a hard-coded question. The generated `qa-results.jsonl` can be
passed directly to `lladar eval`.

The managed copy is intentionally retained for inspection and cleanup. Its
path is printed in the verbose progress output.

Run progress is enabled by default and is written to stderr, including the
dataset count, adaptation stage, per-item status, elapsed time, ETA, and
error type. Use `--no-verbose` only when quiet execution is required.

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

### CLI evaluation

~~~bash
lladar eval test-dataset.jsonl qa-results.jsonl --prompt "Do not invent missing facts; ask for clarification when information is insufficient." --output reports/evaluation.json
~~~

The CLI matches records by id and uses the same evaluator as the Python API.
Use --strict to stop on alignment or judge errors, and
--no-include-raw-answers to omit answer text from the report.

## Install the agent-evaluation skill

Install LLaDAR first, then install the skill into the current project for the
agent platform you use:

~~~bash
pip install lladar
lladar skill install --target codex
~~~

Supported project-local targets are codex, claude, and antigravity.
Use --target all to install into all three platform directories. The installer
does not overwrite modified skill files unless --force is provided.

~~~bash
lladar skill list
lladar skill update --target claude
lladar skill uninstall --target claude
~~~

The skill is installed into .codex/skills, .claude/skills, or
.agents/skills respectively. These platform directories are project-local;
the installer does not modify global agent configuration.

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
- `metadata`: strategy, model, and temperature; custom inline strategies use `custom-<prompt-text>`, while `--prompt-file` strategies use `custom-<prompt-file-path>`; auto chunks also include `chunk_method`, `source_start`, `source_end`, and `knowledge_facts`

Source documents are placed inside an explicit untrusted-data boundary in the model prompt. Instructions found inside source documents must not be followed.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
```

See `docs/PRD-lladar-test-dataset.md` for the complete product requirements.
See `docs/PRD-lladar-evaluation.md` for the evaluation workflow and `docs/PRD-lladar-skill-installer.md` for cross-platform skill installation.
## License

LLaDAR is released under the [MIT License](LICENSE).
