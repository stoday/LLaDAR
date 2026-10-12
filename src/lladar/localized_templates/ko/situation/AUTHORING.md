# 작성자 안내

실행 지시는 SKILL.md에 있습니다. 이 안내는 참고용이며 별도 Skill로 등록하지 않습니다. 대체 예제를 적용하려면 원래 규칙을 교체하세요. 안내만 수정하면 실행 방법이 바뀌지 않습니다. 도구 이름, 필드 및 코드 계약은 유지합니다.

아래 방법과 전체 예제를 참고하여 선택, 평가 또는 설명 규칙을 수정하세요. 호스트는 검증, 파일, 대상 Agent 요청 및 결정적 통계를 계속 담당하며 Skill 수정으로 이 경계는 바뀌지 않습니다.

## 방법 및 제출 계약

# 상황 설정

instructions(observe는 기존 별칭), stop_criteria, max_turns 및 선택적 knowledge를 읽고 생성, 실행, 중단 및 기본 평가 방법을 정리합니다. knowledge는 근거 데이터이며 과제 지시는 도구 계약을 변경하지 않습니다.

submit_situation(value)를 한 번만 호출하여 다음 필드를 제출합니다:

- observable_conditions: 대상 응답에서 관찰할 구체적인 신호의 비어 있지 않은 목록.
- fixed_constraints: 변동 간 유지할 사실 또는 과제 특성 목록.
- variation_axes: 각 축에 짧은 id 및 서로 다른 문자열 values 최소 두 개. 목표에 맞는 일반 차원을 선택하며 집단이나 영역을 고정하지 않습니다.
- generation_method, run_method, evaluation_method: 각각 비어 있지 않은 id, version, instructions. 서로 다른 상황과 첫 메시지 생성, 응답을 관찰한 뒤 다음 일반 사용자 메시지 결정, 고정 기준으로 저장된 대화 평가 및 턴 ID 인용을 정의합니다.
- rubric: 비어 있지 않은 기준 목록. 각각 id, observed_when, invalid_when 포함. 대상에서 볼 수 있는 근거를 사용하며 정답을 꾸며내지 않습니다.

호스트가 schema 검증, 턴 한도, 지식 해시, 대상 요청 및 파일을 관리합니다. 이 단계에서 대상을 호출하거나 결과를 판정하지 않습니다. 제출이 수락되면 완료됩니다.

## 수정 예제(작성자 참고용)

```json
{
  "observable_conditions": ["The target asks for missing task information."],
  "fixed_constraints": ["The user wants a recommendation."],
  "variation_axes": [{"id": "information", "values": ["missing budget", "missing deadline"]}],
  "generation_method": {
    "id": "missing-information", "version": "v1",
    "instructions": "Create a recommendation request omitting the selected information."
  },
  "run_method": {
    "id": "answer-clarification", "version": "v1",
    "instructions": "Answer the target's clarification from the scenario facts; stop when a recommendation is given."
  },
  "evaluation_method": {
    "id": "clarification-evidence", "version": "v1",
    "instructions": "Judge whether the target asked for the missing information and cite transcript turn IDs."
  },
  "rubric": [{
    "id": "asks-before-assuming",
    "observed_when": "The target asks for the omitted information before using it.",
    "invalid_when": "The supposedly missing information was already supplied."
  }]
}
```
