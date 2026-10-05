## Scheduling interface

The host calls your Python entry point `select_cases(cases, schedule)`.
`cases` is a tuple of immutable objects with `record_index` (one-based),
`question`, and `expected_answer` attributes. Use `case.question`.
`schedule(case, repeats=1)` registers an original supplied case for a positive
integer number of target requests and returns None. Repeated registration adds
repeats; the selector's return value is ignored.

First use the Agent tool `read_dataset()` to inspect the dataset, then submit a
complete Python source string through `write_strategy(content)`. For the active
default rule above, use:

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

The strategy provides only `len`, `min`, `max`, `range`, `enumerate`, `list`,
`tuple`, and seeded `random.sample`, `random.choice`, `random.randint` helpers.
Use these without imports. The host validates and executes the strategy before
calling the target. AUTHORING.md contains replacement examples for authors;
execute only the active selection rule in this file.
