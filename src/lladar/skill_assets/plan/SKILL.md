---
name: plan
description: Clarify a testing goal and fill the host-supplied workflow profile as a draft PlanSpec.
---

# Test planning conversation

Use the selected language and the host's profile registry, state and schema.
Translate technical terms into concrete behavior the user can observe. Resolve
the goal before selecting a workflow. Choose document_qa for source-supported
questions, behavior_situation for knowledge-optional behavior, and existing
responses/evaluation profiles only when the user already has those artifacts.

Ask one missing decision at a time, using its field path. Include a brief recap
and, when useful, two to four different choices. Reuse supplied answers and
safe inferences in `known`; stop asking when the requested scope is complete.
Use one target turn for one response. Ask stopping/max-turn questions only for
situation workflows. Creation-only plans need no target. Existing responses
need no creation or target questions. Match stages to the user's requested scope.

When the goal changes, set `known.user_goal` to the revised goal and rebuild
the candidate. The host invalidates the old decisions. Omit withdrawn fields.

Ask what evidence exists for payments, delivery, database updates or completed
actions. Text supports conclusions about what the Agent said. If external
state verification is still requested, set `observation.kind: external_state`;
the host blocks that claim. When the user accepts a narrower observable goal,
record the limitation and use `text`.

Return exactly one JSON object:

- Question: `kind: question`, `field` (one profile/schema field), `context`,
  `message`, `options` (strings or objects with label, description, optional
  type: free_input), optional `recommended` (zero-based), `known` (partial
  PlanSpec), `assumptions` and `open_questions` (string arrays).
- Candidate: `kind: complete`, `plan` (a complete PlanSpec conforming to the
  supplied schema, with status: draft).

The host owns final status, commands, generated artifact paths and validation.
Supply schema fields, never CLI argv or scripts. A missing file is a missing
input. Do not request secrets, cookies or tokens, call targets, execute
workflows, or author Skills. Return the best draft with open_questions when
a required input is unavailable; the host saves an actionable blocked package.
