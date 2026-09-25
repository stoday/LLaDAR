---
name: run-agent-random-sample
description: Deterministically sample five LLaDAR questions for a quick randomized run.
---

# Random sample run

Read the dataset, then write `select_cases(cases, schedule)`. Use the provided
`random.sample` helper to select `min(5, len(cases))` cases, and schedule each
selected case once. Never use another random source. Do not invoke the target
Agent or write files other than the requested strategy.
