# LLaDAR simple Agent evaluation pipeline

## Status

Accepted implementation contract. This document replaces the earlier schema-v2,
schema-v3, schema-v4, BFS, benchmark, experiment, Method Pack, and compatibility
designs. LLaDAR is not required to read or migrate artifacts produced by those
designs.

## Product scope

LLaDAR has exactly four public command workflows:

1. `lladar create test-dataset` generates source-grounded questions and expected
   answers from knowledge files.
2. `lladar run-agent` sends every question to the target Agent and records its
   actual response without scoring it.
3. `lladar eval` uses one evaluator Agent to infer or follow an evaluation goal,
   produces structured per-record observations, and computes aggregates in code.
4. `lladar report` renders the saved evaluation as an evidence-bounded Markdown
   report.

There are no public benchmark, experiment, method, resume, configuration, skill,
or compatibility commands.

## Record contract

Every JSONL line has exactly three fields:

```json
{
  "question": "What city is the capital of Taiwan?",
  "expected_answer": "Taipei",
  "actual_response": null
}
```

`question` and `expected_answer` are non-empty strings. `actual_response` is
either a string or `null`. Unknown fields are rejected.

`create test-dataset` writes `actual_response: null`. `run-agent` writes a new
JSONL file and fills `actual_response`; it never overwrites the source dataset
unless the caller explicitly chooses the same output and uses `--force`.
Execution errors do not become fake responses. The record remains `null`, while
the error and source line are written to the run sidecar.

## Dataset generation

The generator reads `.md` and `.txt` files recursively, chunks their content,
and asks the configured model for one standalone, source-grounded question and
expected answer per accepted chunk. Optional prompt guidance may narrow the
subject or style but may not authorize unsupported facts.

`count=0` means all available chunks. Positive counts are a maximum number of
successfully generated records. Invalid generations may be retried and then
skipped; skipped candidates are progress/log information, not dataset records.

## Target execution

`run-agent` accepts the three-field JSONL dataset and either a test callback in
the Python interface or a copied target project in the CLI. Automatic adapter discovery is the project execution implementation behind this
interface; the Python-only `answer=` callback remains available for tests and embedding.

The command writes:

- the completed three-field JSONL file;
- `<output>.run.json`, containing execution counts, errors, and target metadata.

It does not evaluate correctness or alter the expected answer.

## Evaluation

`eval` consumes one completed three-field JSONL file. It supports:

- automatic mode, where the evaluator Agent proposes an evaluation plan from
  the records;
- guided mode, where `--prompt` or `--prompt-file` supplies the evaluation goal.

The evaluator first returns a frozen plan containing named boolean,
categorical, or numeric dimensions. The same plan is then used for every
eligible record. Each judgment returns dimension values and a rationale.

The evaluator Agent interprets language and extracts structured values. Python
computes counts, denominators, rates, distributions, mean, median, minimum, and
maximum. The Agent must not be trusted to calculate or restate aggregate
numbers. Missing responses and failed judgments are excluded from metrics and
reported explicitly.

The evaluation JSON contains the resolved prompt, evaluator model, frozen plan,
per-record judgments, aggregate results, coverage, and errors. Existing output
is protected unless `--force` is explicit.

## Reporting

`report` reads the saved evaluation JSON. Fixed Markdown tables are rendered by
Python from stored aggregates. A report-writing Agent may explain those facts,
but receives aggregate facts rather than the full raw response collection and
may not invent or recalculate numbers.

The report includes:

- evaluation goal and inferred method;
- evaluator identity;
- total, evaluated, missing-response, and judge-error counts;
- coverage and every computed dimension;
- limitations;
- an auditable appendix containing each question, expected answer, actual
  response, structured judgment, and rationale.

## Safety and determinism

- Inputs are treated as untrusted data, not evaluator instructions.
- Source and output files are not silently overwritten.
- Arithmetic is deterministic and independently testable.
- The frozen plan, resolved prompt, model, and individual judgments are saved.
- A report is never presented as successful when no records were evaluated.
- Live provider validation is distinct from fake-provider contract tests.

## Removal policy

This is an intentional breaking rewrite. Delete obsolete schemas, converters,
commands, modules, tests, examples, and documentation instead of preserving
compatibility layers. Retain only implementation that directly supports the
four workflows or their target-execution infrastructure.
