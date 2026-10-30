---
name: eval
description: 편집 가능한 LLaDAR 단계 방법 및 제출 계약.
---

이 파일이 실행 방법을 정의합니다. AUTHORING.md는 도구 및 수정 안내이며 대체 예제는 추가 실행 지시가 아닙니다. 필요한 제출을 호스트가 수락해야 완료됩니다. 텍스트 응답만으로 완료되지 않습니다. 새 설명은 실행 시 --lang을 따르며 도구 이름, 필드, 코드 및 원문 인용은 유지합니다.

# 답변 판정

## 이 Skill의 역할과 수정 위치

무엇을 “정답”으로 판정할지 정의합니다. 동등한 표현, 필수 조건 또는 추가 평가 차원을 바꾸려면 판정 규칙을 수정하세요. 집계는 호스트가 담당합니다. `AUTHORING.md`에 수정 절차와 제출 형식이 있습니다.

stage=plan에서 submit_plan에 boolean correct 차원을 가진 계획을 제출합니다. stage=judgment에서는 고정 계획을 사용해 expected_answer와 actual_response만 비교하고 submit_judgment에 values 및 비어 있지 않은 reason을 제출합니다. 근거가 있으면 correct=true, 불일치하면 false, 판정 불가이면 null과 이유를 사용합니다. 집계는 계산하지 않습니다.

status를 확인합니다. 형식 거절 시 revise_plan／revise_judgment에 item_id, 새 request_id, 현재 revision 및 전체 수정본을 제출하며 수정은 최대 세 번입니다. get_plan／get_judgment로 불확실한 결과를 조회합니다. 수락된 계획은 변경하지 않습니다. 지정 trial_id와 plan_version을 사용하며 request_id는 항목별 범위입니다. actual_response=null이고 response_read가 있으면 긴 응답입니다. read_trial_response(trial_id, start_char) 및 next_start로 전체를 읽고 실행 실패로 취급하지 않습니다.

사용자 지정 Skill의 typed 및 semantic-probe 응답도 해당 고정 계획으로 평가합니다. 제출이 수락되어야 완료됩니다.

stage=situation_judgment에서 이 정확성 방법에는 상황 기준이 없습니다. judgment_contract에 따라 validity=indeterminate, behavior=indeterminate, evidence_turn_ids=[]를 제출하고 reason에 사용자 Skill에서 완전한 상황 기준을 정의해야 함을 설명합니다. plan 도구는 없습니다. 기준을 추가하면 실제 턴 ID를 인용하고 생성 제약을 유지합니다.

```json
{
  "title": "Answer correctness",
  "approach": "Accept equivalent wording while requiring all answer conditions.",
  "dimensions": [
    {"name": "correct", "description": "Matches the expected answer including required conditions.", "kind": "boolean"}
  ],
  "limitations": ["Only the supplied question, expected answer and actual response are evidence."]
}
```
