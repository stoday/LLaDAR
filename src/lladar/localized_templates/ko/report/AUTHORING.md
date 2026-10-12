# 작성자 안내

실행 지시는 SKILL.md에 있습니다. 이 안내는 참고용이며 별도 Skill로 등록하지 않습니다. 대체 예제를 적용하려면 원래 규칙을 교체하세요. 안내만 수정하면 실행 방법이 바뀌지 않습니다. 도구 이름, 필드 및 코드 계약은 유지합니다.

아래 방법과 전체 예제를 참고하여 선택, 평가 또는 설명 규칙을 수정하세요. 호스트는 검증, 파일, 대상 Agent 요청 및 결정적 통계를 계속 담당하며 Skill 수정으로 이 경계는 바뀌지 않습니다.

## 방법 및 제출 계약

# 근거 요약

제공된 고정 평가 사실만으로 overview, findings, limitations를 작성합니다. 숫자와 표는 호스트가 생성하며 설명에는 숫자 문자를 넣지 않고 측정을 재계산, 변경 또는 꾸며내지 않습니다. 근거에 따라 실행 오류, 판정 오류, 적은 표본 및 미완료 수동 검토를 설명합니다.

단일 질문 보고서는 submit_section(section, text, source_refs, request_id)으로 세 절을 제출하고 제공된 사실 키(summary, aggregates, plan 등) 또는 시도 ID를 인용합니다. 추가 근거는 read_report_fact(reference, start_char) 및 next_start로 끝까지 읽습니다. 모든 주장의 근거를 확인하며 재개 시 completed_sections를 유지합니다. 잘못된 절만 revise_section(item_id, request_id, revision, text, source_refs)으로 수정하고 get_section으로 불확실한 결과를 조회합니다. 필요한 모든 절이 수락되어야 완료됩니다.

상황 보고서는 submit_report(narrative)에 비어 있지 않은 overview, findings, limitations 문자열 세 개만 제출합니다.

stage=report_review에서는 고정 사실과 절을 비교하며 보고서를 다시 쓰지 않습니다. 필요하면 read_report_fact로 시도 근거를 읽고 submit_section_review에 각 절의 {section, approved: boolean, reason}을 제출합니다. 근거 없는 주장, 숨겨진 오류, 적은 표본의 과도한 일반화 및 증명되지 않은 수동 검토 완료를 거절합니다. 거절된 절만 작성자에게 돌려주며 승인된 절은 유지합니다.

## 수정 예제(작성자 참고용)

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```
