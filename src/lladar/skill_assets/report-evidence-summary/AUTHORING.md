# Report authoring guide

Edit `SKILL.md` to change audience, tone, ordering, and emphasis. This guide
describes the host contract; its examples are references for editing, not
additional instructions to execute. New prose follows the requested language.
Measurements and tables remain owned by the host.

## Ordinary reports: sections and evidence

Load the selected Skill before using host tools. A `stage: report` request
provides frozen `facts` and `completed_sections`. Leave accepted sections in
`completed_sections` unchanged. Submit each remaining section with
`submit_section(section, text, source_refs, request_id)`:

- `section`: `overview`, `findings`, or `limitations`.
- `text`: a nonempty string grounded in the saved evaluation, without digit
  characters, including years, percentages, and numbered labels.
- `source_refs`: a nonempty list of existing fact keys, such as `summary`,
  `plan`, or `aggregates`, or trial IDs from this evaluation.
- `request_id`: a stable identifier for this submission; reuse it only when
  retrying the same payload after an uncertain result.

Example arguments, to adapt to the supplied facts:

```json
{
  "section": "overview",
  "text": "This report summarizes the saved evaluation evidence.",
  "source_refs": ["summary"],
  "request_id": "overview"
}
```

Use `read_report_fact(reference, start_char)` for more evidence. `facts` reads
the full frozen evaluation; existing top-level keys and trial IDs read specific
parts. Continue with the returned `next_start` until it is null when the evidence
is paginated. A reference must exist in this evaluation; do not infer raw
responses from aggregates.

Inspect the returned status. For `needs_revision`, repair the reported issues
with `revise_section(item_id, request_id, revision, text, source_refs)`, using
the section name as `item_id`, a new request ID, and the returned current
revision. Use `get_section(item_id)` after an uncertain outcome. Work is complete
only when all required sections are accepted. The host then checks their support.

Keep findings within the supplied samples and test conditions. Describe saved
execution errors, judge errors, and pending human review when applicable.

## Support review

For `stage: report_review`, inspect the supplied `sections` against `facts`.
Use `read_report_fact` for further evidence. Submit
`submit_section_review(judgments)` with exactly one object per required section,
each containing `section`, boolean `approved`, and a nonempty evidence-bounded
`reason`. The required sections are `overview`, `findings`, and `limitations`.
This stage checks support rather than rewriting prose. Reject unsupported claims,
hidden execution or judge errors, overgeneralization, and assertions that human
review has been completed without evidence. The host returns rejected sections
to the author with `revision_issues`; repair them with `revise_section` while
preserving accepted sections.

## Situation reports: whole narrative

Situation report requests supply `observe`, `summary`, `method`, and
`trial_evidence` facts and expose `submit_report(narrative)`. Submit exactly
three nonempty strings, `overview`, `findings`, and `limitations`, grounded in
those facts and containing no digit characters. An accepted narrative completes
this submission. Example `narrative` argument:

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the saved outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```
