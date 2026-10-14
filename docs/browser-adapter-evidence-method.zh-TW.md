# 從一次真站示範生成對話 adapter：已驗證的證據與通用方法

這份紀錄使用 2026-10-14 的 ChatGPT 示範與後續真站探針，目的是找出「哪些材料足以寫出送題／收答程式」，再將判斷方式寫成網站無關的 Agent 工作規則。錄製與瀏覽器設定只保存在本機；本文不含 cookie、proof、Turnstile 或 session 值。

## 真正構成一次成功對話的材料

下表的事件號碼來自 `.tmp/chatgpt-rebuild-20261014/responses.jsonl.work/browser-discovery/recording/events.sqlite3`。它們是同一輪示範的因果鏈，而非「所有 POST」。

| 材料 | 證明什麼 | 對 adapter 的用途 |
| --- | --- | --- |
| 保存的瀏覽器設定與 `state/initial.json` | 示範頁面可再開啟；cookie、網站儲存值和允許來源由宿主保存 | 讓網站用自己的程式建立當次有效的環境；值不寫入 adapter 程式碼 |
| 事件 1738（輸入前後的 DOM），1737／1740（點擊及表單提交） | 使用者在可編輯的 `textarea[name="prompt"]` 輸入示範問題並提交表單 | 找到輸入與送出動作，不必猜 CSS class 或只憑請求方法猜測 |
| 事件 1768（送題請求）與 1803（HTTP 回應） | 新問題進入 `/unauth-mweb/conversation/updates` 的 `prompt` 欄位；該次請求有回應 | 確認送題動作確實發出，並可用新問題關聯請求 |
| 事件 2467（1768 的回應 body）與 2485（完成後頁面文字） | 回應串流含 `content`、`terminal-received`、`end`，頁面顯示完成回答 | 確認有回答及完成；單看 HTTP 200 不夠 |
| 事件 988／1579、1660／1866，以及 989／1577 | 網站在送題前取得並結算當次的要求與對話準備值；其中 proof 和 Turnstile 均標記為必需 | **只在要寫純 HTTP adapter 時**追溯這條依賴鏈，不能複製示範時的短效值 |

錄製的輸入 DOM 足以找到 `textarea[name="prompt"]`；這次手寫程式使用的回答容器 `[data-assistant-markdown-root]` 是我在真站探針額外觀察到的，**不在當時選給 Code Agent 的 17 筆材料中**。因此不能聲稱原 Code Agent 單靠那 17 筆就應該產生這支瀏覽器 adapter。錄製器現已在使用者確認完整回答時，另存結構化的 `demonstration_final_dom`，供之後的 Agent 選取回答容器；舊錄製不會憑空補出這筆事件。

## 這次的實際驗證

舊版純 HTTP 方案把錄製時的 proof／Turnstile 值寫進程式。新問題確實送入 `prompt`，HTTP 也回 200，但回應是 `source_submission_unavailable`，沒有 `content` 回答。這證明**那一版重播**失敗，不代表所有純 HTTP 實作都不可能。若能重建每次有效的前置值與送題格式，HTTP 仍應優先測試。

手寫的 [通用瀏覽器 adapter 執行器](../src/lladar/browser_ui_adapter.py) 依網站設定開啟保存的設定檔，填入新問題，送出，等新回答文字穩定後關閉瀏覽器。真站以新題得到 `21`、`25`，以及一段非空的向量資料庫用途回答；本機獨立問答頁的同一執行器測試也通過。這是**每題短暫開啟瀏覽器**的成功基準，並非「錄製後純 HTTP、全程無瀏覽器」已成功；後續正式流程的整合與真站驗證見下文。

真站設定保存在本機忽略目錄 `.tmp/chatgpt-rebuild-20261014/chatgpt-ui-adapter.json`，可用以下方式重跑一題：

```powershell
uv run python -m lladar.browser_ui_adapter --config .tmp/chatgpt-rebuild-20261014/chatgpt-ui-adapter.json "新的測試問題"
```

## 網站無關的 Agent 工作規則

1. **材料 Agent 找一條完整因果鏈。** 用示範題的獨特字串搜尋使用者輸入、送出事件、網路請求或 WebSocket 訊框；沿 request／operation ID 找回應、內容與完成標誌。同時選擇輸入前 DOM、送出動作及完成後 DOM。只選有助於送題、取答或必要前置步驟的材料；對每一個動態值標出「來自哪個回應、何時取得、送到哪裡」。不預設網站一定使用 POST。
2. **寫程式 Agent 只收到已選材料。** 它先提交一版完整的送題／收答 adapter，不得自己重新搜尋。若材料能產生當次有效的所有前置值，先用 HTTP／WebSocket。若材料表明值必須由當次頁面產生，則生成瀏覽器操作設定，讓宿主用保存的設定檔在每題開啟、送答、關閉；不把短效值寫死。兩種方式都要從當次新問題建立請求關聯。
3. **宿主立刻真站測試並提供可修的錯誤。** 檢查「問題確實送出、回應與本題關聯、取得非空完整回答」。HTTP 200 或按鈕點擊成功都不是完成條件。若失敗，回傳去敏後的狀態、回應診斷、缺少的欄位／事件、頁面輸入與回答候選結構。寫程式 Agent 先用現有材料修一版；只有確實缺少材料時，材料 Agent 才針對缺口補找。每包新材料最多一輪生成與立即測試，首版成功即停止。
4. **Agent 能力必須與宿主一致。** 這次已讓正式 `run-agent` 的 Code Agent 可提交 `browser_ui` 動作，由宿主使用保存的 profile 執行。材料 Agent 選擇錄製事件，Code Agent 只根據選出的材料寫送題／收答程式；實際方案仍須通過真站 probe、驗證與正式題目。

選擇傳輸方式的原則是「先根據完整材料寫最少步驟的請求，立即真站驗證；失敗才依錯誤補齊依賴或改用網站客戶端」。瀏覽器模式不需要預先判斷所有按鈕狀態；本次用當次送題請求與新回答容器的穩定文字做完成判斷。其他網站若有不同的送出或完成機制，Agent 必須依錄製材料產生對應設定並經真站測試。

## 2026-10-14 後續整合與 ChatGPT 真站驗證

正式流程已接受 `browser_ui` adapter 動作，錄製器會保存完成回答時的結構化 DOM。宿主把該 DOM 的每個節點精簡後提供給 Code Agent，保留控制項、屬性與回答元素；瀏覽器 profile、可用來源、當次問題與逾時由宿主控制。

這輪 Agent 第一版在程式預檢遇到 `AttributeError`；第二版在 ChatGPT 真站已送題，但因原本的 DOM 長事件預覽略過回答元素，等待回答逾時。改用完整的結構化 DOM 封包後，Agent 第三版自行提交並通過真站 probe、兩題新問題驗證與一題正式測試。整輪共 11 次模型呼叫、5 次網站請求。隨後正式 `run-agent --page-url --adapter` CLI 載入這份 Agent 生成的 manifest，另跑一筆資料並成功寫入非空的 `actual_response`。

證據保存在 `.tmp/chatgpt-browser-ui-20261014/target/adapter/attempts/`、`target/validation/last-test.json`、`target/runtime/turns.json`、`cli-responses.jsonl` 與 `cli-responses.jsonl.run.json`。這證明 ChatGPT 站點的瀏覽器方案與 CLI 接線可行；ChatGPT 的純 HTTP 重播仍未成功，其他網站也尚須各自真測。
