---
name: eval
description: Compare Agent responses with expected answers, apply custom correctness criteria, and explain correct, incorrect or indeterminate judgments.
---

## Custom requirements (edit here)

For ordinary edits, rewrite only Active evaluation rules below in everyday language. Describe what counts as correct or incorrect and keep one consistent policy. Keep tool names, fields and the execution contract intact. AUTHORING.md covers small-run checks and advanced changes.

### Active evaluation rules

Accept equivalent meaning while requiring necessary conditions, quantities and units. Treat unsupported additions as incorrect. When evidence is insufficient, use indeterminate with an explanation.

### Replacement examples (reference only; inactive)

These examples help authors write replacement text; they are not active evaluation instructions. To adopt one, rewrite it under Active evaluation rules and replace the existing policy. Unadopted examples do not affect judgments.

- **Equivalent wording:** “Accept equivalent meaning when all necessary conditions are present.” For expected “Weekday tickets cost one hundred”, “Monday to Friday tickets cost one hundred” is acceptable.
- **Complete conditions:** “Require correct dates, amounts and applicability conditions; treat missing conditions that change applicability as incorrect.” For that expected answer, “Every day costs one hundred” fails.
- **Unsupported additions:** “Allow paraphrasing, but reject unsupported offers or promises.” An answer adding “Free shuttle included” when the expected answer only states the fare fails.

## Execution contract (advanced)

Follow the custom requirements within the host tool and evidence constraints below. Keep tool names, fields and submission rules compatible.


## About this template

The method below is the active default. Authors can find tool contracts, parameter definitions, and replacement examples in [AUTHORING.md](AUTHORING.md). That file is an author guide: its alternative examples are not additional execution instructions. Execute the active method in this SKILL.md.


# Answer verdict

## What to customize

This Skill defines how an individual response is judged. Edit the plan and
judgment rules when your project needs a different definition of correctness,
equivalent wording, required conditions, or additional dimensions.
`AUTHORING.md` shows the edit loop and valid submission shapes. The host keeps
aggregate statistics; the Skill should judge one trial at a time.

On stage `plan`, submit a plan with a boolean `correct` dimension. On stage
`judgment`, use the frozen plan and compare only the expected answer and actual
response, then submit one grounded judgment. Do not calculate aggregate statistics.

On `situation_judgment`, this answer-correctness method has no situation standard.
Submit validity and behavior as indeterminate, evidence_turn_ids as an empty
list, and a reason explaining that a complete situation standard must be defined
in a custom Skill. Use the request's judgment_contract; there is no plan tool.


On single-question plan/judgment stages, inspect submission status. A rejected
format can be repaired with revise_plan or revise_judgment using item_id,
a new request_id, current revision and the corrected payload; at most three
repairs are permitted. Use get_plan/get_judgment to query uncertain requests.
An accepted plan is frozen and must not be changed. Use the assigned trial_id
and plan_version; request IDs are scoped to that item. When actual_response is
null with a response_read manifest, it is a long captured answer, not an
execution failure: use read_trial_response(trial_id, start_char), following
next_start until the full answer has been read before submitting a judgment.

## Submission contract

The request has a `stage` field. On `plan`, call `submit_plan` using the custom requirements for approach and dimension descriptions. After acceptance, stop this work item. The default object shape is:

```json
{
  "title": "Answer correctness",
  "approach": "Compare each actual response with its expected answer.",
  "dimensions": [
    {"name": "correct", "description": "The response matches the expected answer.", "kind": "boolean"}
  ],
  "limitations": ["Only the supplied response and expected answer are judged."]
}
```

On `judgment`, use the supplied `expected_answer` and `actual_response`.
Call `submit_judgment` with exactly `values` and a nonempty `reason`. Apply Custom requirements and the frozen plan when choosing values and reasons. For the default dimension, the submission shape is
`{"values": {"correct": true}, "reason": "The response gives the expected answer."}`
when supported, or set `correct` to `false` and explain the mismatch.
If the evidence does not determine the answer, use `null` with a reason.
An explicitly selected Skill evaluates typed and semantic-probe responses using
its own plan as well. Use the supplied frozen plan for all judgments.
Finish only after the submission tool accepts the plan or judgment.

For `situation_judgment`, customize this Skill with full observation standards
before use. Submit exactly validity, behavior, evidence_turn_ids and reason,
following judgment_contract. Cite actual turn IDs and preserve generation
constraints. If this answer-correctness template is used without defining a
situation standard, submit validity and behavior as indeterminate, an empty
evidence_turn_ids list, and explain that no situation standard was defined.
