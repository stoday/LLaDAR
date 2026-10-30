# 作者指南

## 從這裡開始

修改 `SKILL.md` 的選題與重複次數規則；主程式會負責執行受測 Agent。完成後執行：

```sh
lladar run-agent DATASET.jsonl --project PROJECT --skill ./my-run-agent --output responses.jsonl
```

比較不同策略時使用新的輸出路徑。

有效執行指示位於 SKILL.md。本指南是參考，不會註冊為另一個 Skill。替代範例須由作者取代原規則後才採用；僅修改本指南不會改變執行方法。工具名稱、參數欄位與程式碼契約保持原值。

依下方有效方法與完整範例修改選擇、評估或敘述規則。主程式持續負責資料驗證、檔案、受測 Agent 請求與確定性統計；修改 Skill 不會改變這些界線。

## 方法與提交契約

# 穩定性執行

先使用 read_dataset()，再以 write_strategy(content) 提交完整 Python 原始碼，入口為 select_cases(cases, schedule)。每個原始案例只排定一次，repeats=3；不篩選、不呼叫受測 Agent、不寫入策略以外的檔案。

cases 是不可變物件的 tuple，含 record_index（從一開始）、question、expected_answer；使用 case.question。schedule(case, repeats=1) 接受原始案例與正整數重複次數，回傳 None；重複排定會累加，入口的回傳值不使用。

策略可用 len、min、max、range、enumerate、list、tuple，以及有種子的 random.sample、random.choice、random.randint；不使用 import。主程式驗證及執行策略後才呼叫受測目標。AUTHORING.md 的替代範例僅供修改，執行此處的有效規則。

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

## 修改範例（僅供作者參考）

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
