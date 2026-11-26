---
name: run-agent
description: Choose LLaDAR dataset cases and repeat counts for execution.
---

## Custom requirements (edit here)

Rewrite the rules below in everyday language. These are active instructions, not examples; keep one consistent policy. AUTHORING.md provides replacement examples and a small-run review guide.

Run all dataset cases, repeating each case three times.

## Execution contract (advanced)

Follow the custom requirements within the host tool and evidence constraints below. Keep tool names, fields and submission rules compatible.


## About this template

The method below is the active default. Authors can find tool contracts, parameter definitions, and replacement examples in [AUTHORING.md](AUTHORING.md). That file is an author guide: its alternative examples are not additional execution instructions. Execute the active method in this SKILL.md.


# Stability run

## What to customize

This Skill chooses which dataset cases to run and how many times to repeat
each case. To customize it, edit Custom requirements above; the
host still runs the target Agent and records its responses. `AUTHORING.md`
contains examples for running all cases, sampling, or focusing on a range.

Read the dataset, then write `select_cases(cases, schedule)`. Implement the selection and repeat counts in Custom requirements. Register each selected original case once with its requested repeats. The host invokes the target Agent; write only the requested strategy.

## Scheduling interface

The host calls your Python entry point `select_cases(cases, schedule)`.
`cases` is a tuple of immutable objects with `record_index` (one-based),
`question`, and `expected_answer` attributes. Use `case.question`.
`schedule(case, repeats=1)` registers an original supplied case for a positive
integer number of target requests and returns None. Repeated registration adds
repeats; the selector's return value is ignored.

First use the Agent tool `read_dataset()` to inspect the dataset, then submit a
complete Python source string through `write_strategy(content)`. Generate the strategy from Custom requirements. AUTHORING.md contains syntax examples.


The strategy provides only `len`, `min`, `max`, `range`, `enumerate`, `list`,
`tuple`, and seeded `random.sample`, `random.choice`, `random.randint` helpers.
Use these without imports. The host validates and executes the strategy before
calling the target. AUTHORING.md contains replacement examples for authors;
execute only the active selection rule in this file.
