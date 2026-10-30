# Situation authoring reference

Use the active method in `SKILL.md`. For each request, compile the supplied
instructions and stopping rule into a situation configuration, then call
`submit_situation(value)` and finish after it accepts the proposal.

`value` contains `observable_conditions`, `fixed_constraints`, `variation_axes`,
`generation_method`, `run_method`, `evaluation_method`, and `rubric`. Each
method has nonempty `id`, `version`, and `instructions`. Each variation axis has
a unique `id` and at least two distinct `values`. Each rubric entry has a unique
`id`, `observed_when`, and `invalid_when`.

Choose observable conditions and variations relevant to the user's goal. The
generation method produces a scenario and its first user message. The run method
describes any follow-up conversation. For `max_turns` of 1, a scenario contains
one target response and no follow-up is needed. The evaluation method judges the
saved target response or transcript against the rubric and cites turn IDs.

Knowledge, when supplied, is evidence for generation; it is not automatically
loaded into the target. If `submit_situation` reports validation errors, correct
the proposal and submit again.
