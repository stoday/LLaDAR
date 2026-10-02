# Editing this evaluation Skill

The active instructions are in `SKILL.md`. Use the skill with
`lladar eval RESPONSES --skill DIRECTORY --output EVALUATION.json`.

## Agent work

When all completed trials use free responses, the agent first receives a
`plan` request with up to 25 completed records. Call `submit_plan(plan)` with exactly `title`, `approach`,
`dimensions`, and `limitations`. Each dimension has `name`, `description`, and
`kind` (`boolean`, `categorical`, or `numeric`). Include a boolean `correct`
dimension. The tool returns `{"accepted": true}` or a validation error.

When typed or semantic-probe trials are present, LLaDAR supplies a fixed plan
instead. For each assigned free-response `judgment` request, compare `expected_answer` with
`actual_response`. Call `submit_judgment(judgment)` with `values` for every
planned dimension and a nonempty `reason`. In a mixed dataset, submitting only
`correct` lets the host fill the typed/probe-only dimension with `null`. The tool returns
`{"accepted": true}` or a validation error. Complete one accepted judgment
per free-response trial.

LLaDAR judges typed answer protocols and verified semantic probes in code.
It calculates coverage, aggregates, and stability from the saved judgments.
An execution error has no response to judge. Situation evaluation with
`--situation-config` has its own frozen method and cannot use `--skill`.

Start with the bundled instructions in `SKILL.md`, then change one judgment
rule at a time and compare the saved evaluation results.
