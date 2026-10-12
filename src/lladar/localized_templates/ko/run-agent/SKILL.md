---
name: run-agent
description: 편집 가능한 LLaDAR 단계 방법 및 제출 계약.
---

이 파일이 실행 방법을 정의합니다. AUTHORING.md는 도구 및 수정 안내이며 대체 예제는 추가 실행 지시가 아닙니다. 필요한 제출을 호스트가 수락해야 완료됩니다. 텍스트 응답만으로 완료되지 않습니다. 새 설명은 실행 시 --lang을 따르며 도구 이름, 필드, 코드 및 원문 인용은 유지합니다.

# 안정성 실행

read_dataset()으로 확인한 뒤 write_strategy(content)에 전체 Python 소스를 제출합니다. 진입점은 select_cases(cases, schedule)입니다. 각 원본 사례를 한 번만 repeats=3으로 등록합니다. 필터링, 대상 Agent 호출 및 전략 외 파일 쓰기는 하지 않습니다.

cases는 불변 객체의 tuple이며 record_index(1부터 시작), question, expected_answer를 포함합니다. case.question을 사용합니다. schedule(case, repeats=1)은 원래 사례와 양의 정수를 받고 None을 반환합니다. 재등록은 반복 횟수를 더하며 진입점 반환 값은 무시됩니다.

len, min, max, range, enumerate, list, tuple 및 시드가 있는 random.sample, random.choice, random.randint를 import 없이 사용합니다. 호스트는 전략을 검증 및 실행한 뒤 대상을 호출합니다. AUTHORING.md의 대체 예제는 수정용이며 여기의 활성 규칙만 실행합니다.

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```
