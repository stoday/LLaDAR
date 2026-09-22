# Automatic project adapters

## Objective

`lladar run-agent DATASET --project PATH` discovers how to submit questions to a
Python project and observe its real answers, without requiring an entrypoint.
The implementation incorporates VIDE-TESTING's coding-agent exploration workflow.

## MVP contract

- Keep explicit `--entrypoint` and Python `answer=` callbacks compatible.
- Without an entrypoint, inspect a managed project copy and generate a standalone
  Python adapter. Do not edit target source, replace providers, or fabricate answers.
- Adapter stdin: `{request_id, message}`. stdout: exactly one JSON object with the
  matching `request_id`, a nonempty string `output`, and an `observation` explanation.
  Target logs belong on stderr. Extract the target answer without rewriting it.
- Give the coding agent at most two distinct questions, never reference answers,
  labels, or the evaluator rubric. Fix the adapter before the full dataset run.
- Independently replay every probe in a fresh copy and process. Run every dataset
  case in another fresh copy and process; correlate results to LLaDAR case IDs.
- Preserve the current three-field response JSONL and evaluation semantics. Runtime
  errors remain execution errors, not substantive answers or judge failures.
- Preserve adapter source/hash, proposal, exploration audit, verification, and
  per-request observations under `.lladar/runs/`. On preparation failure, preserve
  evidence and do not create or overwrite the requested answer artifact.
- Reuse target `.venv` or accept `--target-python`; inject environment values without
  copying `.env` files. Expose timeout and exploration tool-call budget controls.
- Controller and target dependencies live in separate Python environments. For
  both automatic and explicit project execution, missing target environments fail
  before model calls; never fall back to the controller. Probe the actual target
  `sys.prefix` and reject the controller environment or a venv exposing system
  packages. Remove inherited Python search paths and controller activation state
  before spawning the target. Dependency installation remains the project's own
  setup step; do not merge its requirements into LLaDAR.
- Empty/skipped-only datasets do not invoke a model. Existing output protection
  remains in force.

## Boundaries

Python targets and locally observable file/database/CLI/HTTP results are in scope.
Dependencies and credentials must already be available. Report ambiguous or blocked
integration instead of guessing missing credentials or installing dependencies.
Project copies are state isolation, not an OS security sandbox. Generated Python
runs with the user's privileges; only trusted target projects should be tested.
No browser login automation, distributed job lifecycle, or persistent adapter cache
in this first version. `verified` means replay succeeded, not answer correctness.

## Acceptance

Use real paid model calls for discovery, target execution, and LLaDAR evaluation;
do not substitute mocks. Exercise a message-based target and a file/database target,
inspect generated code, confirm exact case-ID alignment, preserve raw evidence,
and document observed outcomes separately from intended behavior. Verify invalid
protocol, timeout, and state isolation using real subprocesses without API mocks.
