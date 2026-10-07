# Editing this report-writing Skill

## Author guide: purpose, tools, and customization

This guide is for Skill authors. The active rules are in `SKILL.md`.
The guide is not automatically registered as another Skill, but an Agent can
read it as a reference resource. Alternative examples do not replace the active
method. When adopting an alternative, replace the original rule in `SKILL.md`
and keep one consistent method. Editing only this guide does not change the rules.

Edit the prose rules in `SKILL.md` to change the audience, tone, emphasis, and
presentation of limitations. For example: "Explain results to a product owner
in Traditional Chinese, prioritizing execution failures and evidence limitations."
The host generates statistics and tables; the model need not recalculate them.

| Interface | Data shape and purpose |
| --- | --- |
| Request `stage` | `report`, requesting one narrative |
| Request `facts` | Ordinary evaluations contain `plan`, `summary`, `aggregates`, `stability`; situation evaluations contain `observe`, `summary`, `method`, `trial_evidence` |
| Only LLaDAR tool: `submit_report(narrative)` | Exactly `overview`, `findings`, `limitations`, each a nonempty string |

`overview` introduces the report, `findings` interprets results, and `limitations`
describes the bounds of evidence. The tool returns `{"accepted": true}` or a
validation error. One accepted submission completes the work. Prose must contain
no digit characters, including years, percentages, and numbered labels; the host
renders saved numbers as tables. Ordinary requests omit individual raw responses,
so the model cannot quote cases from information it did not receive.

Complete `narrative` argument to `submit_report(narrative)`; adapt prose to actual facts:

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```

## Execution contract

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
