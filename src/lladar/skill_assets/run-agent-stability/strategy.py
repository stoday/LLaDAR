def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
