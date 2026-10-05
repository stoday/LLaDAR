# Editing this report-writing Skill

## 作者指南：用途、工具與修改範例

本檔供作者參考，實際指令在 `SKILL.md`。指南不會自動成為 Skill，但仍可被 Agent 作為資源讀取。
修改 `SKILL.md` 的文字規則，可調整閱讀對象、語氣、重點及限制的表達。
例如加上「用繁體中文向產品負責人解釋結果，優先討論執行失敗與證據限制」。
統計與表格由主程式產生，不需要作者要求模型重算。

| 介面 | 資料形狀與用途 |
| --- | --- |
| 請求 `stage` | `report`，表示撰寫一次報告敘述 |
| 請求 `facts` | 物件：一般評估包含 `plan`、`summary`、`aggregates`、`stability`；情境評估包含 `observe`、`summary`、`method`、`trial_evidence` |
| 唯一 LLaDAR 工具 `submit_report(narrative)` | narrative 是恰好含 `overview`、`findings`、`limitations` 的物件，三個值都必須是非空字串 |

`overview` 用於開頭，`findings` 解釋結果，`limitations` 說明證據能支持到哪裡。
工具回傳 `{"accepted": true}` 或驗證錯誤；成功提交一次才算完成。
敘述不得包含數字字元（包含年份、百分比、編號）；數值直接由已保存資料渲染成表格。
一般請求沒有逐筆原始回答，不能要求模型從未提供的內容引用個別案例。

呼叫 `submit_report(narrative)` 的完整參數值範例（文字仍須依真實 facts 撰寫）：

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```

## 執行契約

Use `SKILL.md` with
`lladar report EVALUATION.json --skill DIRECTORY --output report.md`.
The agent receives `stage: report` and saved `plan`, `summary`, `aggregates`,
and `stability` facts from the evaluation.

Call `submit_report(narrative)` once with exactly three nonempty strings:
`overview`, `findings`, and `limitations`. The tool returns
`{"accepted": true}` or a validation error. Keep prose grounded in the
supplied facts and free of numeric claims; the host rejects prose containing
digits. Completion is one accepted narrative.

LLaDAR renders counts, rates, distributions, stability, question-type tables,
and the trial appendix from saved evaluation data. The Skill changes the
interpretation and wording, not those measurements or tables.
