# 自訂 Skill 範本：實際操作範例

這個例子用一筆已保存的 Agent 回覆，經過自訂 `eval` Skill 評估，再用自訂 `report` Skill 寫報告。它會實際呼叫 Gemini；請先依目前的 LLaDAR 設定準備 `.env`，或用 `--env-file` 指向你的設定檔。建立範本本身不呼叫模型。

## 1. 準備輸入

在一個空的工作目錄建立 `responses.jsonl`，內容只有一行：

```json
{"question":"What is 2 + 2?","expected_answer":"4","actual_response":"The answer is 4."}
```

檔案需以 UTF-8 儲存。這三個欄位分別是測試問題、預期答案和先前取得的受測 Agent 回覆；此範例從評估階段開始，因此不會再次執行受測 Agent。

## 2. 產生並檢閱評估 Skill

```sh
lladar create eval-skill --output ./my-eval
```

閱讀 `./my-eval/SKILL.md` 的評估步驟與 `./my-eval/AUTHORING.md` 的工具契約。先保留原樣完成基準執行；之後可改寫 `SKILL.md` 中判斷正確性的準則，再用新的輸出檔比較結果。

```sh
lladar eval ./responses.jsonl --skill ./my-eval --output ./evaluation.json --model gemini:gemini-2.5-flash
```

成功時 CLI 會顯示 `Evaluated 1 record(s)`；`evaluation.json` 的 `items[0].values.correct` 應為 `true`。模型的文字理由可能不同。

## 3. 產生並使用報告 Skill

```sh
lladar create report-skill --output ./my-report
lladar report ./evaluation.json --skill ./my-report --output ./report.md --model gemini:gemini-3.7-flash
```

閱讀 `report.md`。Skill 撰寫摘要、發現與限制；統計表取自 `evaluation.json`。修改 `./my-report/SKILL.md` 後，用另一個報告輸出路徑重跑即可比較敘述。

## 其餘範本與預設位置

`lladar create test-dataset-skill`、`situation-skill`、`run-agent-skill`、`eval-skill`、`report-skill` 都可省略 `--output`，分別建立在目前目錄的 `./lladar-skills/<stage>/`。每次命令會印出帶有該路徑的使用範例。目的目錄若已存在，命令會停止，請選新的輸出位置。

本機驗證紀錄（2026-10-02）：在此 checkout 以產生的 `my-eval` 和 `my-report` Skill 真實呼叫 Gemini，得到 1 筆已評估紀錄、`correct=true`，並成功產出 Markdown 報告。這是上述最小範例的實際結果，不代表所有自訂準則或目標 Agent 都已驗證。
