# 自訂 Skill 範本：實際操作範例

範本已包含完整執行流程。一般只需開啟 `SKILL.md`，修改「自訂需求（修改這裡）」到
「執行契約（進階）」之間的文字，用平常說話的方式描述想要的結果。
目前繁體中文與英文範本提供這個編輯區塊；其餘語言維持原本範本。

| 步驟 | 可以改什麼 | 可以怎麼說 |
| --- | --- | --- |
| 產生題目 | 對象、範圍、問句風格與答案要求 | 用一般遊客的口吻詢問交通與開放時間，答案保留限制條件。 |
| 執行 Agent | 全量或抽樣、每題次數 | 隨機抽五題，每題測三次；不足五題時執行全部。 |
| 評估回答 | 通過條件、允許差異、不可接受的錯誤 | 接受同義說法，但日期、金額與適用條件不能錯。 |
| 產生報告 | 讀者、語氣、重點順序 | 給客服主管閱讀，先說主要問題，再列代表案例與改善建議。 |

多輪情境使用 `situation-skill`，在同一區塊描述觀察目標與可見的成功／失敗證據。
例如：「資訊不足時應先追問日期；直接宣稱預約成功不符合期待。」

請替換原有需求，保留一組一致的規則；保留進階區塊的工具名稱、欄位與提交流程。
`AUTHORING.md` 提供正反例、試跑方式與進階契約；只改指南不會改變執行需求。
先用少量資料與新的輸出路徑試跑，檢查結果是否符合自己的例子。
自然語言需求提供行為指引，不保證固定題數或完整覆蓋率。
只想調整評估標準時，也可直接使用 `eval --criteria`，不必建立 Skill。

這個例子用一筆已保存的 Agent 回覆，經過自訂 `eval` Skill 評估，再用自訂 `report` Skill 寫報告。它會實際呼叫 Gemini；請先依目前的 LLaDAR 設定準備 `.env`，或用 `--env-file` 指向你的設定檔。建立範本本身不呼叫模型。

## 1. 準備輸入

在一個空的工作目錄建立 `responses.jsonl`，內容只有一行：

```json
{"question":"What is 2 + 2?","expected_answer":"4","actual_response":"The answer is 4."}
```

檔案需以 UTF-8 儲存。這三個欄位分別是測試問題、預期答案和先前取得的受測 Agent 回覆；此範例從評估階段開始，因此不會再次執行受測 Agent。

## 2. 產生並檢閱評估 Skill

```sh
lladar create eval-skill --output ./my-eval --lang zh-TW
```

開啟 `./my-eval/SKILL.md`，先保留預設需求完成基準執行；之後只改「自訂需求」，再用新的輸出檔比較結果。需要例子時閱讀 `./my-eval/AUTHORING.md`。

```sh
lladar eval ./responses.jsonl --skill ./my-eval --output ./evaluation.json --model gemini:gemini-2.5-flash
```

成功時 CLI 會顯示 `Evaluated 1 record(s)`；`evaluation.json` 的 `items[0].values.correct` 應為 `true`。模型的文字理由可能不同。

## 3. 產生並使用報告 Skill

```sh
lladar create report-skill --output ./my-report --lang zh-TW
lladar report ./evaluation.json --skill ./my-report --output ./report.md --model gemini:gemini-3.7-flash
```

閱讀 `report.md`。Skill 撰寫摘要、發現與限制；統計表取自 `evaluation.json`。修改 `./my-report/SKILL.md` 後，用另一個報告輸出路徑重跑即可比較敘述。

## 其餘範本與預設位置

每個目錄內的 `SKILL.md` 是實際執行方法，`AUTHORING.md` 是撰寫指南，包含用途、可修改的規則、
工具參數、回傳值與完整範例。這份指南就是 guide.md 的角色，不需要再建立重複文件。
以 run-agent 為例，指南會解釋 `cases` 是案例物件序列、`schedule` 是主程式提供的登記函數，
以及 `select_cases` 如何選題與設定次數，並提供全量、抽樣和條件選取範例。

參考檔不會自動成為另一個 Skill，但 Agent 可以透過資源工具主動讀取。
因此指南的替代範例只供作者選擇；採用時請替換 `SKILL.md` 中的原策略，避免互相矛盾的規則。

`lladar create test-dataset-skill`、`situation-skill`、`run-agent-skill`、`eval-skill`、`report-skill` 都可省略 `--output`，分別建立在目前目錄的 `./lladar-skills/<stage>/`。每次命令會印出帶有該路徑的使用範例。目的目錄若已存在，命令預設停止。要用最新範本蓋掉原本的 `SKILL.md` 與 `AUTHORING.md`（包含你的修改），加上 `--force`；目錄內其他檔案保留：

```sh
lladar create run-agent-skill --output ./my-run-agent --force
```

本機驗證紀錄（2026-10-02）：在此 checkout 以產生的 `my-eval` 和 `my-report` Skill 真實呼叫 Gemini，得到 1 筆已評估紀錄、`correct=true`，並成功產出 Markdown 報告。這是上述最小範例的實際結果，不代表所有自訂準則或目標 Agent 都已驗證。
