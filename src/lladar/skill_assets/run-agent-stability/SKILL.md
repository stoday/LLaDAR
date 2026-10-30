---
name: run-agent-stability
description: Run every LLaDAR question three times to measure verdict stability.
---

# Stability run

## What to customize

This Skill chooses which dataset cases to run and how many times to repeat
each case. To customize it, edit the selection and repeat rules below; the
host still runs the target Agent and records its responses. `AUTHORING.md`
contains examples for running all cases, sampling, or focusing on a range.

Read the dataset, then write `select_cases(cases, schedule)`. Schedule every
provided case exactly once with `repeats=3`. Do not filter, invoke the target
Agent, or write files other than the requested strategy.
