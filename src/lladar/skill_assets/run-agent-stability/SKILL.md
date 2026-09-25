---
name: run-agent-stability
description: Run every LLaDAR question three times to measure verdict stability.
---

# Stability run

Read the dataset, then write `select_cases(cases, schedule)`. Schedule every
provided case exactly once with `repeats=3`. Do not filter, invoke the target
Agent, or write files other than the requested strategy.
