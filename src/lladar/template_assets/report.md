# Editing this report-writing Skill

## Edit only Custom requirements to start

Open `SKILL.md` and replace the text between **Custom requirements (edit here)** and **Execution contract (advanced)**. The template already contains a working default. Describe the result you want, its conditions and, if useful, a matching and non-matching example. No Python or JSON is needed for ordinary policy edits.

Replacement example: “Write for a support manager; prioritize major issues, representative cases and actionable improvements.”

Example of the distinction: Pointing out a missed refund condition with evidence fits; claiming universal reliability without evidence does not.

Replace the existing policy rather than appending a competing rule. Keep the execution contract, tool names and fields intact. Advanced examples below are references, not additional instructions; editing only AUTHORING.md does not change the active policy.

### Check a small run

Use a small source or dataset first and a fresh output path. Read the saved results and compare them with your matching/non-matching examples. For execution, check selected cases and repeat counts in the run sidecars; for evaluation or reporting, reuse saved responses or evaluation facts. Template creation is offline; stage execution can call models and the target. Natural-language requirements guide behavior; they do not guarantee coverage or counts beyond host validation.

## Advanced reference


## Start here

Edit the prose policy in `SKILL.md` when changing the report audience, tone,
ordering, or emphasis. For example, ask for a Traditional Chinese report that
prioritizes execution failures and evidence limitations. Then run:

Run `lladar report EVALUATION.json --skill ./my-report --output report.md` after editing.

The host owns measurements and tables. The Skill should interpret supplied
facts, explain limitations, and avoid inventing numbers.

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
