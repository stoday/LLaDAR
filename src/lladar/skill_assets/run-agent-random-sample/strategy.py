def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case)
