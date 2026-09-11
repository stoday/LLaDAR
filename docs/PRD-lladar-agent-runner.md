# LLaDAR schema-v2 agent runner

## Goal

`lladar run-agent` executes every runnable case in a schema-v2 question-group
dataset and writes the observed-answer artifact consumed by `lladar eval`:

```text
lladar create test-dataset -> lladar run-agent -> lladar eval
```

The generated dataset remains immutable: reference answers exist only on
original cases, while observed Agent answers are stored in a separate JSONL.

## Inputs and expansion

The runner accepts schema version `2` only. It validates the entire dataset
before copying a project, adapting code, calling an Agent, or creating the
answer file. Schema-v1 input fails with an instruction to regenerate it.

Each `ready` group expands in source order to independent sessions:

1. the original case, identified by the group `id`;
2. the single `information_omission` variant;
3. every `peer_cue_addition` variant.

`skipped` groups remain in the dataset for coverage accounting but schedule no
sessions. Group and variant IDs must be non-empty and globally unique.

## Observed-answer schema

Every attempted session writes one UTF-8 JSONL record. Successful execution:

```json
{
  "schema_version": 2,
  "id": "group-abc-variant-def",
  "group_id": "group-abc",
  "kind": "peer_cue_addition",
  "question": "Which plan applies to my grandmother?",
  "status": "ok",
  "answer": "The Agent's unmodified response"
}
```

Failed execution uses `status: "execution_error"` and a non-empty `error`
instead of `answer`. A failure is recorded and later sessions continue. The
runner never judges, repairs, summarizes, or invents an Agent response.

`id` is the join key. `group_id`, `kind`, and `question` are copied into the
record so the evaluator can detect stale or mismatched artifacts without
depending on line order.

## Python API

```python
import lladar

completed_sessions = lladar.run_agent(
    "test-dataset.jsonl",
    "qa-results.jsonl",
    answer=lambda question: project_agent(question),
)
```

The return value is the number of sessions with `status: "ok"`.

## CLI and project boundary

```powershell
lladar run-agent .\test-dataset.jsonl `
  --project .\example_project `
  --entrypoint .\example_project\main.py `
  --output .\qa-results.jsonl
```

`--entrypoint` may be project-relative (`main.py`) or a path inside the
original project (`.\example_project\main.py`, including an absolute path).
Paths outside the project fail before a managed copy is created.

Project mode:

- resolves the original project's Python interpreter before copying;
- copies source to `.lladar/runs/<run-id>/<project-name>` without `.env`, Git,
  virtual environments, caches, or nested `.lladar` state;
- uses the copied entrypoint directly when it already reads
  `LLADAR_QUESTION`;
- otherwise asks the root-confined Akasha adapter to make the smallest change
  in the copy and verifies the question-input seam;
- starts one child process per case with `LLADAR_QUESTION` set;
- preserves the original project unchanged and retains the managed copy for
  inspection.

The managed copy is a source-safety boundary, not an OS security sandbox. The
child process retains the host permissions and network access granted to it.

Progress and sanitized error types go to stderr. The answer file is never
overwritten unless `--force` is supplied.

## Non-goals

- Multi-turn conversations or clarification follow-ups.
- Changing the evaluated Agent's provider, knowledge, or answer logic.
- Treating repeated hard-coded output as a real answer seam.
- Evaluating correctness or cue sensitivity in the runner.
- Sandboxing untrusted code at the OS or container level.

## Acceptance criteria

1. Every ready original and variant schedules exactly one isolated session.
2. Every output record carries the stable ID and copied case metadata.
3. Skipped groups schedule no sessions.
4. Legacy schemas and duplicate IDs fail before Agent execution.
5. Per-session execution failures remain visible without stopping later cases.
6. Both project-relative and project-prefixed entrypoint paths resolve only
   inside the original project.
7. Compatible entrypoints are not adapted; incompatible ones are adapted only
   in the managed copy.
8. The output is accepted directly by the schema-v2 evaluator.
