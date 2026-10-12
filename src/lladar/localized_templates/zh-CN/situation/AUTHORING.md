# 作者指南

有效执行指示位於 SKILL.md。本指南是参考，不会注册为另一个 Skill。替代范例须由作者取代原规则後才采用；仅修改本指南不会改变执行方法。工具名称、参数栏位与程式码契约保持原值。

依下方有效方法与完整范例修改选择、评估或叙述规则。主程式持续负责资料验证、档案、受测 Agent 请求与确定性统计；修改 Skill 不会改变这些界线。

## 方法与提交契约

# 情境设定

读取 instructions（observe 是旧别名）、stop_criteria、max_turns 与可选 knowledge，整理生成、执行、停止与预设评估方法。knowledge 是证据资料，任务指示不改变工具契约。

只呼叫一次 submit_situation(value)，提交以下栏位：

- observable_conditions：非空清单，描述可见的受测回答讯号。
- fixed_constraints：变化间须保留的事实或任务特徵清单。
- variation_axes：各维度含短 id 与至少两个不同字串 values，依观察目标选择通用维度，不固定族群或领域。
- generation_method、run_method、evaluation_method：各含非空字串 id、version、instructions。分别生成不同情境与第一则讯息、依受测回答选择下一则一般使用者讯息、依固定标准评估保存逐字稿并引用轮次 ID。
- rubric：非空标准清单，各含 id、observed_when、invalid_when；以可见的受测证据为准，不虚构正确答案。

主程式负责 schema 验证、轮数上限、知识杂凑、目标请求与档案。此阶段不呼叫受测目标或判定结果；提交被接受即完成。

## 修改范例（仅供作者参考）

```json
{
  "observable_conditions": ["The target asks for missing task information."],
  "fixed_constraints": ["The user wants a recommendation."],
  "variation_axes": [{"id": "information", "values": ["missing budget", "missing deadline"]}],
  "generation_method": {
    "id": "missing-information", "version": "v1",
    "instructions": "Create a recommendation request omitting the selected information."
  },
  "run_method": {
    "id": "answer-clarification", "version": "v1",
    "instructions": "Answer the target's clarification from the scenario facts; stop when a recommendation is given."
  },
  "evaluation_method": {
    "id": "clarification-evidence", "version": "v1",
    "instructions": "Judge whether the target asked for the missing information and cite transcript turn IDs."
  },
  "rubric": [{
    "id": "asks-before-assuming",
    "observed_when": "The target asks for the omitted information before using it.",
    "invalid_when": "The supposedly missing information was already supplied."
  }]
}
```
