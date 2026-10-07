# LLaDAR simple Agent evaluation pipeline

## Status

Accepted implementation contract. This document replaces the earlier schema-v2,
schema-v3, schema-v4, BFS, benchmark, experiment, Method Pack, and compatibility
designs. LLaDAR is not required to read or migrate artifacts produced by those
designs.

## Proposed extension

The 2026-10-07 [evaluation criteria contract](PRD-evaluation-criteria.zh-TW.md)
specifies mutually exclusive `eval --criteria` and `eval --skill` inputs,
including situation reevaluation and evaluation-setting provenance. It takes
precedence for explicit evaluation overrides; unchanged defaults retain their
existing answer-correctness behavior.

[Situation generation and multi-turn behavior evaluation](PRD-situation-generation.zh-TW.md)
is a PRD proposal, not part of the accepted implementation contract below.
It adds a situation artifact under create while keeping the four public command
workflows. Its CLI and data contracts must not be described as implemented
until the proposal is built and verified.

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

The global `lladar --version` option prints `lladar VERSION` to stdout and exits
successfully without a workflow command or model credentials. It reads the
installed `lladar` distribution metadata, generated from `[project].version`
in `pyproject.toml`; CLI source must not contain a separate version number.

The optional `--log PATH` CLI argument records the current workflow's Python
stdout and stderr in one UTF-8 text file while preserving terminal output and
terminal detection. It can appear before the command or on a workflow command.
The file contains emitted progress, Agent traces, summaries, and handled errors;
`--no-verbose` suppresses detailed messages in both destinations. ANSI color
codes are removed from the file. Parent directories are created as needed;
existing log files are rejected before workflow execution, including with
`--force`. Streams and file handles are restored on success, failure, and
cancellation. This is a CLI output record, not Akasha structured `keep_logs` or
a capture of child-process output that bypasses Python stdout/stderr.

Verbose dataset generation displays accepted tool submissions immediately as
candidate previews, and each selected final record before publishing. Batch-file
generation can display results only after the Agent delivers its candidate file.
Target execution displays each question and expected answer before each trial,
then the actual response immediately when available. Missing responses are omitted.
Question, expected answer, and actual response use cyan, green, and magenta
respectively, including multiline content. Details are flushed to stderr and the
optional log immediately so users can inspect direction and cancel with Ctrl+C.
Non-TTY output and `NO_COLOR` remain plain text; log files never contain ANSI
codes. `--no-verbose` suppresses these details in both destinations.

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

`run-agent` accepts the three-field JSONL dataset and one target source: a copied
target project, a browser-captured question workflow selected by `--page-url`,
or the Python-only `answer=` callback for tests and embedding. Automatic adapter
discovery remains the project execution implementation; `--service-url` retains
its project-mode meaning and is not a bare-URL contract-discovery mechanism.

The [browser-capture PRD](PRD-browser-captured-api-evaluation.zh-TW.md) specifies
manual login, calibration, request confirmation, and response extraction. Its
latest 2026-09-25 direction is **direct model extraction as the only browser
answer-extraction path**, replacing generated Python and built-in answer rules,
not retaining them as options or fallbacks. This specification revision does
not claim that the implementation migration or live acceptance is complete.
The model receives only the separately approved response material for the
current request and returns the final text, not code or an extraction DSL.
It receives no tools, credentials, `expected_answer`, or evaluation results;
it must not answer the question itself, summarize, translate, or correct the
target's response. Fixed host code still handles transport, request identity,
completion, size limits, and result shape, but does not select answer fields.
Independent local reference checks and fidelity tests remain required; a valid
model output is not proof of faithful extraction or answer correctness.
Each response requires model extraction, with explicit transfer consent and
bounded calls, time, and tokens. Prior synthetic-evidence consent does not
authorize sending real response content or replenish exhausted model budgets.
Extraction failures remain separate from answer quality; the three-field
contract, project-mode adapters, and later `eval`/`report` stages are unchanged.

The command writes:

- the completed three-field JSONL file;
- `<output>.run.json`, containing execution counts, errors, and target metadata.

It does not evaluate correctness or alter the expected answer.

## Evaluation

`eval` consumes one completed three-field JSONL file and its sibling trials
sidecar when present. It uses one bundled or local `--skill DIRECTORY`; there
is no prompt or prompt-file mode.

The evaluator first returns a frozen plan containing named boolean,
categorical, or numeric dimensions. The same plan is then used for every
completed trial. Each judgment returns dimension values and a rationale. Every
plan includes a boolean `correct` dimension so Python can report stability.

The evaluator Agent interprets language and extracts structured values. Python
computes counts, denominators, rates, distributions, mean, median, minimum, and
maximum. The Agent must not be trusted to calculate or restate aggregate
numbers. Missing responses and failed judgments are excluded from metrics and
reported explicitly.

The evaluation JSON contains skill provenance, evaluator model, frozen plan,
per-trial judgments, aggregate results, per-record stability, coverage, and
errors. Existing output is protected unless `--force` is explicit.

## Reporting

`report` reads the saved evaluation JSON. Fixed Markdown tables are rendered by
Python from stored aggregates. A report-writing Agent may explain those facts,
but receives aggregate facts rather than the full raw response collection and
may not invent or recalculate numbers.

The report includes:

- evaluation skill and frozen method;
- evaluator identity;
- record, scheduled-trial, evaluated, execution-error, and judge-error counts;
- coverage and every computed dimension;
- limitations;
- a stability table and an auditable appendix containing every trial's question,
  expected answer, actual response, structured judgment, and rationale.

## Safety and determinism

- Inputs are treated as untrusted data, not evaluator instructions.
- Source and output files are not silently overwritten.
- Arithmetic is deterministic and independently testable.
- The frozen plan, skill provenance, model, and individual judgments are saved.
- A report is never presented as successful when no records were evaluated.
- Live provider validation is distinct from fake-provider contract tests.

## Removal policy

This is an intentional breaking rewrite. Delete obsolete schemas, converters,
commands, modules, tests, examples, and documentation instead of preserving
compatibility layers. Retain only implementation that directly supports the
four workflows or their target-execution infrastructure.
