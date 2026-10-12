---
name: report
description: 可编辑的 LLaDAR 阶段方法与提交契约。
---

此档案定义有效方法。AUTHORING.md 是工具与修改指南，替代范例不会自动成为额外执行指示。完成条件是主程式接受所需提交；纯文字回覆不代表完成。新生成叙述使用执行时的 --lang；工具名称、资料栏位、程式码与逐字引文保持原值。

# 证据摘要

仅依提供的冻结评估事实撰写 overview、findings、limitations。数字与表格由主程式产生，叙述不包含数字字元，不重新计算、修改或虚构测量。依证据提及执行错误、判定错误、小样本限制与待人工审查事项。

单题报告以 submit_section(section, text, source_refs, request_id) 提交三个章节，引用提供的事实键（如 summary、aggregates、plan）或试次 ID。需要更多证据时用 read_report_fact(reference, start_char)，依 next_start 读完整。检查所有主张的支持，续跑时保留 completed_sections。只用 revise_section(item_id, request_id, revision, text, source_refs) 修正无效章节，并以 get_section 查询不确定结果。所有必要章节被接受才完成。

情境报告使用 submit_report(narrative)，恰含三个非空字串 overview、findings、limitations。

stage=report_review 时检查提供章节与冻结事实，不重写报告。必要时 read_report_fact 读试次证据，透过 submit_section_review 提交每个必要章节的 {section, approved: boolean, reason}。拒绝未支持主张、隐藏错误、小样本过度概括与已完成人工审查的未证明声明。被拒章节退回局部修正，已通过章节保留。
