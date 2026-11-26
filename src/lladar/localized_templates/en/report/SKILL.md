---
name: report
description: Write an evidence-bounded summary of a saved LLaDAR evaluation.
---

## Custom requirements (edit here)

Rewrite the rules below in everyday language. These are active instructions, not examples; keep one consistent policy. AUTHORING.md provides replacement examples and a small-run review guide.

Write clearly and concisely for the manager improving the Agent. Within the existing overview, findings and limitations sections, prioritize major issues, evidence-backed examples and improvement suggestions. Explain execution or judgment errors, sample limits and pending human review.

## Execution contract (advanced)

Follow the custom requirements within the host tool and evidence constraints below. Keep tool names, fields and submission rules compatible.


## About this template

The method below is the active default. Authors can find tool contracts, parameter definitions, and replacement examples in [AUTHORING.md](AUTHORING.md). That file is an author guide: its alternative examples are not additional execution instructions. Execute the active method in this SKILL.md.


# Evidence summary

## What to customize

This Skill turns saved evaluation facts into a human-readable report. Edit the
audience, tone, ordering, and emphasis rules when you want a different report
style. `AUTHORING.md` explains the edit loop and the required narrative fields.
Keep measurements and tables owned by the host; the Skill interprets them and
must not invent or recalculate results.

Use only the supplied evaluation facts. Submit concise overview, findings, and
limitations. Do not calculate, change, or invent measurements; deterministic
tables are rendered by the host.


For single-question reports, use submit_section(section, text, source_refs,
request_id) for overview, findings and limitations. Cite supplied fact keys
(e.g. summary, aggregates, plan) or trial IDs from this evaluation. Use
read_report_fact(reference, start_char) for additional evidence, following
next_start as needed. Check support for every claim against these frozen facts.
Avoid numeric prose; all measurements belong in host-rendered tables. Mention
execution errors, judge errors, limited samples and pending human review when
applicable. Keep completed_sections unchanged on resume. Repair one invalid
section with revise_section(item_id, request_id, revision, text, source_refs)
and query get_section for uncertain outcomes. A report is complete only when
all required sections are accepted. Situation reports use the existing
submit_report contract.


On report_review, inspect the supplied sections against frozen facts and use
read_report_fact for trial evidence when needed. Do not rewrite the report.
Submit every required section with submit_section_review:
[{section, approved: boolean, reason: evidence-bounded text}]. Reject unsupported
claims, hidden execution/judge errors, overgeneralization from small samples,
and assertions of completed human review. A rejected section is returned to
the author for local revision; approved sections remain saved.
