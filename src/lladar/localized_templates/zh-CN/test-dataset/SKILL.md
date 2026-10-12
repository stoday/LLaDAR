---
name: test-dataset
description: 可编辑的 LLaDAR 阶段方法与提交契约。
---

此档案定义有效方法。AUTHORING.md 是工具与修改指南，替代范例不会自动成为额外执行指示。完成条件是主程式接受所需提交；纯文字回覆不代表完成。新生成叙述使用执行时的 --lang；工具名称、资料栏位、程式码与逐字引文保持原值。

# 来源支持的问题生成

读取指定来源，依请求选择直接 QA、语意图谱或两者。使用来源中的事实、注解或可证明推导的准则；保留来源证据，区分来源事实、推论概念与合成控制。问题须独立可回答，预期答案须由来源支持。

直接自由问答：先 read_source，针对指定批次精确读取 assigned_start 至 assigned_end 并跟随 next_start。透过 submit_question 一次提交 question、expected_answer、source_refs、answer_basis、rationale 与唯一 request_id。answer_basis 使用 source_answer、source_annotation 或 derived_criterion。确认 status 与 issues。若被拒绝，以 revise_question 使用 item_id、目前 revision、新 request_id 与完整修正版；最多修正三次。提交结果不确定时用 get_question_request 查询原 request_id，只对相同内容重用该 ID。

相容知识点路径：先 submit_knowledge_points，使用 read_id 与逐字 quote 提交 statement、topic、evidence；以 list_knowledge_points／read_knowledge_point 读取已接受知识点。submit_qa 使用 knowledge_point_id、question、expected_answer。自由题不加入 id、actual_response 或 question_type。选择／排序题依请求的 typed contract。

若图谱支持概念或对照问题：先提交有证据的 nodes、edges、facts 至 submit_semantic_graph；使用 read_semantic_graph 确认，再 submit_test_plans。direct_fact 的答案为事实值与单位；concept_mapping 的答案涵盖完整候选集合。controlled_invariance 一组只改变一个已宣告 control_dimension 的 control_value，保留任务、概念、候选、答案契约与问题骨架。宣告维度 ID、标签、语意范围、值关系与相容维度；关系不明时不可使用。

主程式负责验证、选择维度、去重、笔数上限、完整配对与最终写入。依 validation_errors 与 existing_results 修正既有结果，不重送已完成的图谱与计画。Python 可探索、计算与修正；内建自由题使用原子提交工具，主程式负责序列化。若自订相容方法提供 candidate_path，候选档是完整且具权威性的快照，须遵守 candidate_contract。完成条件是候选证据与契约全部有效。
