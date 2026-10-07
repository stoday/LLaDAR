---
name: eval-answer-verdict
description: Judge each LLaDAR response for answer correctness.
---

# Answer verdict

On stage `plan`, submit a plan with a boolean `correct` dimension. On stage
`judgment`, use the frozen plan and compare only the expected answer and actual
response, then submit one grounded judgment. Do not calculate aggregate statistics.

On `situation_judgment`, this answer-correctness method has no situation standard.
Submit validity and behavior as indeterminate, evidence_turn_ids as an empty
list, and a reason explaining that a complete situation standard must be defined
in a custom Skill. Use the request's judgment_contract; there is no plan tool.
