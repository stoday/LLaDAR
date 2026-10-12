# 作者指南

有效执行指示位於 SKILL.md。本指南是参考，不会注册为另一个 Skill。替代范例须由作者取代原规则後才采用；仅修改本指南不会改变执行方法。工具名称、参数栏位与程式码契约保持原值。

依下方有效方法与完整范例修改选择、评估或叙述规则。主程式持续负责资料验证、档案、受测 Agent 请求与确定性统计；修改 Skill 不会改变这些界线。

## 方法与提交契约

# 证据摘要

仅依提供的冻结评估事实撰写 overview、findings、limitations。数字与表格由主程式产生，叙述不包含数字字元，不重新计算、修改或虚构测量。依证据提及执行错误、判定错误、小样本限制与待人工审查事项。

单题报告以 submit_section(section, text, source_refs, request_id) 提交三个章节，引用提供的事实键（如 summary、aggregates、plan）或试次 ID。需要更多证据时用 read_report_fact(reference, start_char)，依 next_start 读完整。检查所有主张的支持，续跑时保留 completed_sections。只用 revise_section(item_id, request_id, revision, text, source_refs) 修正无效章节，并以 get_section 查询不确定结果。所有必要章节被接受才完成。

情境报告使用 submit_report(narrative)，恰含三个非空字串 overview、findings、limitations。

stage=report_review 时检查提供章节与冻结事实，不重写报告。必要时 read_report_fact 读试次证据，透过 submit_section_review 提交每个必要章节的 {section, approved: boolean, reason}。拒绝未支持主张、隐藏错误、小样本过度概括与已完成人工审查的未证明声明。被拒章节退回局部修正，已通过章节保留。

## 修改范例（仅供作者参考）

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```
