# Unified LLaDAR artifact schema

## Status

Current implementation contract. This supersedes artifact shapes repeated in earlier PRDs.

## Scope

All public files exchanged with LLaDAR commands or the Python API use definitions in the packaged `src/lladar/schemas/v2.json`.

| Artifact | Encoding | Definition |
| --- | --- | --- |
| Test-dataset configuration | TOML | TestDatasetConfig |
| Generation policy | TOML | GenerationPolicy |
| Dataset record | JSONL | DatasetRecord |
| Observed answer record | JSONL | ObservedAnswerRecord |
| Evaluation report | JSON | EvaluationReport |
| Evaluation comparison item | JSONL | EvaluationItem |

The JSON Schema defines required fields, types, allowed values, cardinalities, and unknown-field handling. Parsed TOML objects and each JSONL record are validated against their named definitions. Each top-level artifact has integer `schema_version` 2. Policy `version` remains an independent revision number. Version 1 artifacts are rejected without conversion.

## Runtime requirements

The schema is packaged in the wheel and source distribution. One loader and validator are used at public read and write boundaries, with field-path errors. Validation is local and does not fetch references. Cross-field, source-grounding, and policy-reference checks remain in Python after shape validation. The README and active PRDs point to this contract.

## Acceptance

Tests verify schema validity, representative accepted and rejected records for each artifact, CLI and Python read/write paths, and wheel inclusion.
