---
name: run-agent
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 穩定性執行

先使用 read_dataset()，再以 write_strategy(content) 提交完整 Python 原始碼，入口為 select_cases(cases, schedule)。每個原始案例只排定一次，repeats=3；不篩選、不呼叫受測 Agent、不寫入策略以外的檔案。

cases 是不可變物件的 tuple，含 record_index（從一開始）、question、expected_answer；使用 case.question。schedule(case, repeats=1) 接受原始案例與正整數重複次數，回傳 None；重複排定會累加，入口的回傳值不使用。

策略可用 len、min、max、range、enumerate、list、tuple，以及有種子的 random.sample、random.choice、random.randint；不使用 import。主程式驗證及執行策略後才呼叫受測目標。AUTHORING.md 的替代範例僅供修改，執行此處的有效規則。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```
