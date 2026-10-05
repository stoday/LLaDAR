# Editing this evaluation Skill

## 作者指南：用途與修改位置

本檔的範例供作者參考，執行規則放在 `SKILL.md`。指南不會自動註冊成 Skill，
但 Agent 可主動把它當參考資源讀取。選擇替代準則時，請替換原規則，避免準則衝突。
這個 Skill 適合修改自由回答的評估，例如容許同義表達、要求重要條件完整，或額外判斷答案是否簡潔。
修改 `SKILL.md` 的判斷準則及提交契約；若增加維度，也要修改 plan 與 judgment 中對應欄位。
只有修改 AUTHORING.md 不會替代執行規則。

### 請求、工具與參數

| 階段 | 請求資料 | 該階段全部 LLaDAR 工具 |
| --- | --- | --- |
| `plan` | `records` 是最多二十五筆已完成 trial 的 list | `submit_plan(plan)`，提交整個評估方法物件 |
| `judgment` | 本次 trial 欄位直接位於請求頂層（沒有巢狀 record 或 plan） | `submit_judgment(judgment)`，提交這一筆的維度值及理由，主程式依已保存 plan 驗證 |

record 包含 `question`（測試問題）、`expected_answer`（來源支持的答案）、
`actual_response`（受測 Agent 的文字回答），並有 record_index 與 trial 識別。
工具參數是物件，不是檔案路徑。plan 定義整批共用的評估規則，judgment 是單筆結果。
兩個工具都回傳 `{"accepted": true}`，無效輸入會產生驗證錯誤；修正後再提交。

### 完整提交範例

呼叫 `submit_plan(plan)` 的 plan 值：

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

呼叫 `submit_judgment(judgment)` 的 judgment 值：

```json
{"values": {"correct": true}, "reason": "The response states the expected answer with all required conditions."}
```

boolean 維度填 true/false；categorical 填字串；numeric 填數值；無法判斷可填 null 並說明理由。
`values` 的欄位必須符合固定 plan，不能每筆自行新增維度。
例如新增 `concise` 布林維度後，每筆 judgment 都須提交 `correct` 與 `concise`。
單筆判斷不等於統計，總數、比例與穩定性由主程式計算。

## 執行契約與特殊資料模式

The active instructions are in `SKILL.md`. Use the skill with
`lladar eval RESPONSES --skill DIRECTORY --output EVALUATION.json`.

## Agent work

When all completed trials use free responses, the agent first receives a
`plan` request with up to 25 completed records. Call `submit_plan(plan)` with exactly `title`, `approach`,
`dimensions`, and `limitations`. Each dimension has `name`, `description`, and
`kind` (`boolean`, `categorical`, or `numeric`). Include a boolean `correct`
dimension. The tool returns `{"accepted": true}` or a validation error.

When typed or semantic-probe trials are present, LLaDAR supplies a fixed plan
instead. For each assigned free-response `judgment` request, compare `expected_answer` with
`actual_response`. Call `submit_judgment(judgment)` with `values` for every
planned dimension and a nonempty `reason`. In a mixed dataset, submitting only
`correct` lets the host fill the typed/probe-only dimension with `null`. The tool returns
`{"accepted": true}` or a validation error. Complete one accepted judgment
per free-response trial.

LLaDAR judges typed answer protocols and verified semantic probes in code.
It calculates coverage, aggregates, and stability from the saved judgments.
An execution error has no response to judge. Situation evaluation with
`--situation-config` has its own frozen method and cannot use `--skill`.

Start with the bundled instructions in `SKILL.md`, then change one judgment
rule at a time and compare the saved evaluation results.
