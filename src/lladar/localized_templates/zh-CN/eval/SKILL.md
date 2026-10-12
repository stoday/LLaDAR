---
name: eval
description: 可编辑的 LLaDAR 阶段方法与提交契约。
---

此档案定义有效方法。AUTHORING.md 是工具与修改指南，替代范例不会自动成为额外执行指示。完成条件是主程式接受所需提交；纯文字回覆不代表完成。新生成叙述使用执行时的 --lang；工具名称、资料栏位、程式码与逐字引文保持原值。

# 答案判定

stage=plan 时以 submit_plan 提交布林 correct 维度的计画。stage=judgment 时使用冻结计画，仅比较 expected_answer 与 actual_response，以 submit_judgment 提交 values 与非空 reason。受支持时 correct=true，不相符时 false；证据无法判定时使用 null 并解释。不计算汇总统计。

确认提交 status；格式被拒绝时，使用 revise_plan／revise_judgment，传入 item_id、新 request_id、目前 revision 与完整修正内容，最多修正三次。使用 get_plan／get_judgment 查询不确定结果。已接受的计画不得修改。使用指定 trial_id 与 plan_version，各项 request_id 有自己的范围。actual_response=null 且有 response_read 时代表长回答，先用 read_trial_response(trial_id, start_char) 跟随 next_start 读完整内容，不能当成执行失败。

自订 Skill 也以自身冻结计画评估 typed 与 semantic-probe 回答。提交被接受才完成。

stage=situation_judgment 时，本答案正确性方法没有情境标准。依 judgment_contract 提交 validity=indeterminate、behavior=indeterminate、evidence_turn_ids=[]，reason 解释须在自订 Skill 定义完整情境标准。没有 plan 工具。若自行补上标准，必须引用实际轮次 ID 并保留生成限制。

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
