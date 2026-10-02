# Editing this report-writing Skill

Use `SKILL.md` with
`lladar report EVALUATION.json --skill DIRECTORY --output report.md`.
The agent receives `stage: report` and saved `plan`, `summary`, `aggregates`,
and `stability` facts from the evaluation.

Call `submit_report(narrative)` once with exactly three nonempty strings:
`overview`, `findings`, and `limitations`. The tool returns
`{"accepted": true}` or a validation error. Keep prose grounded in the
supplied facts and free of numeric claims; the host rejects prose containing
digits. Completion is one accepted narrative.

LLaDAR renders counts, rates, distributions, stability, question-type tables,
and the trial appendix from saved evaluation data. The Skill changes the
interpretation and wording, not those measurements or tables.
