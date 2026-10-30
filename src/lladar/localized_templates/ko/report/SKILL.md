---
name: report
description: 편집 가능한 LLaDAR 단계 방법 및 제출 계약.
---

이 파일이 실행 방법을 정의합니다. AUTHORING.md는 도구 및 수정 안내이며 대체 예제는 추가 실행 지시가 아닙니다. 필요한 제출을 호스트가 수락해야 완료됩니다. 텍스트 응답만으로 완료되지 않습니다. 새 설명은 실행 시 --lang을 따르며 도구 이름, 필드, 코드 및 원문 인용은 유지합니다.

# 근거 요약

## 이 Skill의 역할과 수정 위치

저장된 평가 사실을 사람이 읽는 보고서로 정리합니다. 독자, 문체, 순서 및 강조점을 바꾸려면 이 부분을 수정하세요. 숫자와 표는 호스트가 만들며 Skill은 제공된 근거만 해석합니다. `AUTHORING.md`에 수정 절차가 있습니다.

제공된 고정 평가 사실만으로 overview, findings, limitations를 작성합니다. 숫자와 표는 호스트가 생성하며 설명에는 숫자 문자를 넣지 않고 측정을 재계산, 변경 또는 꾸며내지 않습니다. 근거에 따라 실행 오류, 판정 오류, 적은 표본 및 미완료 수동 검토를 설명합니다.

단일 질문 보고서는 submit_section(section, text, source_refs, request_id)으로 세 절을 제출하고 제공된 사실 키(summary, aggregates, plan 등) 또는 시도 ID를 인용합니다. 추가 근거는 read_report_fact(reference, start_char) 및 next_start로 끝까지 읽습니다. 모든 주장의 근거를 확인하며 재개 시 completed_sections를 유지합니다. 잘못된 절만 revise_section(item_id, request_id, revision, text, source_refs)으로 수정하고 get_section으로 불확실한 결과를 조회합니다. 필요한 모든 절이 수락되어야 완료됩니다.

상황 보고서는 submit_report(narrative)에 비어 있지 않은 overview, findings, limitations 문자열 세 개만 제출합니다.

stage=report_review에서는 고정 사실과 절을 비교하며 보고서를 다시 쓰지 않습니다. 필요하면 read_report_fact로 시도 근거를 읽고 submit_section_review에 각 절의 {section, approved: boolean, reason}을 제출합니다. 근거 없는 주장, 숨겨진 오류, 적은 표본의 과도한 일반화 및 증명되지 않은 수동 검토 완료를 거절합니다. 거절된 절만 작성자에게 돌려주며 승인된 절은 유지합니다.
