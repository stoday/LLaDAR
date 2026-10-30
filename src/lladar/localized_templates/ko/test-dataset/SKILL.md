---
name: test-dataset
description: 편집 가능한 LLaDAR 단계 방법 및 제출 계약.
---

이 파일이 실행 방법을 정의합니다. AUTHORING.md는 도구 및 수정 안내이며 대체 예제는 추가 실행 지시가 아닙니다. 필요한 제출을 호스트가 수락해야 완료됩니다. 텍스트 응답만으로 완료되지 않습니다. 새 설명은 실행 시 --lang을 따르며 도구 이름, 필드, 코드 및 원문 인용은 유지합니다.

# 출처에 기반한 질문 생성

## 이 Skill의 역할과 수정 위치

이 Skill은 지식 출처에서 검증 가능한 질문, 예상 답변 및 근거를 만듭니다. 질문 생성 방식을 바꾸려면 아래 생성 규칙을 수정하세요. `AUTHORING.md`는 수정 절차와 자세한 도구 계약을 설명하는 참고 자료입니다.

지정된 출처를 읽고 요청에 따라 직접 QA, 의미 그래프 또는 둘 다 선택합니다. 출처의 사실, 주석 또는 근거 있는 도출 기준을 사용하며 출처 사실, 추론 개념 및 합성 제어를 구분합니다. 질문은 독립적으로 답변 가능하고 예상 답변은 출처로 뒷받침되어야 합니다.

자유 답변은 read_source부터 시작합니다. assigned_start부터 assigned_end까지 정확히 읽고 next_start를 따릅니다. submit_question에 question, expected_answer, source_refs, answer_basis, rationale 및 고유 request_id를 함께 제출합니다. answer_basis는 source_answer, source_annotation 또는 derived_criterion입니다. status와 issues를 확인하고 거절되면 revise_question에 item_id, 현재 revision, 새 request_id 및 전체 수정본을 제출합니다. 수정은 최대 세 번입니다. 결과가 불확실하면 get_question_request로 원래 request_id를 조회하며 동일한 내용에만 해당 ID를 재사용합니다.

호환 지식점 방법은 submit_knowledge_points에 statement, topic, read_id 및 원문 quote를 포함한 evidence를 제출합니다. list_knowledge_points／read_knowledge_point로 수락된 지식점을 확인합니다. submit_qa는 knowledge_point_id, question, expected_answer를 사용합니다. 자유 답변에 id, actual_response 또는 question_type을 추가하지 않습니다. 선택 및 순위 질문은 요청의 typed contract를 따릅니다.

개념 또는 비교에 그래프가 필요하면 근거 있는 nodes, edges, facts를 submit_semantic_graph에 제출하고 read_semantic_graph로 확인한 뒤 submit_test_plans를 제출합니다. direct_fact는 값과 단위, concept_mapping은 전체 후보 집합을 답변으로 사용합니다. controlled_invariance는 한 쌍에서 선언된 control_dimension 하나의 control_value만 바꾸고 과제, 개념, 후보, 답변 계약 및 질문 골격을 유지합니다. 차원 ID, 이름, 의미 범위, 값 관계 및 호환 차원을 선언하며 관계가 불명확한 값은 사용하지 않습니다.

호스트는 검증, 차원 선택, 중복 제거, 수량 제한, 완전한 쌍 및 저장을 담당합니다. validation_errors와 existing_results를 확인해 수정하며 완료된 그래프와 계획은 다시 제출하지 않습니다. Python은 탐색, 계산 및 수정에 사용할 수 있습니다. 내장 자유 답변은 원자적 제출 도구를 사용하며 직렬화는 호스트가 담당합니다. 호환 candidate_path를 사용하는 경우 해당 파일은 전체 후보의 정확한 스냅샷이며 candidate_contract를 따라야 합니다. 근거와 계약이 모두 유효하면 완료됩니다.
