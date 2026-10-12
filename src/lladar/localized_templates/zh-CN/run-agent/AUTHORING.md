# 作者指南

有效执行指示位於 SKILL.md。本指南是参考，不会注册为另一个 Skill。替代范例须由作者取代原规则後才采用；仅修改本指南不会改变执行方法。工具名称、参数栏位与程式码契约保持原值。

依下方有效方法与完整范例修改选择、评估或叙述规则。主程式持续负责资料验证、档案、受测 Agent 请求与确定性统计；修改 Skill 不会改变这些界线。

## 方法与提交契约

# 稳定性执行

先使用 read_dataset()，再以 write_strategy(content) 提交完整 Python 原始码，入口为 select_cases(cases, schedule)。每个原始案例只排定一次，repeats=3；不筛选、不呼叫受测 Agent、不写入策略以外的档案。

cases 是不可变物件的 tuple，含 record_index（从一开始）、question、expected_answer；使用 case.question。schedule(case, repeats=1) 接受原始案例与正整数重复次数，回传 None；重复排定会累加，入口的回传值不使用。

策略可用 len、min、max、range、enumerate、list、tuple，以及有种子的 random.sample、random.choice、random.randint；不使用 import。主程式验证及执行策略後才呼叫受测目标。AUTHORING.md 的替代范例仅供修改，执行此处的有效规则。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

## 修改范例（仅供作者参考）

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

```python
def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case, repeats=1)
```

```python
def select_cases(cases, schedule):
    for case in cases:
        if case.record_index <= 5:
            schedule(case, repeats=2)
```
