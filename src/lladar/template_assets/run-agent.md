# Editing this run-agent scheduling Skill

Use `SKILL.md` with `lladar run-agent DATASET.jsonl --project PROJECT --skill
DIRECTORY --output responses.jsonl`. This Skill selects cases and repeat
counts. The target Agent still supplies the actual responses.

The request has `stage: select_cases`. Call `read_dataset()` first; it returns
each numbered question and expected answer as text. Then call
`write_strategy(content)` with Python source defining
`select_cases(cases, schedule)`. The host checks that the source compiles and
runs it with supplied `cases`, `schedule`, and deterministic `random` helper.
Each case has `record_index`, `question`, and `expected_answer`.
Call `schedule(case, repeats=1)` for each selected case; repeats must be a
positive integer. Complete one valid strategy that schedules the intended
cases. The host then invokes the target Agent and records trials.

The bundled `run-agent-stability` method has a trusted `strategy.py` that the
host executes without a scheduling model call. A generated custom Skill lives
at a different path, so it uses the Skill agent and `write_strategy` tool.
Copying `strategy.py` does not enable the bundled fast path. Keep target
execution and adapter discovery separate from scheduling instructions.
