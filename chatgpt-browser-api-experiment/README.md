# ChatGPT 瀏覽器紀錄與 API 重播實驗

2026-10-14 實測，使用 Playwright 1.63.0、Chromium 153.0.8010.12，可見瀏覽器、獨立 profile、未登入訪客介面。

已完成：開啟 https://chatgpt.com → 輸入「嗨」→ 送出 → 等待完整回覆；再依紀錄用 Python 連續送出兩次並取得完整答案。原始網站的登入、驗證流程由網站與使用者處理。

後續已實作 `detached.py`：攔截一則尚未送出的新請求，關閉 Chromium、結束 Playwright，再由 Python `httpx` 發送並收完答案。單次移交已成功；關閉瀏覽器後繼續送第二則，尚未成功。

## 執行

在本資料夾中使用專案現有環境：

```powershell
$env:PYTHONIOENCODING='utf-8'
../.venv/Scripts/python.exe probe.py
../.venv/Scripts/python.exe replay.py --count 2
# 瀏覽器準備一則新請求，關閉後由 httpx 送出；預設只送一次。
../.venv/Scripts/python.exe detached.py
# 額外實驗：關閉後繼續第二則，重新呼叫 prepare。目前第二則失敗，程序回傳 1。
../.venv/Scripts/python.exe detached.py --count 2 --refresh-prepare
```

若在其他環境，先建立自己的虛擬環境，安裝 `requirements.txt`，再執行 `python -m playwright install chromium`。

`--count` 範圍 1–20，`--timeout` 預設 180 秒。不要同時執行兩支程式，因為它們使用同一個 profile。

## 實際紀錄

- `artifacts/events.jsonl`：探索過程，包含前期選擇器不符合實際介面的紀錄，以及後續成功的回覆。
- `artifacts/opened.png`、`typed.png`、`completed.png`：開啟、輸入、完成的畫面。
- `artifacts/initial-dom.json`、`after-submit-dom.json`：本次新對話的 DOM 證據。
- `recording.json`：由實際紀錄整理的 URL、輸入框、回覆元素、API 路徑和完成條件；重播程式直接讀取此檔。
- `artifacts/replay-*/events.jsonl`：每次重播的 HTTP 狀態、協定結束訊號、答案與時間。
- `artifacts/replay-*/turn-*-complete.png`：每次完整回覆的畫面。

`profile/` 與 `artifacts/` 都已 Git 忽略。程式不把 cookies、驗證 token、請求 header 值輸出到報告。DOM、圖片可能包含個人資訊，僅保留本機。

## 找到的介面與完成判斷

觀察到輸入框為 `#mobile-composer-prompt`；回覆元素為 `[data-message-role="assistant"]`，完成時有 `data-message-complete` 屬性。回覆正文位於 `[data-assistant-markdown-root]`。

送出過程會使用 `/unauth-mweb/conversation/prepare` 和 Sentinel prepare/finalize；實際生成回覆為：

```text
POST /unauth-mweb/conversation/updates
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Response: application/vnd.openai.conversation-source+ndjson
```

成功串流含 `control → shared/content/state → end`。`replay.py` 同時要求 HTTP 200、正確 Content-Type、最後一筆 `type=end`，以及 DOM 完成標記與非空回覆正文。任何失敗會記錄並停止，不把 HTTP 200 或空白回覆當成成功。

## API 的實際限制

直接把已完成的舊請求重送，實測得到 HTTP 400、`source_submission_unavailable`。以下是原始 `replay.py` 的限制；後續 `detached.py` 的成功範圍另見下節。

目前可用方式是 **瀏覽器協助的 API 呼叫**：每次重新開頁，由網站產生新的 conversation state、訊息與驗證資料；Python 攔截當次送出請求，使用 Playwright `route.fetch()` 的 HTTP API 發送原始請求，再把回傳結果交還頁面呈現。程式會輸入「嗨」以觸發網站正常的準備流程，所以每次仍需要瀏覽器。

這是本次觀察到的 ChatGPT 網站內部介面，不是穩定公開 API。網站改版、訪客限制、登入或驗證都可能使本紀錄失效；目前只證明本機訪客介面的兩次成功執行，不推論登入帳號或長期可靠性。

## 驗證結果

最後修正版實際執行 `replay.py --count 2 --timeout 40`，兩次均 HTTP 200、串流 `end`、DOM 完成且答案非空，整個程序約 18.6 秒：

1. 「嗨嗨！😄 👋 …很高興見到你～今天想聊點什麼呢？…」
2. 「嗨嗨！😊👋 …很高興見到你～今天想聊些什麼呢？💕」

