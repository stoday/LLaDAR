# Writing a run-agent Skill: case selection and repeat counts

This guide is for Skill authors. The active rules are in `SKILL.md`.
The guide is not automatically registered as another Skill, but an Agent can
read it as a reference resource. Alternative examples do not replace the active
method. When adopting an alternative, replace the original rule in `SKILL.md`
and keep one consistent method. Editing only this guide does not change the rules.

## Purpose and where to edit

Use this Skill for quick sampling, focused tests, or repeated stability tests.
Edit the "Stability run" section of `SKILL.md`: the default runs every case
three times. Replace `repeats=3` to change the count, or replace "every provided
case" to use sampling. The host obtains target responses after scheduling.

## Complete Agent tool list

| Tool | Parameters and purpose | Return value and next step |
| --- | --- | --- |
| `read_dataset()` | No arguments; read questions and expected answers | A string with `[1] Q: ...` followed by `Expected: ...` on the next line; use it to choose a strategy |
| `write_strategy(content)` | A complete Python source string defining `select_cases` | `strategy.py was written`; checks nonempty source and syntax, then the host executes and validates the schedule |

Read first, then submit through the tool. Pasting code in chat is not a submission.
The tool writes to the host-designated path.

## What is `select_cases(cases, schedule)`?

This is the entry point defined in the strategy for LLaDAR to call, not an Agent
tool. The host supplies both arguments; authors do not construct them.

| Name | Type and content | Usage |
| --- | --- | --- |
| `cases` | A read-only tuple of `StrategyCase` objects | Select original cases with `for case in cases` or `cases[:5]` |
| `case.record_index` | An integer index starting at one | `case.record_index <= 5` selects the first five cases |
| `case.question` | The question string | `"refund" in case.question.lower()` selects by question text |
| `case.expected_answer` | The expected-answer string | Available for selection; does not contain a target response |
| `schedule` | A host callback | `schedule(case, repeats=3)` registers a case and repeat count |

Use `case.question`, not `case["question"]`. `schedule(case, repeats=1)` returns
`None`; repeats must be a positive integer, not a boolean. Use an original case
from `cases`, not a new dictionary or copy. Repeated registration adds counts;
case order follows first registration. The selector's return value is ignored;
`return cases` alone schedules nothing. Two cases each registered once with
`repeats=3` produce six target requests.

## Complete strategy environment

Available built-ins: `len`, `min`, `max`, `range`, `enumerate`, `list`, `tuple`.
Loops, conditions, slicing, and string methods are available. There is no
`import`, `open`, `print`, `sorted`, network interface, or target invocation interface.
A seeded `random` object is supplied without an import:

| Method | Parameters, return value, and use case |
| --- | --- |
| `random.sample(population, k)` | Returns a list of k distinct sequence items; `0 <= k <= len(population)`; quick sampling |
| `random.choice(population)` | Returns an original item from a nonempty sequence; single-case smoke test |
| `random.randint(a, b)` | Returns an integer including both endpoints; choose a repeat count in a positive integer range |

The same input, program, and seed reproduce the sample.

## Complete alternative examples

These blocks are `write_strategy(content)` argument examples. Choose one method,
put its rules in `SKILL.md`, and ask the Agent to read before submitting.

### Stability: every case three times (default)

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

### Quick check: randomly select up to five cases, once each

```python
def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case, repeats=1)
```

Skill instructions that can replace the default section:

> Read the dataset with read_dataset. Use write_strategy to submit Python defining
> select_cases(cases, schedule). Select min(5, len(cases)) cases with the supplied
> random.sample helper and schedule each selected case once with repeats=1.
> Finish after submitting the strategy; the host executes the target.

### Focused range: the first five cases, twice each

```python
def select_cases(cases, schedule):
    for case in cases:
        if case.record_index <= 5:
            schedule(case, repeats=2)
```

You can use a question-text condition instead. No matches produce an empty schedule.

## Execution contract

Use `SKILL.md` with `lladar run-agent DATASET.jsonl --project PROJECT --skill
DIRECTORY --output responses.jsonl`. This Skill selects cases and repeat
counts. The target Agent still supplies the actual responses.

The request has `stage: select_cases`. Call `read_dataset()` first; it returns
each numbered question and expected answer as text. Then call
`write_strategy(content)` with Python source defining
`select_cases(cases, schedule)`. The host checks that the source compiles and
runs it with supplied `cases`, `schedule`, and deterministic `random` helper.
Each case has `record_index`, `question`, and `expected_answer`.
Call `schedule(case, repeats=1)` for each selected case; repeats must be a
positive integer. Complete one valid strategy that schedules the intended
cases. The host then invokes the target Agent and records trials.

The bundled `run-agent-stability` method has a trusted `strategy.py` that the
host executes without a scheduling model call. A generated custom Skill lives
at a different path, so it uses the Skill agent and `write_strategy` tool.
Copying `strategy.py` does not enable the bundled fast path. Keep target
execution and adapter discovery separate from scheduling instructions.
