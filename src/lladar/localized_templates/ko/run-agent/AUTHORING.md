# 작성자 안내

실행 지시는 SKILL.md에 있습니다. 이 안내는 참고용이며 별도 Skill로 등록하지 않습니다. 대체 예제를 적용하려면 원래 규칙을 교체하세요. 안내만 수정하면 실행 방법이 바뀌지 않습니다. 도구 이름, 필드 및 코드 계약은 유지합니다.

아래 방법과 전체 예제를 참고하여 선택, 평가 또는 설명 규칙을 수정하세요. 호스트는 검증, 파일, 대상 Agent 요청 및 결정적 통계를 계속 담당하며 Skill 수정으로 이 경계는 바뀌지 않습니다.

## 방법 및 제출 계약

# 안정성 실행

read_dataset()으로 확인한 뒤 write_strategy(content)에 전체 Python 소스를 제출합니다. 진입점은 select_cases(cases, schedule)입니다. 각 원본 사례를 한 번만 repeats=3으로 등록합니다. 필터링, 대상 Agent 호출 및 전략 외 파일 쓰기는 하지 않습니다.

cases는 불변 객체의 tuple이며 record_index(1부터 시작), question, expected_answer를 포함합니다. case.question을 사용합니다. schedule(case, repeats=1)은 원래 사례와 양의 정수를 받고 None을 반환합니다. 재등록은 반복 횟수를 더하며 진입점 반환 값은 무시됩니다.

len, min, max, range, enumerate, list, tuple 및 시드가 있는 random.sample, random.choice, random.randint를 import 없이 사용합니다. 호스트는 전략을 검증 및 실행한 뒤 대상을 호출합니다. AUTHORING.md의 대체 예제는 수정용이며 여기의 활성 규칙만 실행합니다.

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

## 수정 예제(작성자 참고용)

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

```python
def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case, repeats=1)
```

```python
def select_cases(cases, schedule):
    for case in cases:
        if case.record_index <= 5:
            schedule(case, repeats=2)
```
