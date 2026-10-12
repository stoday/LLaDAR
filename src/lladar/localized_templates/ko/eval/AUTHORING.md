# 작성자 안내

실행 지시는 SKILL.md에 있습니다. 이 안내는 참고용이며 별도 Skill로 등록하지 않습니다. 대체 예제를 적용하려면 원래 규칙을 교체하세요. 안내만 수정하면 실행 방법이 바뀌지 않습니다. 도구 이름, 필드 및 코드 계약은 유지합니다.

아래 방법과 전체 예제를 참고하여 선택, 평가 또는 설명 규칙을 수정하세요. 호스트는 검증, 파일, 대상 Agent 요청 및 결정적 통계를 계속 담당하며 Skill 수정으로 이 경계는 바뀌지 않습니다.

## 방법 및 제출 계약

# 답변 판정

stage=plan에서 submit_plan에 boolean correct 차원을 가진 계획을 제출합니다. stage=judgment에서는 고정 계획을 사용해 expected_answer와 actual_response만 비교하고 submit_judgment에 values 및 비어 있지 않은 reason을 제출합니다. 근거가 있으면 correct=true, 불일치하면 false, 판정 불가이면 null과 이유를 사용합니다. 집계는 계산하지 않습니다.

status를 확인합니다. 형식 거절 시 revise_plan／revise_judgment에 item_id, 새 request_id, 현재 revision 및 전체 수정본을 제출하며 수정은 최대 세 번입니다. get_plan／get_judgment로 불확실한 결과를 조회합니다. 수락된 계획은 변경하지 않습니다. 지정 trial_id와 plan_version을 사용하며 request_id는 항목별 범위입니다. actual_response=null이고 response_read가 있으면 긴 응답입니다. read_trial_response(trial_id, start_char) 및 next_start로 전체를 읽고 실행 실패로 취급하지 않습니다.

사용자 지정 Skill의 typed 및 semantic-probe 응답도 해당 고정 계획으로 평가합니다. 제출이 수락되어야 완료됩니다.

stage=situation_judgment에서 이 정확성 방법에는 상황 기준이 없습니다. judgment_contract에 따라 validity=indeterminate, behavior=indeterminate, evidence_turn_ids=[]를 제출하고 reason에 사용자 Skill에서 완전한 상황 기준을 정의해야 함을 설명합니다. plan 도구는 없습니다. 기준을 추가하면 실제 턴 ID를 인용하고 생성 제약을 유지합니다.

## 수정 예제(작성자 참고용)

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

```json
{"values": {"correct": true}, "reason": "The response states the expected answer with all required conditions."}
```
