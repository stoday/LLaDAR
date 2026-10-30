---
name: eval-answer-verdict
description: Judge each LLaDAR response for answer correctness.
---

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
