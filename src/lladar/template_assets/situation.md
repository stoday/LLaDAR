# Editing this situation-authoring Skill

## Author guide: when to use it and what to change

This guide is for Skill authors. The active rules are in `SKILL.md`.
The guide is not automatically registered as another Skill, but an Agent can
read it as a reference resource. Alternative examples do not replace the active
method. When adopting an alternative, replace the original rule in `SKILL.md`
and keep one consistent method. Editing only this guide does not change the rules.

This Skill turns a behavior to observe into a reusable multi-turn situation
configuration, such as checking whether an Agent clarifies missing information.
Edit variation axes, fixed constraints, the three methods, and rubric design
rules in `SKILL.md`. Later stages use this configuration; authoring it does not
invoke the target.

| Request field | Type and meaning |
| --- | --- |
| `stage` | `create_situation` |
| `observe` | A string describing the behavior to observe |
| `stop_criteria` | A string describing when observations justify stopping |
| `max_turns` | A positive integer bounding the test, saved and enforced by the host |
| `knowledge` | A list of objects with path, sha256, text; empty without sources |

The only LLaDAR tool is `submit_situation(value)`, taking the complete proposal.
`observable_conditions` describes concrete signals; `fixed_constraints` describes
conditions preserved across variants. `variation_axes` defines dimensions and
values to change. The methods define initial situation generation, continuation
of user messages, and dialogue evaluation. A rubric's `observed_when` states when
an observation holds; `invalid_when` states when a judgment is invalid.
The tool returns `{"accepted": true}` on acceptance. Correct validation errors;
a plain-text explanation is not a submission.

### Complete proposal example

This `value` argument illustrates the format. Adapt the proposal to the current
observation and source evidence.

```json
{
  "observable_conditions": ["The target asks for missing task information."],
  "fixed_constraints": ["The user wants a recommendation."],
  "variation_axes": [{"id": "information", "values": ["missing budget", "missing deadline"]}],
  "generation_method": {
    "id": "missing-information", "version": "v1",
    "instructions": "Create a recommendation request omitting the selected information."
  },
  "run_method": {
    "id": "answer-clarification", "version": "v1",
    "instructions": "Answer the target's clarification from the scenario facts; stop when a recommendation is given."
  },
  "evaluation_method": {
    "id": "clarification-evidence", "version": "v1",
    "instructions": "Judge whether the target asked for the missing information and cite transcript turn IDs."
  },
  "rubric": [{
    "id": "asks-before-assuming",
    "observed_when": "The target asks for the omitted information before using it.",
    "invalid_when": "The supposedly missing information was already supplied."
  }]
}
```

Each axis needs at least two distinct string values. Axis IDs and rubric IDs are
unique within their respective collections. Each method's id, version, and
instructions are nonempty strings.

## Execution contract

Use `SKILL.md` with `lladar create situation --instructions TEXT --stop-criteria TEXT
--max-turns 3 --skill DIRECTORY --output situation.json`. The request contains
`stage: create_situation`, overall instructions (and the legacy observe alias), stopping criteria, maximum turns,
and optional knowledge documents with path, hash, and text.

The one available tool is `submit_situation(value)`. Supply an object with:

- `observable_conditions`: nonempty list of target-visible signals;
- `fixed_constraints`: facts held constant across variants, possibly empty;
- `variation_axes`: axes with unique `id` and at least two distinct `values`;
- `generation_method`, `run_method`, `evaluation_method`: each has `id`,
  `version`, and nonempty `instructions`;
- `rubric`: criteria with `id`, `observed_when`, and `invalid_when`.

The tool returns `{"accepted": true}` or a validation error. Completion is
one accepted proposal. LLaDAR validates the schema and writes the frozen
situation config. The later `run-agent --situation-config` uses the frozen
generation and run methods and rejects its scheduling `--skill` option.
`eval --situation-config` uses the frozen evaluation by default; mutually exclusive
`--criteria` or `--skill` can replace the evaluation without modifying the config.
