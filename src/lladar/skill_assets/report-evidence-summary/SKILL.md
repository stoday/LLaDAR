---
name: report-evidence-summary
description: Write an evidence-bounded summary of a saved LLaDAR evaluation.
---

# Evidence summary

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
