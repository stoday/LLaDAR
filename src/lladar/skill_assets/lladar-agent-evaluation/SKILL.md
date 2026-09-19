---
name: lladar-agent-evaluation
description: Run a project's Agent against a LLaDAR schema-v2 contrastive dataset, collect observed answers by stable case ID, evaluate LLaDAR BFS, or diagnose and improve an Agent from the report.
---

# LLaDAR Agent evaluation

Run the fixed workflow in order. Keep the evaluated Agent's framework,
provider, knowledge, and lifecycle intact; Akasha belongs to LLaDAR's generator,
adapter controller, and judge, not necessarily to the evaluated Agent.

## 1. Establish the Agent seam

Inspect the project's README, package metadata, entrypoints, tests, and
configuration. Record the knowledge paths, one-question invocation seam,
normal execution command, required services, and human-only login steps.

The test boundary is the outer user-facing workflow, including initialization,
configured services, knowledge, available tools, routing and final processing.
Do not replace it with a lower-level model or agent method. Automatic mode first
performs read-only discovery and validates source-line evidence. If the intended
feature is unclear, pass `--intent` or obtain human clarification.

Use `lladar run-agent --project` without an entrypoint to discover the project's
input/output and generate an adapter. An existing executable that reads
`LLADAR_QUESTION` may use the explicit `--entrypoint` compatibility mode. Use
`references/adapter-template.md` only when automatic discovery cannot integrate
the project. Report missing services or ambiguous interfaces instead of guessing.

Completion: the project, knowledge, environment, and required services are
identified; independent execution verification happens in step 3.

## 2. Generate or select schema-v2 data

```bash
lladar create test-dataset --knowledge <knowledge-path> --output test-dataset.jsonl
```

Keep generated data separate from observed answers. Ready records contain an
original plus variants; skipped records remain coverage evidence and schedule
no Agent calls. Schema-v1 data must be regenerated.

Completion: every dataset line validates as schema version 2 and at least one
record has `status: "ready"`.

## 3. Run isolated sessions

```bash
lladar run-agent test-dataset.jsonl \
  --project <project-path> \
  --env-file <env-file> \
  --output qa-results.jsonl
```

The runner copies the project below `.lladar/runs/`, excludes local secrets and
state, reuses the original virtual-environment interpreter when present, and
runs every ready original and variant in an independent process. Automatic mode
also creates a fresh copy per execution. It generates an adapter using at most two
distinct questions, independently verifies it, then fixes that adapter for the
dataset run. Do not pass reference answers or judge labels to discovery. Inspect
`.lladar/runs/<run>/adapter/` for adapter code, audit, verification, and observations.
Check that it calls the real Agent, preserves its answer, correlates outputs, and
cleans up any services it starts. Verification proves execution, not answer quality.
The controller and target must have distinct Python environments. A missing
target `.venv` is a blocker; never install target requirements into LLaDAR or
reuse LLaDAR's interpreter. The runner verifies the target's actual environment
and clears inherited Python import paths. Use `--target-python` to select the
target's existing environment, `--timeout` to bound each
execution, and `--max-tool-calls` to bound discovery. Missing dependencies or
credentials are blockers, not reasons to mock a provider or change the Agent.

Explicit `--entrypoint` accepts a project-relative path or a path inside the
original project and retains the existing `LLADAR_QUESTION`/stdout behavior.

Ambiguous automatic runs pause before adapter generation or target calls. The
terminal menu accepts a candidate, `c` for clarification, or `q` to save. In
noninteractive mode (`--no-interactive`), exit code 3 means `needs_confirmation`,
not execution failure. Preserve the printed run directory and hand the feature
choice to the human; do not silently select the easiest candidate.

Continue with `lladar resume-agent <run> --candidate <id>` or
`lladar resume-agent <run> --clarification "<human intent>"`. Omitting both opens
the menu when a terminal is available. Resume rejects changed source/data and
protects existing outputs; do not edit saved evidence to bypass these checks.
Human selection establishes the requested feature, not proof of runtime behavior.

Preserve actual Agent output. Execution failures belong in answer JSONL as
`execution_error`; later sessions may continue. Hand credentials, OTP, CAPTCHA,
payment, and interactive login to the user. Keep tokens, cookies, hidden
prompts, and provider logs out of artifacts.

Completion: `qa-results.jsonl` contains one attempted schema-v2 record for every
ready original and variant, using the exact source IDs and questions.

## 4. Preflight and evaluate

Read `references/answer-schema.md` when validating or producing answer JSONL.
Run the deterministic check, then the fixed evaluator:

```bash
python .codex/skills/lladar-agent-evaluation/scripts/validate_qa_answers.py test-dataset.jsonl qa-results.jsonl
lladar eval test-dataset.jsonl qa-results.jsonl --output reports/evaluation.json
```

The evaluator joins by ID. `--prompt` adds domain guidance but cannot override
the LLaDAR BFS session, eligibility, correctness, equivalence, or score rules.
Use `--strict` when any alignment or judge error must stop the run. Use
`--no-include-raw-answers` when reports must omit answers.

Completion: both report JSON and `.items.jsonl` exist. Report BFS beside
original accuracy, scoring coverage, clarification/execution/judge error rates,
alignment errors, and the most relevant kind/cue breakdowns. Label the score as
LLaDAR-specific, not an official BBQ or FairMT metric.

## 5. Improve only when requested

Treat the report as diagnosis. Make the smallest justified Agent change, rerun
the same dataset, and compare reports under the same protocol. Never fabricate
missing answers or silently retry uncertain writes.

## Integrity boundaries

- Automatic discovery tries an independent Graphify tool environment by default;
  `--no-graphify` disables it. Missing/failed extraction falls back to source reads.
  Inspect graph-status.json and source lines; graph inference is not runtime proof.
- For REST targets, use the existing public route and preserve its request handling
  and final formatting. Inspect startup/readiness/request/response/coverage in the
  interface proposal. Never bypass the API by importing an internal agent function.
- Missing service setup requires clarification. A supplied `--service-url` selects
  an existing test service that the adapter must not start or stop. Otherwise use an
  isolated local instance and the supplied service helper; retain logs and traces.
- Report frontend omissions, unverified cross-language links and untested streaming
  or job-polling contracts. Do not equate JSON API acceptance with browser coverage.

- IDs are the only join keys; line order carries no meaning.
- Existing datasets, answers, and reports require a new path or explicit
  `--force`.
- The managed copy protects source files but is not an OS security sandbox.
- A live integration passes only when the real Agent produced the final answer
  artifact.
