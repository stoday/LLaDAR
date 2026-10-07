## Submission contract

The request has a `stage` field. On `plan`, call `submit_plan` with exactly this
object shape, then stop this work item:

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
Call `submit_judgment` with exactly `values` and a nonempty `reason`. For this
plan, use
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
