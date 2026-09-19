# Complete public interface discovery and human confirmation

## Boundary

Test the outermost user-facing input that preserves the application's initialization,
configured services, knowledge, tools, workflow selection and final output processing.
Do not substitute an inner model call or a convenient internal function. Tools remain
available to the real agent; the adapter does not force every tool to run per question.

## Phases

1. Read-only discovery: inspect source/docs, propose public interfaces with source-line
   evidence, the complete flow, output observation and unresolved questions. No adapter
   writes, imports of target code, target execution or probe calls in this phase.
2. If exactly one evidenced public interface has no unresolved questions, select it.
   Multiple candidates or unresolved questions require human confirmation. Present
   feature labels, flow, evidence and uncertainties, not just Python symbol names.
3. Interactive terminals allow selecting an interface, providing clarification for
   another read-only discovery, or saving and leaving. EOF/interrupt preserves state.
   Non-interactive execution saves `needs_confirmation` and exits with code 3.
4. After selection, generate an adapter for that interface and independently replay it.
   Runtime/answer verification is separate from choosing the intended feature.

## Persistence and CLI

`run-agent` gains `--interactive` / `--no-interactive` (default: terminal detection)
and `--intent TEXT`. `resume-agent RUN` accepts `--candidate ID` or
`--clarification TEXT`, interaction controls and an optional environment-file override.
RUN is the managed run directory printed when paused. Persist context, source and
dataset hashes, interface proposals, decisions, clarification history and audit.
Do not persist credential values. Resume reuses the saved workspace; changed source,
workspace, dataset or an existing output causes a clear error before model execution.
Only `needs_confirmation` runs may resume. A human decision cannot waive missing
source evidence or choose an internal-only entrypoint. Explicit legacy `--entrypoint`
remains a user-directed compatibility path and does not assert boundary verification.

## Acceptance

Exercise selection, clarification, save/EOF, noninteractive exit, invalid selection,
changed-input rejection and fresh-process resume without mocked model providers.
Use a real paid API on a layered project with two public flows: discovery must pause
without invoking the target, then a selected flow must retain knowledge/tools and
outer result processing through independent replay and dataset execution. Preserve
the generated adapter and run evidence. Evidence improves confidence, not a proof
that an arbitrary project has no undiscovered entrypoints.
