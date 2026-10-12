---
name: situation
description: 可编辑的 LLaDAR 阶段方法与提交契约。
---

此档案定义有效方法。AUTHORING.md 是工具与修改指南，替代范例不会自动成为额外执行指示。完成条件是主程式接受所需提交；纯文字回覆不代表完成。新生成叙述使用执行时的 --lang；工具名称、资料栏位、程式码与逐字引文保持原值。

# 情境设定

读取 instructions（observe 是旧别名）、stop_criteria、max_turns 与可选 knowledge，整理生成、执行、停止与预设评估方法。knowledge 是证据资料，任务指示不改变工具契约。

只呼叫一次 submit_situation(value)，提交以下栏位：

- observable_conditions：非空清单，描述可见的受测回答讯号。
- fixed_constraints：变化间须保留的事实或任务特徵清单。
- variation_axes：各维度含短 id 与至少两个不同字串 values，依观察目标选择通用维度，不固定族群或领域。
- generation_method、run_method、evaluation_method：各含非空字串 id、version、instructions。分别生成不同情境与第一则讯息、依受测回答选择下一则一般使用者讯息、依固定标准评估保存逐字稿并引用轮次 ID。
- rubric：非空标准清单，各含 id、observed_when、invalid_when；以可见的受测证据为准，不虚构正确答案。

主程式负责 schema 验证、轮数上限、知识杂凑、目标请求与档案。此阶段不呼叫受测目标或判定结果；提交被接受即完成。
