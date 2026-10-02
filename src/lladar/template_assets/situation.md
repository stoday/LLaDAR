# Editing this situation-authoring Skill

Use `SKILL.md` with `lladar create situation --observe TEXT --stop-criteria TEXT
--max-turns 3 --skill DIRECTORY --output situation.json`. The request contains
`stage: create_situation`, observation text, stopping criteria, maximum turns,
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
situation config. The later multi-turn `run-agent --situation-config` and
`eval --situation-config` modes use that frozen method; they do not accept the
ordinary `run-agent` or `eval` `--skill` option.
