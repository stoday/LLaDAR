# LLaDAR agent runner

## Goal

Connect the existing LLaDAR workflow so a generated contrastive dataset can be
sent to the project agent automatically and produce the answer artifact needed
by `lladar eval`:

```text
lladar create test-dataset -> lladar run-agent -> lladar eval
```

The project agent's source code should not need to be modified.

## Important boundary

LLaDAR cannot generically inject a question into an arbitrary executable. A
script that accepts a question through a CLI argument, standard input, or an
existing callable has an observable invocation seam. A script such as
`example_project/main.py`, which creates an Akasha agent at import time and
passes a hard-coded question, has no framework-neutral input seam.

The runner therefore supports explicit invocation seams and reports an
actionable error when none is available. It must not run the same hard-coded
question once per dataset item and label those repeated answers as real test
results.

## Proposed MVP

### Akasha integration

Use `akasha-terminal` as the adaptation controller because LLaDAR already uses
it for dataset generation and evaluation judging. The controller should use
`akasha.agents()` with LLaDAR-owned, root-confined tools to inspect the copied
project and propose or apply the smallest adapter change.

Akasha is responsible for reasoning about the project structure and selecting
an input seam. LLaDAR remains responsible for deterministic file boundaries,
patch application, process execution, output capture, question-injection
verification, and JSONL writing. The controller must not receive unrestricted
filesystem or shell access, and it must not operate on the original project.

The initial tool bundle can expose directory listing, UTF-8 file reading,
literal/regex search, Python symbol location, sandbox-only patching, and a
constrained candidate-run operation. Skills or MCP may package these tools,
but they do not replace LLaDAR's path and process policy.

### Public API

Add a runner API with a question callback:

```python
count = lladar.run_agent(
    "test-dataset.jsonl",
    "qa-results.jsonl",
    answer=lambda question: project_agent(question),
)
```

The runner reads `underspecified_question`, calls the supplied answerer once
per item, and writes UTF-8 JSONL. Each record preserves the dataset `id` and
contains either:

```json
{"id":"item-001","status":"ok","answer":"actual response"}
```

or an explicit runtime error:

```json
{"id":"item-001","status":"error","error":"..."}
```

Errors are recorded per item so later items can run. The runner never invents,
rewrites, summarizes, or judges an agent answer.

### CLI

`run-agent` is an opinionated workflow. These behaviors are mandatory and are
not user-selectable flags:

- create a managed workspace under `.lladar/runs/<run-id>/` by copying the target project;
- copy the target project without modifying the original;
- auto-adapt only the managed copy when an input seam is missing;
- run each dataset item in an independent process;
- verify that the current dataset question reached the agent before accepting
  its output.

The MVP's workspace isolation is not a security sandbox. It protects the
original source tree from edits and keeps each test process separate, while
retaining the copied workspace under `.lladar/runs/` for inspection and
cleanup. The copied agent may still access whatever filesystem, network,
credentials, and process permissions the host grants it. A hardened
OS/container sandbox is a future security feature for untrusted projects and
is not required for the initial runner.

The user supplies only the dataset, project, entrypoint, and answer output:

```powershell
lladar run-agent test-dataset.jsonl `
  --project example_project `
  --entrypoint main.py `
  --output qa-results.jsonl
```

The runner captures the agent answer, sends progress to stderr, and writes the
id-keyed answer JSONL. If the copy cannot be created, or the adapter cannot
prove question injection, the item/run fails clearly instead of falling back
to the original hard-coded question.

### Validation and safety

- Match and preserve dataset IDs; never join by line number.
- Refuse an existing answer output unless `--force` is supplied.
- Keep stdout machine-readable for command mode; progress goes to stderr.
- Do not expose environment files, credentials, cookies, or raw provider errors
  in progress output.
- Distinguish agent execution errors from evaluator judgment errors.
- Validate the answer artifact before evaluation.

## `example_project/main.py`

The current file is not directly compatible with the generic command mode: it
does not accept the dataset question and executes during import. To test this
unchanged file, provide a project-local wrapper outside `main.py` that exposes
the question callback, or use an explicitly opt-in Akasha-specific legacy
bridge that intercepts the `akasha.ask` call. The bridge must be clearly
labelled provider-specific, run each item in isolation, and fail closed if it
cannot confirm that the question was replaced.

The generic runner may perform controlled source rewriting in the managed
copy, but must not rewrite the original project or rely on unconstrained AST
guessing. Every generated change must be validated against the observed agent
call and included in a diagnostic artifact when the run fails.

The copy operation must exclude secrets and unnecessary state such as `.env`,
`.venv`, `.git`, caches, cookies, and browser profiles. Required provider
configuration is injected into the child process through the normal secure
environment mechanism.

## Non-goals for MVP

- Automatically modifying or rewriting the user's agent.
- Inferring a question input point from arbitrary source code.
- Supporting every framework's conversation/session lifecycle.
- Automatically retrying or changing an agent after a failed evaluation.
- Combining runner and judge prompts; the runner sends the dataset question,
  while `lladar eval` owns the evaluation rubric.

## Acceptance criteria

1. A callable answerer can process every dataset item and emit one id-keyed
   result per item.
2. A command template can process a real external agent without changing that
   agent's source.
3. Missing IDs, duplicate IDs, empty answers, and process failures are visible
   before or in the evaluation flow.
4. The generated `qa-results.jsonl` can be passed directly to `lladar.eval`.
5. Tests cover successful answers, per-item failures, output protection,
   placeholder validation, and ID preservation.
6. Documentation shows the complete create -> run-agent -> eval workflow and
   explains why a hard-coded script needs a wrapper or an explicit bridge.
