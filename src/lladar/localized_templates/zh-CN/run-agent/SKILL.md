---
name: run-agent
description: 可编辑的 LLaDAR 阶段方法与提交契约。
---

此档案定义有效方法。AUTHORING.md 是工具与修改指南，替代范例不会自动成为额外执行指示。完成条件是主程式接受所需提交；纯文字回覆不代表完成。新生成叙述使用执行时的 --lang；工具名称、资料栏位、程式码与逐字引文保持原值。

# 稳定性执行

## 这个 Skill 的作用与修改位置

它决定要执行哪些数据集案例，以及每个案例重复多少次。修改时编辑下面的选择规则和 `repeats`；受测 Agent 的执行和回答保存由主程式负责。`AUTHORING.md` 提供全量、抽样和范围选择示例。

先使用 read_dataset()，再以 write_strategy(content) 提交完整 Python 原始码，入口为 select_cases(cases, schedule)。每个原始案例只排定一次，repeats=3；不筛选、不呼叫受测 Agent、不写入策略以外的档案。

cases 是不可变物件的 tuple，含 record_index（从一开始）、question、expected_answer；使用 case.question。schedule(case, repeats=1) 接受原始案例与正整数重复次数，回传 None；重复排定会累加，入口的回传值不使用。

策略可用 len、min、max、range、enumerate、list、tuple，以及有种子的 random.sample、random.choice、random.randint；不使用 import。主程式验证及执行策略後才呼叫受测目标。AUTHORING.md 的替代范例仅供修改，执行此处的有效规则。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```
