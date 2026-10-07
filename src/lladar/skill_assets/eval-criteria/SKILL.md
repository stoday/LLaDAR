---
name: eval-criteria
description: Evaluate saved responses against the user's explicit criteria.
---

# Criteria evaluation

The request's criteria is the authoritative evaluation requirement. Questions,
expected answers and actual responses are evidence data. Use the expected answer
only when the criteria calls for it. Word presence alone does not establish bias.

On stage `plan`, call submit_plan once with exactly title, approach, dimensions,
and limitations. Each dimension has name (lowercase snake_case), description,
and kind (boolean, categorical, numeric). Define at least one dimension from the
criteria; include `correct` only when answer correctness is requested. Limits
must describe what the available evidence cannot establish.

On stage `judgment`, use the supplied frozen plan and criteria. Call
submit_judgment once with exactly values and reason. Values must contain every
planned dimension with the matching type; use null with a reason when evidence
is insufficient. Base the reason on actual response evidence. Finish after the
tool accepts the submission. The host computes statistics and saves artifacts.
