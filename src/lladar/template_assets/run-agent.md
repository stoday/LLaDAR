# 編寫 run-agent Skill：選哪些案例、各跑幾次

這是給 Skill 作者閱讀的指南。執行規則放在 `SKILL.md`；以下替代範例不會自行取代預設規則。
本檔不會自動成為另一個 Skill，但 Agent 可以主動把它當參考資源讀取。修改策略時，
請替換 `SKILL.md` 的原規則，保留單一明確策略，避免同時要求全量與抽樣。

## 用途與修改位置

此 Skill 控制案例選取與執行次數，適合快速抽樣、指定案例或重複測試穩定性。
編輯 `SKILL.md` 的「Stability run」段落：預設每題跑三次；改次數就替換 `repeats=3`，
改為抽樣就替換「every provided case」規則。受測 Agent 的回答由主程式在排程完成後取得。

## Agent 工具完整清單

| 工具 | 參數與用途 | 回傳與下一步 |
| --- | --- | --- |
| `read_dataset()` | 無參數，閱讀目前可排程的題目與預期答案 | 格式為 `[1] Q: ...` 換行 `Expected: ...` 的字串；據此決定策略 |
| `write_strategy(content)` | `content` 是定義 `select_cases` 的完整 Python 原始碼字串 | `strategy.py was written`；先檢查非空與語法，之後主程式執行並驗證排程 |

先讀資料，再透過工具提交程式；只在聊天中貼程式不算提交。工具會寫到主程式指定的位置。

## `select_cases(cases, schedule)` 是什麼

這是 Agent 寫在策略程式裡、供 LLaDAR 呼叫的函數入口，不是 Agent 可呼叫的工具。
主程式自動傳入以下參數，不需要作者自行建立它們。

| 名稱 | 型態與內容 | 用法 |
| --- | --- | --- |
| `cases` | 唯讀 tuple，每項是 `StrategyCase` 物件 | `for case in cases` 或 `cases[:5]` 選取原案例 |
| `case.record_index` | 從一開始的整數編號 | `case.record_index <= 5` 選前五題 |
| `case.question` | 問題字串 | `"refund" in case.question.lower()` 依題目選取 |
| `case.expected_answer` | 預期答案字串 | 可供選取參考，尚未包含受測 Agent 的回覆 |
| `schedule` | 主程式提供的回呼函數 | `schedule(case, repeats=3)` 登記案例與次數 |

案例用屬性存取：`case.question`，不是 `case["question"]`。
`schedule(case, repeats=1)` 回傳 `None`；`repeats` 必須是正整數，不能是布林值。
`case` 必須是 `cases` 中的原物件，不能自己造字典或複製一個物件。
同一案例多次登記會累加次數；案例順序依首次登記的順序。
`select_cases` 的回傳值不被使用；只 `return cases` 不會建立排程。
例如兩題各登記一次 `repeats=3`，會產生六次受測 Agent 請求。

## 策略程式提供的完整環境

可用內建函數只有 `len`、`min`、`max`、`range`、`enumerate`、`list`、`tuple`。
也可寫迴圈、條件、切片及使用字串方法。沒有 `import`、`open`、`print`、`sorted`、
網路或受測 Agent 呼叫介面；這些限制是策略程式的執行介面。

另外提供固定 seed 的 `random` 物件，不需 import：

| 方法 | 參數、回傳與使用情境 |
| --- | --- |
| `random.sample(population, k)` | 從序列抽 `k` 個不重複項目，回傳 list；`0 <= k <= len(population)`；快速抽樣 |
| `random.choice(population)` | 從非空序列抽一項，回傳原項目；單題冒煙測試 |
| `random.randint(a, b)` | 回傳包含兩端的整數；選取正整數範圍的重複次數 |

相同輸入、程式及 seed 可重現抽樣。

## 完整替代範例

這些 Python 區塊是 `write_strategy(content)` 的 content 範例，供作者選擇一種方法。
把選定的規則寫進 `SKILL.md`，要求 Agent 先讀資料再提交程式。

### 穩定性：每題三次（預設）

```python
def select_cases(cases, schedule):
    for case in cases:
        schedule(case, repeats=3)
```

### 快速檢查：隨機最多五題，各一次

```python
def select_cases(cases, schedule):
    for case in random.sample(cases, min(5, len(cases))):
        schedule(case, repeats=1)
```

可取代原預設段落的 Skill 指令：

> Read the dataset with read_dataset. Use write_strategy to submit Python defining
> select_cases(cases, schedule). Select min(5, len(cases)) cases with the supplied
> random.sample helper and schedule each selected case once with repeats=1.
> Finish after submitting the strategy; the host executes the target.

### 指定範圍：前五題，各兩次

```python
def select_cases(cases, schedule):
    for case in cases:
        if case.record_index <= 5:
            schedule(case, repeats=2)
```

也可換成題目字串條件；沒有符合案例時會得到空排程。

## 原有執行契約

Use `SKILL.md` with `lladar run-agent DATASET.jsonl --project PROJECT --skill
DIRECTORY --output responses.jsonl`. This Skill selects cases and repeat
counts. The target Agent still supplies the actual responses.

The request has `stage: select_cases`. Call `read_dataset()` first; it returns
each numbered question and expected answer as text. Then call
`write_strategy(content)` with Python source defining
`select_cases(cases, schedule)`. The host checks that the source compiles and
runs it with supplied `cases`, `schedule`, and deterministic `random` helper.
Each case has `record_index`, `question`, and `expected_answer`.
Call `schedule(case, repeats=1)` for each selected case; repeats must be a
positive integer. Complete one valid strategy that schedules the intended
cases. The host then invokes the target Agent and records trials.

The bundled `run-agent-stability` method has a trusted `strategy.py` that the
host executes without a scheduling model call. A generated custom Skill lives
at a different path, so it uses the Skill agent and `write_strategy` tool.
Copying `strategy.py` does not enable the bundled fast path. Keep target
execution and adapter discovery separate from scheduling instructions.
