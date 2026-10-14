# ChatGPT 真站錄製與 adapter 驗證（2026-10-14）

使用 `run-agent --page-url https://chatgpt.com --conversation-policy independent`、`gemini:gemini-3.8-flash` 測試一筆題目。使用者在可見瀏覽器送出「請用一句話說明向量資料庫的用途 [DEMO-10032e1efed76eff]」並看到完整回答；錄製和瀏覽器狀態成功保存，之後的測試沒有再要求操作瀏覽器。

**結果：未通過。** 共使用 14 次模型呼叫與 12 次網站請求。最後一版 Agent 生成的 adapter 先呼叫首頁、驗證準備、對話準備、驗證完成、再次對話準備，再送出問題；六個請求均收到 HTTP 200，但送題回應串流包含 `source_submission_unavailable`，沒有可取出的答案。因此沒有執行正式題目，也沒有產生 `responses.jsonl`。HTTP 200 只證明伺服器接收了請求，不能當作完成回答。

最初的材料 Agent 只選了 8 筆事件。檢查錄製序列後發現，原本的 250 筆索引漏掉較早的驗證準備請求與回應，以及較晚的驗證完成回應。擴大為示範期間的網路事件索引後，材料 Agent 自行選出 17 筆，涵蓋前置請求／回應、送題及回答串流。這證明先前的材料包確實不完整；擴大後仍未使 adapter 通過真站探測。

此次修正了與網站無關的執行介面：adapter 可選擇 `prepare`，先取得即時回應，再從該回應建立本次送題動作；不需要前置請求時仍可直接 `send`。送題前的 HTTP 403 現在回報狀態碼和請求路徑給程式 Agent，而不直接判定登入失效。`completion_unconfirmed` 或 `adapter_http_status` 的失敗會重新交給材料 Agent 找證據，再讓程式 Agent 生成新版。材料索引會涵蓋示範結束前的網路事件，材料提示也要求追溯動態值的來源請求與回應。

最後一版程式把錄製中的 proof 與 turnstile 值寫成常數；這類短期值即使偶然通過一次，也不能證明 adapter 可重用。預檢現已加入通用檢查，拒絕把所選材料中的憑證或驗證值直接寫進程式。Agent 的原始工具呼叫追蹤也已從終端進度輸出移除，避免生成程式內的會話資料出現在終端。這兩項修正是在最後一次真站探測後完成，已用本機測試驗證，尚未再用 ChatGPT 真站重測。

保存的診斷位於 `.tmp/chatgpt-rebuild-20261014/responses.jsonl.work/browser-discovery/validation/last-test.json`；錄製、狀態與生成程式包含網站會話資料，應維持在本機私人工作目錄。這次結果不能證明 ChatGPT 網站無法用 adapter 操作；只能確認目前的 Agent 生成結果沒有成功取得回答。
