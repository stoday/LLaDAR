# Editing this evaluation Skill

## Author guide: purpose and where to edit

This guide is for Skill authors. The active rules are in `SKILL.md`.
The guide is not automatically registered as another Skill, but an Agent can
read it as a reference resource. Alternative examples do not replace the active
method. When adopting an alternative, replace the original rule in `SKILL.md`
and keep one consistent method. Editing only this guide does not change the rules.

Use `eval --criteria TEXT` for ordinary evaluation requirements. Use this complete
Skill instead when customizing the evaluation workflow; criteria and Skill are
mutually exclusive. An explicit method evaluates typed/probe responses too.
Use this Skill to customize judgments: accept equivalent wording,
require important conditions, or also judge concision. Edit the judgment rules
and submission contract in `SKILL.md`. When adding dimensions, update both the
plan and judgment fields.

### Requests, tools, and parameters

| Stage | Request data | Complete LLaDAR tool list for this stage |
| --- | --- | --- |
| `plan` | `records` is a list of up to twenty-five completed trials | `submit_plan(plan)` submits the evaluation-method object |
| `judgment` | Current trial fields at the top level, plus the frozen `plan` | `submit_judgment(judgment)` submits values and a reason; the host validates against its saved plan |
| `situation_judgment` | `scenario`, `turns`, `generation` constraints, `judgment_contract` | `submit_judgment(value)` submits validity, behavior, evidence_turn_ids and reason |

Records contain `question` (test question), `expected_answer` (source-supported
answer), `actual_response` (target text), `record_index`, and `trial` identifiers.
Arguments are objects, not file paths. A plan defines shared rules; a judgment
is one trial's result. Both tools return `{"accepted": true}` or a validation
error; correct invalid input and resubmit.

### Complete submission examples

The `plan` argument to `submit_plan(plan)`:

```json
{
  "title": "Answer correctness",
  "approach": "Accept equivalent wording while requiring all answer conditions.",
  "dimensions": [
    {"name": "correct", "description": "Matches the expected answer including required conditions.", "kind": "boolean"}
  ],
  "limitations": ["Only the supplied question, expected answer and actual response are evidence."]
}
```

The `judgment` argument to `submit_judgment(judgment)`:

```json
{"values": {"correct": true}, "reason": "The response states the expected answer with all required conditions."}
```

Boolean dimensions take true/false; categorical dimensions take strings; numeric
dimensions take numbers. Use null with a reason when evidence is insufficient.
`values` keys must match the frozen plan; do not add dimensions independently
for each trial. After adding a boolean `concise` dimension, each judgment must
include both `correct` and `concise`. The host calculates totals, rates, stability.

## Execution contract and special input modes

The active instructions are in `SKILL.md`. Use the skill with
`lladar eval RESPONSES --skill DIRECTORY --output EVALUATION.json`.

## Agent work

When a complete Skill is explicitly selected, the agent first receives a
`plan` request with up to 25 completed records. Call `submit_plan(plan)` with exactly `title`, `approach`,
`dimensions`, and `limitations`. Each dimension has `name`, `description`, and
`kind` (`boolean`, `categorical`, or `numeric`). Include a boolean `correct`
dimension only when the method assesses answer correctness. The tool returns
`{"accepted": true}` or a validation error.

For each assigned `judgment` request, follow this Skill's standards and the supplied
frozen plan. Use `expected_answer` when relevant to those standards.
Call `submit_judgment(judgment)` with `values` for every
planned dimension and a nonempty `reason`. The tool returns
`{"accepted": true}` or a validation error. Complete one accepted judgment
per completed trial.

With no explicit criteria or Skill, LLaDAR judges typed answer protocols and verified semantic probes in code.
It calculates coverage, aggregates, and stability from the saved judgments.
An execution error has no response to judge. Situation evaluation with
`--situation-config` uses its frozen method by default; an explicit Skill replaces
the evaluation standards without changing that original config.

For situation input, define the full observation standards in this Skill, then
handle `situation_judgment` by calling `submit_judgment` with exactly `validity`
(valid/invalid/indeterminate), `behavior` (observed/not_observed/indeterminate),
`evidence_turn_ids` (existing turn IDs), and nonempty `reason`. Observed behavior
requires cited evidence. Preserve fixed scenario constraints and declared
variation when assessing validity. The host validates the judgment and computes
counts. This stage has no submit_plan tool and supplies no original evaluation
rubric, so the Skill must contain its own standards. The answer-correctness
template requires customization before it can assess a situation observation.

Start with the bundled instructions in `SKILL.md`, then change one judgment
rule at a time and compare the saved evaluation results.
