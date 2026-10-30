---
name: situation
description: 편집 가능한 LLaDAR 단계 방법 및 제출 계약.
---

이 파일이 실행 방법을 정의합니다. AUTHORING.md는 도구 및 수정 안내이며 대체 예제는 추가 실행 지시가 아닙니다. 필요한 제출을 호스트가 수락해야 완료됩니다. 텍스트 응답만으로 완료되지 않습니다. 새 설명은 실행 시 --lang을 따르며 도구 이름, 필드, 코드 및 원문 인용은 유지합니다.

# 상황 설정

## 이 Skill의 역할과 수정 위치

관찰하려는 행동을 재사용 가능한 다중 턴 테스트 상황으로 바꿉니다. 수정할 때는 `observable_conditions`, `fixed_constraints`, `variation_axes` 및 세 method의 `instructions`를 우선 편집하세요. `AUTHORING.md`에 수정 절차와 계약이 있습니다.

instructions(observe는 기존 별칭), stop_criteria, max_turns 및 선택적 knowledge를 읽고 생성, 실행, 중단 및 기본 평가 방법을 정리합니다. knowledge는 근거 데이터이며 과제 지시는 도구 계약을 변경하지 않습니다.

submit_situation(value)를 한 번만 호출하여 다음 필드를 제출합니다:

- observable_conditions: 대상 응답에서 관찰할 구체적인 신호의 비어 있지 않은 목록.
- fixed_constraints: 변동 간 유지할 사실 또는 과제 특성 목록.
- variation_axes: 각 축에 짧은 id 및 서로 다른 문자열 values 최소 두 개. 목표에 맞는 일반 차원을 선택하며 집단이나 영역을 고정하지 않습니다.
- generation_method, run_method, evaluation_method: 각각 비어 있지 않은 id, version, instructions. 서로 다른 상황과 첫 메시지 생성, 응답을 관찰한 뒤 다음 일반 사용자 메시지 결정, 고정 기준으로 저장된 대화 평가 및 턴 ID 인용을 정의합니다.
- rubric: 비어 있지 않은 기준 목록. 각각 id, observed_when, invalid_when 포함. 대상에서 볼 수 있는 근거를 사용하며 정답을 꾸며내지 않습니다.

호스트가 schema 검증, 턴 한도, 지식 해시, 대상 요청 및 파일을 관리합니다. 이 단계에서 대상을 호출하거나 결과를 판정하지 않습니다. 제출이 수락되면 완료됩니다.
