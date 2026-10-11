# CrowS-Pairs 四階段實測

日期：2026-10-11。環境：Windows、Python 3.11；模型：`gemini:gemini-3.7-flash`。

## 結果

修正驗收腳本的 callable 參數後，生成、run-agent、eval、report 均已完成，未遇到資料交接錯誤。報告三個必要段落通過獨立模型查驗。四階段已完成 session 的接續檢查也全部通過，沒有再次呼叫模型或目標 Agent。

| 階段 | 實際結果 | 執行時間 |
| --- | --- | ---: |
| 生成 | 3 題；3 題模型查驗通過，排除 0 題 | 190.23 秒 |
| run-agent | 3 筆真實 Gemini 回答，目標僅接收 question | 22.31 秒 |
| eval | 3／3 已評估；execution_error=0、judge_error=0；本次正確判定 3／3 | 170.50 秒 |
| report | 三段接受，semantic_review=passed，正式報告已發布 | 60.03 秒 |

這是三題衍生 QA 的功能與資料交接測試，不是全資料集測試，也不是 CrowS-Pairs 原始 benchmark 分數。人工審閱尚未完成。

## 來源與選取範圍

- 來源：[nyu-mll/crows-pairs](https://github.com/nyu-mll/crows-pairs)。
- 固定 commit：`8aaac11c485473159ec9328a65253a5be9a479dc`。
- 主檔：`data/crows_pairs_anonymized.csv`；實際 1508 列，437764 bytes，約 428 KiB。
- Agent 自行選取邏輯資料列 1、2、3、7、10、11、18、19、31、35、36、40 作為生成依據。保存的 CSV 核對結果涵蓋 9 種 bias_type；這不代表最終三題涵蓋全部類別。
- 最終題目依據資料列 2、11、35，分別為 socioeconomic、nationality、physical-appearance。三題提供完整句子對與分類選項，答案依據均標記為 `source_annotation`，未宣稱來源是原始 QA。
- 逐題核對 3 個來源引文：保存的 normalized_text 之字元範圍與 quote 完全一致，資料集 SHA-256 相符。

主檔小於一般下載 2 MiB 上限；本次没有觸發超過 2 MiB 的串流抽樣路徑，不能作為 GB／TB 級或超限下載的驗證。

## 設定與驗收腳本問題

生成、eval 及 report 使用 128000 輸入／4096 輸出 token 額度；eval 使用 strict=True。受測 callable 使用 AkashaProvider 呼叫 Gemini、temperature=0、max_output_tokens=4096，僅接收 question。執行策略為內建 run-agent-random-sample，三個試次各產生一筆實際回答。

初次驗收腳本將 max_input_tokens／max_output_tokens 傳入 callable 的 run_agent，受到「僅適用於 project target」的既有參數驗證拒絕。修正腳本後，沿用已生成資料集，改以 responses-v2.jsonl 執行，沒有重產題目；失敗 session 與初始問題保留。這項腳本錯誤不計為目標 Agent 的 execution_error。

## 保存證據

本機產物位於 `.tmp/crows-pairs-acceptance-2026-10-11/`，未納入版本控制：

- [逐題生成查驗報告](../../.tmp/crows-pairs-acceptance-2026-10-11/dataset.generation-report.md)
- [最終報告](../../.tmp/crows-pairs-acceptance-2026-10-11/report.md)
- [四階段驗收結果](../../.tmp/crows-pairs-acceptance-2026-10-11/acceptance.json)
- [來源引文檢查](../../.tmp/crows-pairs-acceptance-2026-10-11/source-check.json)
- [四階段接續檢查](../../.tmp/crows-pairs-acceptance-2026-10-11/resume-check.json)

資料檔另有 dataset.jsonl、生成 sidecar／來源快照、responses-v2.jsonl 與 run／trial sidecar、evaluation.json、report.md.report.json，以及各階段的 .work 目錄。接續檢查以會拒絕任何外部呼叫的 factory／target 執行公開 API，確認完成的 session 直接回傳既有成果。
