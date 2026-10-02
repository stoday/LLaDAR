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
For mixed typed or semantic-probe input, the host may supply a fixed plan and
accept a judgment containing only `correct`; it fills the other dimension
with `null`.
Finish only after the submission tool accepts the plan or judgment.
