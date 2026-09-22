# LLaDAR

[English](README.md) | [繁體中文](README.zh-TW.md)

LLaDAR is a small Agent-evaluation pipeline with four workflows:

1. Generate questions and expected answers from knowledge.
2. Run a target Agent and capture its actual responses.
3. Let an evaluator Agent choose or follow an evaluation method, then compute
   statistics deterministically in Python.
4. Render an evidence-bounded Markdown report.

## Installation

```bash
python -m pip install lladar
```

Python 3.11 and 3.12 are supported. The default model provider reads its
credentials from `.env` or the process environment.

## Record format

Every JSONL line has exactly three fields:

```json
{"question":"What is Taiwan's capital?","expected_answer":"Taipei","actual_response":null}
```

`question` and `expected_answer` are non-empty strings. `actual_response` is a
string or `null`. There are no schema versions and unknown fields are rejected.

## Quick start

Generate a dataset:

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

Run the target Agent. LLaDAR inspects a copied project and creates a temporary
adapter for its real public workflow:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

An explicit entrypoint may be supplied with `--entrypoint`. It receives the
question in `LLADAR_QUESTION` and must print the final response to stdout.

Evaluate automatically:

```bash
lladar eval responses.jsonl --output evaluation.json
```

Or provide an evaluation goal:

```bash
lladar eval responses.jsonl --prompt "Assess factual correctness and answer completeness." --output evaluation.json
```

Render the report:

```bash
lladar report evaluation.json --output report.md
```

`eval` asks one Agent to freeze a set of boolean, categorical, or numeric
dimensions and judge each completed record. Counts, rates, distributions,
means, medians, minima, and maxima are calculated by Python. `report` renders
those saved facts and includes a record-level appendix.

## Outputs

- `create test-dataset`: three-field JSONL with `actual_response: null`
- `run-agent`: completed three-field JSONL and `<output>.run.json`
- `eval`: evaluation plan, judgments, coverage, and aggregates in JSON
- `report`: Markdown tables, interpretation, limitations, and audit appendix

Existing outputs are protected unless `--force` is specified. See
[the current product contract](docs/PRD-simple-agent-evaluation.md) for the full
behavior and safety boundaries.

## Development

```bash
python -m pip install -e ".[test]"
pytest
```
