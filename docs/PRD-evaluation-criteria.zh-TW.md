# 評估準則與完整 Skill 的互斥入口

日期：2026-10-07。狀態：已實作，CLI／公開 API 的 fixture 驗收通過；真實模型驗收待執行。

## 問題與目標

一般使用者應能直接描述評估需求，不必先建立 Skill。目前 Skill 同時承擔判定標準與作業方法，情境評估又被原始 config 綁定，難以用不同標準重新分析同一批回答。

本次提供兩個互斥入口：`--criteria TEXT` 是一般使用者的評估需求；`--skill DIRECTORY` 是進階使用者提供的完整評估方法，包含自己的標準與作業步驟。兩者不設優先順序，同時指定即拒絕。未指定時沿用現有預設。

## CLI 與公開介面

- `lladar create situation --instructions TEXT`（或 `--instructions-file PATH`）描述整體情境作業，仍一次規劃生成、執行、停止與預設評估。既有 `--observe`／`--observe-file` 作為相同入口的相容別名；同時指定兩種來源拒絕。既有停止條件與輪數引數維持。
- `lladar eval RESPONSES [--criteria TEXT | --skill DIRECTORY]` 支援單題與 `--situation-config PATH` 情境模式。
- `evaluate(..., criteria=None, skill=None)` 與 `evaluate_situation(..., criteria=None, skill=None)` 必須在公開 API 層也驗證互斥與非空準則。
- 長篇準則本次可由呼叫端讀取後傳入；不增加 criteria-file 或其他新入口。

## 模式選擇與評估契約

| 輸入 | 未指定 | criteria | skill |
| --- | --- | --- | --- |
| 單題 | 既有答案正確性／typed／probe 流程 | 內建準則評估方法，依文字需求建立維度並判斷所有完成回答 | 選定 Skill 提供完整維度與方法 |
| 情境 | config 固定方法與 rubric | 準則取代原始評估目標與 rubric；保留原始情境有效性限制 | Skill 提供完整判斷；保留情境有效性與證據引用契約 |

criteria 或自訂 Skill 明確選取時，不得被 typed／probe 的預設評估方法無聲取代。原有 sidecar 身分與內容核對仍須保留。單題準則允許 boolean、categorical、numeric 維度，不強迫額外產生 `correct`。缺少正確性維度時不產生錯誤的正確率／正確性穩定度表。

情境評估仍輸出 `validity`、`behavior`、`evidence_turn_ids`、`reason`；出現行為必須引用實際輪次，錯誤與證據不足不算未出現。自訂情境 Skill 以 `situation_judgment` request 和 `submit_judgment` 工具提交此契約，不重新生成情境或呼叫受測 Agent。

準則是當次評估指示，目標回答與知識內容是證據資料。內建方法依準則工作，不從目標回答接受改寫準則的指示。此功能不新增 shell／任意程式執行工具，也不把 LLM 詞彙判斷宣稱為程式精確偵測。

## 追溯與重新評估

每份新評估結果記錄 `evaluation_settings`：模式（default/config/criteria/skill）、準則全文與 SHA-256（適用時）、實際 Skill 來源與資源證據（適用時），或原始 config 中的固定評估快照。評估計畫、模型與個別判斷仍保存。

情境 sidecar 與原始 config 雜湊照常核對。覆寫只影響記憶體中的本次評估，不改原始 config、responses 或 sidecar；保存另一份 output 即可比較。報告接受舊結果，也呈現新結果實際使用的設定，不能將覆寫後的判斷標成原始目標的結果。

## 歷史劇案例

生成指示要求根據提供文本建立自然問題，每個情境一問一答，問題不直接要求使用目標詞。原始 config 可先規劃詞彙觀察；同批回答後續可以用 `eval --criteria "區分文本引用、用語解釋與角色台詞中的目標詞，詞彙命中不等同偏見"` 重新評估。文本給生成器不等於已配置到受測 Agent，此既有差異須在文件保留。

## 驗收與測試介面

使用者已確認 CLI 和公開 create_situation／evaluate／evaluate_situation／create_report API 及其輸出檔案為測試介面。外部模型使用注入的 fixture Agent／Provider，逐項完成 red → green。

1. CLI 與 API 拒絕 criteria＋skill、空白 criteria；拒絕發生在模型呼叫或輸出前。
2. 情境整體 instructions 編製完整 config，舊別名與舊 config 可繼續使用。
3. 單題 criteria 送達計畫及判斷，允許不含 correct 的維度，結果與報告保留準則。
4. typed／probe 的顯式評估需求不被預設方法取代。
5. 情境 criteria、情境自訂 Skill 均能重新評估原始 transcript，保存證據與有效性，原始檔案位元組不變。
6. 未指定時維持現有預設；執行失敗／判斷失敗／空完成集合維持明確狀態。
7. README 與中英文 CLI 參考同步新契約。

## 不在本次範圍

跨情境自適應搜尋、重複試次排程、受測背景自動注入、任意程式執行能力、專用詞彙偵測器與新的偏見指標。

## 與既有規格的關係

本文件取代 PRD-situation-generation.zh-TW.md 中「情境 eval 禁止 skill」以及「重評必須改 config」的規則；取代單題自訂評估必須包含 correct 的限制。三欄資料與既有預設評估仍維持。

## 驗證紀錄

2026-10-07：20 項新功能測試及相關回歸共 89 項通過。`eval --help` 與
`create situation --help` 已實際核對；中英文 README、CLI 參考與情境指南已同步。
全套非瀏覽器測試發現既有 `test_deadline_kills_dripping_worker_and_does_not_retry`
失敗；另從修改前 HEAD 匯出原始 source 與該測試，於相同環境重現相同失敗。
該項不屬於本次評估功能，未修改其程式或測試。新功能尚未以真實模型完成端到端驗收。
排除已重現的既有失敗後，最終非瀏覽器回歸結果為 309 passed、2 skipped、
51 deselected（50 項瀏覽器測試與該既有失敗）。編譯檢查與 `git diff --check` 通過。
