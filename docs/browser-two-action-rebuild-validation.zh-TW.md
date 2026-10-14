# `run-agent --page-url` 兩動作重建驗證（2026-10-14）

本次重建把生成的 `adapt(payload)` 限為 `send`、`receive` 兩種操作。宿主負責 HTTP／WebSocket 執行、Cookie、逾時、提交保護與真實事件引用。材料 Agent 選錄製事件；每取得一包新材料，寫程式 Agent 必須在下一個模型呼叫提交完整候選，宿主立即預檢並真站試跑。寫程式 Agent 沒有錄製搜尋工具。

局部測試覆蓋：HTTP 真實本機服務的兩題送收、編碼示範題替換、WebSocket 二進位分段、不中斷連線的串流完成、已送題回合重載恢復、預檢錯誤定位、兩個 Agent 的工具隔離與每次選材後強制提交，以及 CLI 入口。這些測試不能取代真站驗證。

真 Gemini 驗證使用先前人工完成並保存的 `gemini.google.com` 示範錄製，在全新隔離工作階段重新選材與生成，沒有沿用舊候選。材料 Agent 一次選材後，寫程式 Agent 於第 2 次模型呼叫提交第一版，宿主實際送出請求並收到 HTTP 回應。第一版在讀取回應時出現 `protocol_execution_error`（第 81 行、`AttributeError`）。第二版在保留第一版與診斷的情況下提交，真站送收完成約 2,071 字回答，並通過模型合理性審核。

試跑同時暴露一個宿主錯誤：它把完整試次事件再次寫入已滿的示範錄製，導致 `recording_capture_limit` 蓋過試跑成功結果。已改成只保存在獨立的 runtime 回合檔；從完整、已驗證的第二版回合完成資格化，沒有重送該題或手改 adapter。原隔離工作階段累計 5 次模型呼叫、2 次網站請求；其中最後一次模型呼叫在定位宿主錯誤時中止，沒有第三版提交。

接著用該 Agent 生成的 manifest，透過正式 CLI `run-agent --page-url --adapter` 重載，在同一已示範對話模式實測一題、一次正式試次。結果：未開瀏覽器、2 次網站請求（資格化 probe 與正式題）、2 次模型呼叫（各回合合理性審核）、正式試次 `ok`，回答 236 字，輸出 `responses.jsonl`、`trials.jsonl` 與 `run.json`。私有錄製、登入狀態、adapter 與結果位於 `.tmp/gemini-rebuild-20261014/`，不應提交至版本庫。

此證據證明新介面可在 Gemini 真站完成生成、修復、資格化及 CLI 重載執行；目前沒有證明新版在 EIP 或其他網站成功，也沒有證明預設的獨立新對話政策成功。本輪較廣測試中的兩個失敗屬未使用的舊 `BrowserTarget` 測試，分別依賴舊人工輸入與舊 driver 介面；正式 CLI 目前走 `IndependentTarget`。
