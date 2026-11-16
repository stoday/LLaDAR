# Answer verdict submission reference

Follow the active evaluation method in `SKILL.md`. This reference describes the
host's accepted payloads for that method. The request's `stage` selects the
work item. Stop after its submission is accepted.

## Plan

For `stage="plan"`, pass a `plan` object to `submit_plan`. The plan has exactly
`title`, `approach`, `dimensions`, and `limitations`. Each dimension requires
`name`, `description`, and `kind`. Use `kind="boolean"` for `correct`.
`limitations` is a list of strings. A complete plan is shown below.

```json
{
  "title": "Answer correctness",
  "approach": "Compare each actual response with its expected answer, accepting equivalent wording while retaining required conditions.",
  "dimensions": [
    {
      "name": "correct",
      "description": "The response matches the expected answer and its required conditions.",
      "kind": "boolean"
    }
  ],
  "limitations": ["Only the supplied question, expected answer and actual response are evidence."]
}
```

Inspect the tool result. When it returns `status="needs_revision"`, use the
reported issues to correct the payload. Call `revise_plan` with `item_id="plan"`,
a new `request_id`, the returned current `revision`, and the complete corrected
`plan`. `get_plan` retrieves the current status after an uncertain result.
The host permits at most three repairs and freezes an accepted plan.

## Judgment

For `stage="judgment"`, use the supplied frozen plan. Pass a `judgment` object
to `submit_judgment`. It has exactly `values` and a nonempty `reason`.
`values` contains every dimension from the frozen plan, with its declared type.
For the plan above, a supported matching answer can be submitted as follows.

```json
{
  "values": {"correct": true},
  "reason": "The actual response states the expected answer with all required conditions."
}
```

Set `correct` to `false` when the answers disagree and explain the concrete
mismatch. Use `null` with an explanation when the supplied evidence is
insufficient. Judge the actual response in the assigned trial. The host
calculates aggregate statistics.

When `actual_response` is null and `response_read` is present, read the full
captured answer with `read_trial_response(trial_id, start_char)`, following
`next_start`. Use the assigned `trial_id` and `plan_version` when submitting.
For rejected judgments, call `revise_judgment` with the assigned item ID, a new
request ID, the returned revision, and the complete corrected `judgment`.
Use `get_judgment` to inspect an uncertain submission.

## Situation judgment

For `stage="situation_judgment"`, follow the request's `judgment_contract` and
the situation rule in `SKILL.md`. This answer-correctness method has no
situation standard. Submit indeterminate validity and behavior, an empty
`evidence_turn_ids` list, and a reason explaining the missing situation
standard.