較早的重播曾在正文尚未呈現時擷取到空字串，已修正成等待正文非空；以最後包含非空答案與 `run_complete` 的紀錄為完成依據。

## 關閉瀏覽器後的實驗

`detached.py` 的第一則流程：

1. 瀏覽器開啟網站並輸入「嗨」，觸發正常的準備流程。
2. 在 Playwright 路由攔截中保存當次 URL、原始 form bytes、headers/cookies 到記憶體；使用 `route.abort()` 阻止生成請求送到伺服器。這個阶段不用 `route.fetch()`。
3. `await context.close()` 後確認 `browser.is_connected() == False`，並離開 `async_playwright()`。
4. 此時才建立 `httpx.AsyncClient`，發送截下的新請求並讀取 NDJSON。
5. 根據 `mode=replace/append`、`revision/baseRevision` 組合 `markdown`；最後的 complete 記錄可能是空字串，仍須保留前面的累積答案。要求 HTTP 200、content `status=complete`、非空答案、串流最後 `type=end` 且無 diagnostic。

最後的單次實測：瀏覽器約 3.6 秒時關閉，Python 約 4.2 秒時開始發送，約 7.8 秒時收到完整答案。紀錄同時包含 `browser_closed`、`playwright_stopped`、`python_http_send`、`detached_result(success=true)` 和 `run_complete(completed=1)`。

第二則實驗：

- 更新上一輪回傳的 `conversationState` 與新訊息 ID，沿用驗證資料：HTTP 400。
- Python 重新呼叫 `/unauth-mweb/conversation/prepare`：HTTP 200，收到 `conduit_token`。
- 實際比對發現該 token 原本放在送出請求的 `x-conduit-token` header；更新它，並產生新的 `operationId` 與 turn trace ID 後，第二則仍為 HTTP 400、`source_submission_unavailable`。

因此可以確定 **瀏覽器可在第一則送出前關閉，由普通 Python HTTP 客戶端接手發送與接收**。目前不能宣稱 **只開一次瀏覽器就能持續對話**，也尚未確定第二則失敗由哪個驗證或狀態条件造成。沒有實作 CAPTCHA、登入或其他驗證的繞過。

每次獨立實驗存於 `artifacts/detached-*/`：`events.jsonl` 保存時間與答案；`turn-*-schema.json` 僅保存欄位名稱和型別；`before-handoff.png` 是關閉前畫面。完整 cookies、tokens 與 opaque state 不寫入這些檔案，僅留在程序記憶體。初期僅 HTML 解析的版本曾收到成功串流但未擷取答案，因此標記 false；以後續含完整 Markdown 與 success 的紀錄為準。

七項協定測試通過，其中包括空的 complete delta、replace/append 組合、缺失 revision 與未完成串流的判斷。

## 同一段對話中比較兩次正常瀏覽器送出

`compare_turns.py` 開啟一次可見 Chromium，在同一頁、同一個 conversation ID 中依序輸入「嗨」與「秋天天氣真好」。兩次之間不重新載入、不開新對話；使用網站原生 fetch 送出，Python 不代送、不重播。監聽請求並 clone 回應串流以觀察，憑證值僅在記憶體內比較。

```powershell
../.venv/Scripts/python.exe compare_turns.py
```

完成條件是新的 assistant 元素有完成標記、停止圖示消失、正文非空且穩定。另記錄串流是否有 `end`；native fetch clone 偶爾可能被中止，該情況會明確記錄 observer interrupted，不把觀察失敗推論為網站沒有回覆。最後有效執行的兩次串流均 HTTP 200、content complete、最後 end，並確認畫面已完成。

最後有效結果：`artifacts/compare-20261014-214320/`。此前一輪有效比較在 `compare-20261014-214046/`；中間一次回應 clone 觀察失敗的執行另有失敗紀錄。

| 比較項目 | 最後有效執行結果 |
| --- | --- |
| 網站 conversation ID | 相同 |
| `oai-session-id`，header 與 form | 相同 |
| cookies | 七個中六個相同；`oai-sc` 不同 |
| `chatRequirementsToken` | 不同 |
| `proofToken` | 不同 |
| `turnstileToken` | 不同 |
| `telemetryToken` 欄位 | 相同，尚未確認其實際語意 |
| `timingToken` 欄位 | 相同；前一輪有效執行不同，因此不能推論必定逐次更新 |
| `x-conduit-token` header | 不同；前一輪有效執行第二次未帶此 header |
| `operationId`、turn trace ID、user/assistant 訊息 ID | 不同 |
| `conversationState`、`clientContextualInfo` | 不同 |
| form 欄位集合 | 相同，共 23 個 |

