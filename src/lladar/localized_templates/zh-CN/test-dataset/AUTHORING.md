# 作者指南

## 从这里开始

要改变生成的问题，请修改 `SKILL.md`，不要只修改本指南。修改后在生成命令中用 `--skill` 指定此目录，并检查 JSONL 和 `.generation.json`。

有效执行指示位於 SKILL.md。本指南是参考，不会注册为另一个 Skill。替代范例须由作者取代原规则後才采用；仅修改本指南不会改变执行方法。工具名称、参数栏位与程式码契约保持原值。

依下方有效方法与完整范例修改选择、评估或叙述规则。主程式持续负责资料验证、档案、受测 Agent 请求与确定性统计；修改 Skill 不会改变这些界线。

## 方法与提交契约

# 来源支持的问题生成

读取指定来源，依请求选择直接 QA、语意图谱或两者。使用来源中的事实、注解或可证明推导的准则；保留来源证据，区分来源事实、推论概念与合成控制。问题须独立可回答，预期答案须由来源支持。

直接自由问答：先 read_source，针对指定批次精确读取 assigned_start 至 assigned_end 并跟随 next_start。透过 submit_question 一次提交 question、expected_answer、source_refs、answer_basis、rationale 与唯一 request_id。answer_basis 使用 source_answer、source_annotation 或 derived_criterion。确认 status 与 issues。若被拒绝，以 revise_question 使用 item_id、目前 revision、新 request_id 与完整修正版；最多修正三次。提交结果不确定时用 get_question_request 查询原 request_id，只对相同内容重用该 ID。

相容知识点路径：先 submit_knowledge_points，使用 read_id 与逐字 quote 提交 statement、topic、evidence；以 list_knowledge_points／read_knowledge_point 读取已接受知识点。submit_qa 使用 knowledge_point_id、question、expected_answer。自由题不加入 id、actual_response 或 question_type。选择／排序题依请求的 typed contract。

若图谱支持概念或对照问题：先提交有证据的 nodes、edges、facts 至 submit_semantic_graph；使用 read_semantic_graph 确认，再 submit_test_plans。direct_fact 的答案为事实值与单位；concept_mapping 的答案涵盖完整候选集合。controlled_invariance 一组只改变一个已宣告 control_dimension 的 control_value，保留任务、概念、候选、答案契约与问题骨架。宣告维度 ID、标签、语意范围、值关系与相容维度；关系不明时不可使用。

主程式负责验证、选择维度、去重、笔数上限、完整配对与最终写入。依 validation_errors 与 existing_results 修正既有结果，不重送已完成的图谱与计画。Python 可探索、计算与修正；内建自由题使用原子提交工具，主程式负责序列化。若自订相容方法提供 candidate_path，候选档是完整且具权威性的快照，须遵守 candidate_contract。完成条件是候选证据与契约全部有效。

## 修改范例（仅供作者参考）

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

```json
{
  "nodes": [
    {"id": "service-a", "type": "entity", "label": "Service A",
     "origin": "source", "evidence_refs": ["kp_000001"]}
  ],
  "edges": [],
  "facts": [
    {"entity_id": "service-a", "label": "Service A", "value": "30",
     "unit": "days", "evidence_ref": "kp_000001"}
  ]
}
```

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

```json
{
  "knowledge_point_id": "kp_000001",
  "question": "How long does Service A keep data?",
  "expected_answer": "30 days"
}
```
