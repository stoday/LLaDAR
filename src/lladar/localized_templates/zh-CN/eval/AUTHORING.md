# 作者指南

## 从这里开始

要改变等价表达、必要条件或额外评估维度，请修改 `SKILL.md` 的判定规则。

```sh
lladar eval RESPONSES.jsonl --skill ./my-eval --output evaluation.json
```

有效执行指示位於 SKILL.md。本指南是参考，不会注册为另一个 Skill。替代范例须由作者取代原规则後才采用；仅修改本指南不会改变执行方法。工具名称、参数栏位与程式码契约保持原值。

依下方有效方法与完整范例修改选择、评估或叙述规则。主程式持续负责资料验证、档案、受测 Agent 请求与确定性统计；修改 Skill 不会改变这些界线。

## 方法与提交契约

# 答案判定

stage=plan 时以 submit_plan 提交布林 correct 维度的计画。stage=judgment 时使用冻结计画，仅比较 expected_answer 与 actual_response，以 submit_judgment 提交 values 与非空 reason。受支持时 correct=true，不相符时 false；证据无法判定时使用 null 并解释。不计算汇总统计。

确认提交 status；格式被拒绝时，使用 revise_plan／revise_judgment，传入 item_id、新 request_id、目前 revision 与完整修正内容，最多修正三次。使用 get_plan／get_judgment 查询不确定结果。已接受的计画不得修改。使用指定 trial_id 与 plan_version，各项 request_id 有自己的范围。actual_response=null 且有 response_read 时代表长回答，先用 read_trial_response(trial_id, start_char) 跟随 next_start 读完整内容，不能当成执行失败。

自订 Skill 也以自身冻结计画评估 typed 与 semantic-probe 回答。提交被接受才完成。

stage=situation_judgment 时，本答案正确性方法没有情境标准。依 judgment_contract 提交 validity=indeterminate、behavior=indeterminate、evidence_turn_ids=[]，reason 解释须在自订 Skill 定义完整情境标准。没有 plan 工具。若自行补上标准，必须引用实际轮次 ID 并保留生成限制。

## 修改范例（仅供作者参考）

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