兩次的送出格式都是：

```text
POST /unauth-mweb/conversation/updates?lightweight_authenticated=<redacted>&operationId=<redacted>
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Cookie: <redacted>
oai-session-id: <same session>
x-conduit-token: <per-request value when present>
```

form 解碼後包含 `prompt`、`conversationState`、`messageMetadata`、`oai-session-id`、`assistantMessageId`、`userMessageId`、`chatRequirementsToken`、`proofToken`、`turnstileToken`、`telemetryToken`、`timingToken`，以及時區、圖片、clientContextualInfo、conversationRetryOwner、workOrigin/workModel/workEffort/workTier 等欄位。`prompt` 分別是「嗨」與「秋天天氣真好」，實際 wire body 使用 percent encoding。完整的遮罩版本與 URL-encoded 範例在 `request-formats.json`。

這次觀察證明兩次沿用同一個 session，但沒有沿用整包相同驗證資料。時間線包含 Sentinel prepare/finalize 與 conversation prepare 等請求；網站可能在背景預先準備下一則資料，時間線的 turn 表示當時正在操作的 UI 階段，不能單憑該數字判定某個 prepare 一定屬於哪則訊息。

第二次的 conversationState 也不與第一輪最後 state 記錄逐字相同，但兩次 conversation ID 相同；此處只報告位元組比較結果，尚未解析 state 的所有語意。這些差異說明先前 Python「沿用舊 token」不等同於正常第二次瀏覽器送出，仍不足以確定哪個單獨欄位導致 HTTP 400。

`comparison.json` 包含每個欄位是否存在、是否為空、長度與相同/不同；不保存 raw token 或 token fingerprint。`events.jsonl` 包含答案和時間，`turn-*-complete.png` 是完成後截圖。額外三項比較測試驗證不輸出憑證值、區分缺少與空值、以及 cookie 順序不影響判斷。

## 新值來源追蹤

已新增 `trace_sources.py` 與 `trace_hooks.js`。在同一個可見瀏覽器依序送出「嗨」及「秋天天氣真好」，觀察 fetch 的 JSON/NDJSON 回應、相關請求、Set-Cookie、DOM 屬性與 crypto UUID 呼叫堆疊；所有 raw 值僅用於記憶體比對。另讀取該次瀏覽器實際載入的網站 JavaScript，保存靜態來源位置。最後有效紀錄在 `artifacts/sources-20261014-215427/sources.json`。

```powershell
../.venv/Scripts/python.exe trace_sources.py
```

| 新值 | 找到的來源 | 證據 |
| --- | --- | --- |
| `chatRequirementsToken` | `/unauth-mweb/sentinel/chat-requirements/finalize` 回應的 `token` | 與第二次 form 值完全相同 |
| `x-conduit-token` | `/unauth-mweb/conversation/prepare` 回應的 `conduit_token` | 與第二次 header 值完全相同 |
| `proofToken` | 網站 `sentinel-*.js` 的 `G.getEnforcementToken(requirements, ...)`，使用 prepare 回應提供的 proofofwork 要求 | 靜態呼叫路徑；同值已出現在 finalize 請求的 `proofofwork` 欄位 |
| `turnstileToken` | 網站 `turnstile-*.js` 模組的 `getEnforcementToken(requirements)` | 靜態呼叫路徑；同值已出現在 finalize 請求的 `turnstile` 欄位 |
| `userMessageId` | 瀏覽器 `crypto.randomUUID()` | 呼叫回傳值完全相同，包含網站 asset 與行列位置 |
| `assistantMessageId` | `pending-` 加上瀏覽器 `crypto.randomUUID()` | UUID 相符且網站 followup 程式碼確認此前綴 |
| `operationId` | 瀏覽器 `crypto.randomUUID()` | 值與 operation helper 的呼叫堆疊相符 |
| `x-oai-turn-trace-id` | 瀏覽器 `crypto.randomUUID()` | 值與 conversation-prepare-client 的呼叫堆疊相符 |
| `oai-sc` cookie | `/backend-api/sentinel/req` 的 `Set-Cookie` | cookie 值完全相同 |
| `conversationState` | 核心內容來自第一次聊天回覆；第二次送出時另外包含 safety | backendConversationId、parentMessageId 與第一次回覆欄位相符；解析 JSON 後最後 state 與第二次 state 只差 safety |
| `clientContextualInfo` | 網站從 window、screen、performance 等瀏覽器資訊組成 JSON | `a-Vjvhxgkj.js` 明確使用視窗尺寸、pixel ratio、`Math.floor(performance.now()/1e3)` 等來源 |
| `timingToken` | Sentinel SDK 的 `timing()`，未準備好時有 fallback | `sentinel-*.js` 的本地 accessor 呼叫路徑；本次並未取得 SDK 回傳值直接攔截證據 |

