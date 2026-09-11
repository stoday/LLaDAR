# LLaDAR model-exchange trace

Status: Implemented on 2026-09-11

## Summary

`lladar create test-dataset` needs an opt-in diagnostic trace that preserves
what LLaDAR sends to its model provider and what the provider returns before
JSON parsing. Concise error categories remain useful for normal progress, but
they cannot anticipate every malformed response or prompt weakness.

## Goals

- Preserve every semantic-chunking, question-generation, and quality-judgment
  model exchange, including retries.
- Make failed calls immediately visible from directory names.
- Keep ordinary runs and JSONL output clean.
- Preserve enough evidence to improve prompts without guessing.

## Public interface

```powershell
lladar create test-dataset --knowledge .\knowledge --trace
lladar create test-dataset --knowledge .\knowledge --trace --trace-console
```

- `--trace` writes a collision-free run below `.lladar/runs/`.
- `--trace-console` requires `--trace` and additionally mirrors complete prompts
  and raw responses to stderr.
- Python exposes `trace`, `trace_console`, and an optional `trace_root` for tests
  and embedding applications.
- Config accepts `trace`, `trace_console`, and `trace_root`; relative
  `trace_root` paths resolve from the config file.

## Artifact contract

```text
.lladar/runs/20260911-161500/
|-- run.json
|-- events.jsonl
`-- calls/
    |-- 0001-semantic-chunking-attempt-1-FAIL/
    |   |-- request.json
    |   |-- prompt.txt
    |   |-- response.txt
    |   |-- parsed.json
    |   `-- failure.json
    `-- 0002-semantic-chunking-attempt-2-OK/
```

A call directory is created with suffix `INCOMPLETE`, so interruption leaves an
honest artifact. Normal completion atomically renames it to:

- `OK`: parsing and stage-specific validation succeeded.
- `FAIL`: provider return, parsing, schema validation, or quality judgment did
  not allow the pipeline to advance.
- `INCOMPLETE`: execution stopped before a final outcome was recorded.

Each call records stage context, attempt, model settings, exact UTF-8 prompt,
raw textual response when available, parsed JSON when available, validation or
failure information, and duration. `events.jsonl` indexes calls, cache events,
retries, fallback, and final output.

## Console contract

With `--trace`, stderr shows the trace root, sensitivity warning, stage outcome,
reason, and final call-directory path. Full prompt/response bodies remain in
files. With `--trace-console`, those bodies are also printed to stderr. Dataset
JSON/JSONL output is never mixed with diagnostic output.

## Provider boundary

The trace captures the exact prompt LLaDAR passes to the provider adapter and
the raw text that adapter returns. It does not claim to capture provider-wire
messages, hidden system prompts, or transformations performed inside Akasha or
another provider unless that provider separately exposes them.

## Security

Trace artifacts are sensitive because prompts contain source knowledge and raw
responses may repeat it. Tracing is disabled by default, `.lladar/` remains
ignored by Git, and enabling trace prints a warning. API keys, environment
variables, and `.env` contents must never be recorded. `--trace-console` is a
separate explicit opt-in.

## Acceptance criteria

- A successful call ends in `-OK`; validation or parsing failure ends in
  `-FAIL`; interruption leaves `-INCOMPLETE`.
- `prompt.txt` and `response.txt` contain the exact adapter-boundary strings.
- A failed call contains a human-readable `failure.json` and is referenced by
  console progress.
- Every retry creates a new numbered call directory.
- `--trace-console` mirrors complete prompt and response bodies only to stderr.
- Without `--trace`, no trace directory is created and existing verbose behavior
  remains compatible.
- Config precedence and `--no-verbose` behavior remain unchanged.