對話 state 的最後一次比較：第一次回覆的 state 已包含 backendConversationId、parentMessageId、userMessageCount、messages 與 fileUploadLimit；第二次送出時還有 safety。核心 ID 值逐欄匹配成功。safety 如何在頁面內寫入 state 尚未完整追到全部寫入路徑，不能把整包 state 當成未修改的回覆字串。

網站 Sentinel 程式碼先取得 requirements，呼叫兩個 enforcement 方法，再把結果以 `proofofwork` 與 `turnstile` 送到 finalize；finalize 回應 `token`，網站將這三個值組成 `chatRequirementsToken`、`proofToken`、`turnstileToken` 後隨 prompt 送出。這些來源是額外的驗證流程，而不是只由第一次聊天回覆提供。此處只追蹤正常網站流程，沒有另行實作或執行驗證演算法。

`sources.json` 的 exact_matches 是直接值比對；static_flow_evidence 是網站程式碼支持的呼叫路徑，兩者證據強度不同。未找到 exact match 不表示資料不存在或不可取得。瀏覽器中 SDK-return 的泛用 hook 本次沒有捕捉到方法回傳，因此 proof/turnstile 的 SDK 來源採用程式碼加 finalize 請求同值的證據，不冒稱直接觀察其內部計算。

靜態程式碼位於同目錄的 `static-source/`，僅保存瀏覽器實際載入的少數公開 JS assets；不保存握手回應或動態憑證。另有十三項測試通過，涵蓋協定完成、增量文字、憑證遮罩、來源路徑與呼叫堆疊移除 URL query。

## 第二次資訊組合程式

`second_turn.py` 提供兩個模式。預設 **browser-assisted** 是目前實測成功的版本：

```powershell
../.venv/Scripts/python.exe second_turn.py
../.venv/Scripts/python.exe second_turn.py --prompt '秋天天氣真好'
```

程式先讓瀏覽器正常送出「嗨」並等回覆完成，接著輸入第二則，觸發網站正常的驗證與準備流程。第二次生成請求在送到伺服器之前被攔截、阻止送出；所有新資料僅存記憶體。Python 驗證必要欄位、對話連續狀態和 prompt，保留當次網站供應的 IDs、驗證 tokens、headers 與其他欄位，重新組成 URL-encoded form，將當次 cookies 放進 HTTP cookie jar。然後關閉 Chromium、結束 Playwright，使用 `httpx` 發送第二次請求並收完整答案。

**此模式仍依賴瀏覽器取得第二次所需的新驗證資料。** 它驗證的是「第二次送出前可關閉瀏覽器」，不是「第一則後關閉瀏覽器仍可自行生成所有第二次資料」。它也沒有聲稱 Python 獨立產生 proof/Turnstile token；這些由正常網站元件提供。

實測成功紀錄：`artifacts/second-assisted-20261014-222933/events.jsonl`。第二則「秋天天氣真好」在 Playwright 結束後由 httpx 送出，HTTP 200、沒有 diagnostic、正文非空、content complete、串流 end；共完成兩次問答，約 16.6 秒。

另保留 **detached** 模式，測試瀏覽器只在第一則準備階段存在：

```powershell
../.venv/Scripts/python.exe second_turn.py --mode detached --renew-sentinel
```

Python 合併回覆 state 與 safety，使用 `pending-` 前綴的新 assistant ID、新 user/operation/trace ID、自動更新 cookie jar，呼叫 conversation prepare 取得新 conduit，並嘗試正常的 Sentinel prepare。實測第一則成功；新的 proof 與 Turnstile 要求均與原先不同，而且 required 都為 true。程式因此停止，不把舊驗證值當成新的值送出，也不自行實作挑戰解題流程。此模式回傳 1，代表尚未完成第二次。證據：`artifacts/second-turn-20261014-222742/events.jsonl`。

沒有要求 renew 的 detached 模式會標明 Sentinel tokens 是 reused；先前實測第二次可能 HTTP 200 卻包含 `source_submission_unavailable` diagnostic，仍判定失敗。完成判斷看協定和非空答案，不能只看 HTTP status。

新增四項測試驗證 safety 合併、pending ID/cookie jar、第二次當次憑證保留與缺少欄位拒絕。目前十七項測試通過。首次準備遇到 send button 尚未可用時，會等待按鈕可用後送出，以避免輸入後過早按 Enter 被網頁忽略。
