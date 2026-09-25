# PRD：以登入瀏覽器擷取問答 API，直接由模型抽取答案

## Status

2026-09-25 最新決策：使用者明確要求移除程式解析，不保留為選項。
本修訂將 browser mode 的答案抽取收斂成唯一途徑：**模型直接閱讀本次回應的結構與內容，
選取／拼接既有的最終回答，不生成或執行解析程式。**

這取代先前的宣告式解析、內建規則優先、生成 Python parser、模型協助適配與內建解析
並存等方向。舊的 parser 生成／試跑／快取／Monty 路徑及其 CLI 選項都須在實作遷移時
移除，不作後援、不保留相容轉送，也不是新加一個可選的實驗模式。

**方向已確認，直接抽取的程式碼、CLI、依賴與中英文文件已遷移；真模型及真網站驗收仍待新授權。**
本機 fixture 驗證宿主資料流、安全限制與瀏覽器整合，不代表真模型的抽取忠實度已通過。
詳見[直接抽取遷移紀錄](direct-extraction-validation.zh-TW.md)。
[歷史驗證紀錄](browser-capture-validation.zh-TW.md)及本機診斷保留為歷史證據；
舊 GP1–GP9 不再是本版實作清單，通過舊測試也不能抵作下述 DE 驗收。

三欄資料、專案模式的 adapter、`eval` 評分職責、`report` 計算依據與網站權限不變。
本決策不等同允許把真實回答送到模型，也不重置任何模型／網站請求額度。
先前「只傳合成結構」的授權不能沿用為真實回應傳輸同意。

2026-09-25 追加確認：保留請求錄製，互動流程改為一次完整授權及 terminal 彩色核對。
一次 `YES` 須同時明示網站請求與真實內容外傳兩個範圍；`MATCH` 仍是稍後的忠實度核對。
核對不再開 HTML 分頁，但登入、校準與重播仍需要瀏覽器；本修訂不新增無瀏覽器模式。

2026-09-26 追加確認：網站 CLI 不再提供互動模式選擇。指定 `--page-url` 即固定進入
登入、校正、一次完整授權與 terminal `MATCH` 流程；`--interactive`／`--no-interactive`
僅保留給專案模式，與 `--page-url` 合用時於執行前拒絕。stdin 或 stderr 非 TTY 時，
在讀取資料集、啟動瀏覽器與呼叫模型前停止並提示改在終端執行，不自動同意或略過核對。
核准旗標的範圍不變；程式化 runner 的可信本機參考注入介面不在本次 CLI 簡化範圍內。

## Problem Statement

使用者可能只知道一個能登入、提問並取得回答的網站，沒有可讀取的目標專案、OpenAPI
或 Python 入口。要求他們從 DevTools 提供 method、Cookie、payload、JSON path 或
SSE event 規則，會把整合工作轉嫁給使用者。

列舉網站框架、欄位名稱或有限解析操作，難以涵蓋不同回應封裝。先讓模型生成 parser、
再以另一套宣告式規則驗證，還會增加生成程式與驗證契約不一致、runtime 與快取等成本。
本版選擇直接模型抽取，接受逐次呼叫模型的費用與延遲，取消維護多條答案解析路徑。

這個選擇不保證支援所有網站，也不保證模型逐字抽取正確。模型可能漏字、重複片段、
誤取進度訊息或自行修正答案；這些是需要獨立量測的抽取失敗，不可混同目標 Agent 的品質。

## Solution

主要入口維持 `lladar run-agent DATASET --page-url URL`。使用者在 LLaDAR 管理的可見
Chromium 視窗自行登入，手動送出唯一校準標記。LLaDAR 觀察成功的請求與回應，取得同
session 可重播的提問方式，一次完整揭露並取得網站請求及回應傳輸同意後，進行獨立驗證與資料集執行。

唯一答案抽取流程：

`同一題的完整回應 → 必要敏感資料處理 → 模型直接抽取／拼接 → 結果檢查 → actual_response`

- 模型直接輸出答案文字，不輸出 Python、腳本、正規表示式、JSON path 或解析 DSL。
- 所有已支援的文字回應均走這條路，包括看似簡單的 plain text／JSON；沒有無模型捷徑。
- 每次目標回應獨立抽取，不把前一題回答、期望答案或評分結果混入輸入。
- 固定本機程式仍接收 HTTP／SSE、解碼字元與標準傳輸封裝、關聯 request、處理大小限制。
  它不依答案欄位名／事件名選答案，不以另一套語意規則覆蓋模型結果。
- 結果格式／長度／身分／完成狀態檢查不等於內容正確的證明；忠實度另由 fixture
  的獨立基準與真網站的本機人工核對驗收。
- 缺少擷取能力、資料傳輸同意或可信核對方式時停止，不切回舊解析器。

`--page-url`、`--project` 與測試 callback 仍是互斥目標來源。
`--service-url` 仍只供可探索的目標專案使用，不因本版而變成任意 API 的契約探索功能。

## User Stories

1. 使用者只提供網站頁面 URL，不需知道 method、endpoint、headers、Cookie 或 payload。
2. 使用者在 LLaDAR 專用瀏覽器手動登入，不把密碼、OTP、CAPTCHA 或 session 貼進 CLI。
3. 使用者只做一次正常 UI 校準，不需撰寫 parser、指定欄位或辨認網站框架。
4. 同一流程支援已能擷取的文字／JSON／NDJSON／SSE 回應，模型逐次理解封裝。
5. 模型保留目標的完整最終回答，包括錯誤、不確定、拒答與原有語言，不代答或美化。
6. 使用者在同一個提示核准網站請求數、模型目的地、真實回應傳輸範圍與模型用量／時間上限。
7. 使用者能重用仍有效的 LLaDAR-owned profile，或明確選擇全新 profile。
8. 登入失效時回到手動登入；重新登入不自動授權額外重送。
9. 缺少同意、回應不完整或抽取有歧義時，使用者收到明確原因，而不是程式解析備案。
10. 稽核者能區分手動校準、自動驗證、資料集請求、模型抽取及各自失敗的階段。
11. 三欄 JSONL、trials、run sidecar、`eval` 與 `report` 繼續沿用。
12. 封裝、說明文件與 CLI 不再要求使用者選 parser policy 或同意生成程式執行。

## Terminology and Interface

- **回應擷取（Captured response）**：取得屬於同一次 request 的有序材料與已觀察的完成
  狀態；材料可能同時包含進度、工具訊息、片段與最終回答。
- **答案抽取（Answer extraction）**：從既有材料辨認並組合目標已給出的最終回答，
  不生成新答案、不評分。
- **抽取模型**：只讀取本次被授權材料並輸出抽取結果的模型，不是 coding agent。

答案抽取是一個深 Module，對 browser executor 保持一個小 Interface：
`extract(captured_response) -> final(text) | incomplete | ambiguous | unsupported | invalid`。
這裡只有直接模型抽取這一種 Implementation，不建立內建／生成 parser 的選擇層。
Transport capability 與答案抽取為不同 Seam；前者新增可觀察協定，後者理解已取得的文字。

## Browser Capture and Request Execution

- Playwright 維持核心依賴；Chromium 明確按需安裝，不在套件安裝或普通命令中偷偷下載。
  缺少瀏覽器時提供可執行的安裝指示；安裝仍須對應同意。
- 專用 persistent Chromium profile 依網站區分，只供同機同使用者使用，排除於 Git、
  一般 run 產物與封裝外。不讀取、複製、解密或匯入日常 Chrome／Edge／OS credential store。
- 密碼、MFA、OTP、CAPTCHA、帳號切換等操作由使用者在可見瀏覽器完成。取得 session
  不代表允許新增原網站以外的操作，也不等於模型傳輸同意。
- 校準 marker 必須對應唯一可替換的提問位置，以及同一 request 的回應；找不到或有多個
  候選就要求最小補充操作，不猜 API schema、不任意嘗試 POST。
- 暫存 request template 僅供可信 browser executor 在原 session 重播。實際網址、登入
  材料及 request body 不交給抽取模型，也不存進一般 sidecar。
- 支援範圍按可觀察 transport 區分，不按 React、Vue、Angular 等框架列名單。初期擷取
  HTTP fetch/XHR、GraphQL-over-HTTP 的 plain text、JSON、NDJSON、SSE 文字材料。
- 文字 WebSocket、其他 custom streaming、binary、gRPC-web 或 rendered-answer route
  仍須各自實作並驗證擷取能力，不能宣稱換成 LLM 就自動支援。
- 不讀取頁面任意最後一則訊息、不混用其他 request 或前一題的回應。可信宿主產生
  request identity 與 provenance，模型回傳的身分字串不能取代它。
- 完成狀態來自可信的傳輸結束或明確本機觀察，不由看到某個常見答案欄位決定。持續
  開啟的串流若無法觀察完成，須取得本次的本機完成確認或回報 incomplete；逾時不把
  已收到的前半段冒充完整答案，也不無界輪詢模型猜測是否結束。
- 瀏覽器啟動、首次導頁及重新導頁各自最多等待 5 分鐘（300 秒），獨立於
  `--timeout`；不因調整導頁等待而縮短 Agent 回答或模型整輪額度。
- 目標 request 預設等待 60 分鐘（`--timeout 3600`，秒），自動重播另受整輪剩餘期限約束。單次擷取仍限制
  1 MiB decoded bytes；超限不截斷後送模型。Fetch 在保留 chunk 前計數；XHR fallback
  需可信 decoded-byte 測量，缺少測量時停止。這不是整個瀏覽器記憶體的上限。
- 不為停止校準擷取而取消使用者原本的網頁請求。超限、失去登入或 request 身分不明時，
  停止受影響的後續派送並保留已完成結果。
- 網站自動請求數、重新登入後是否可重送及重送上限都須事前揭露並計入同意範圍。
  模型失敗不能觸發額外網站請求。
- 固定排程仍由既有 packaged run skill 在本機計算，不為排程初始化抽取模型；其他
  使用者自訂 skill 的既有模型行為不在本次改動範圍。

## Direct Model Extraction Contract

### 輸入與允許的工作

- 初期抽取驗收使用 `gemini:gemini-3.8-flash`，browser mode 的新抽取預設亦採此模型；
  `--model` 可選明確支援的目的地／模型，不改專案模式、出題、eval 或 report 的預設。
- 每次呼叫只包含本 request 的必要 content type、有序回應本文、完成狀態及固定抽取
  指令。必要的 JSON escaping／文字編碼還原是傳輸處理，不預先替模型選答案。
- 模型可理解不同欄位、巢狀字串、事件及片段，辨認 append 與 replacement、排除非答案
  材料，再直接輸出目標原有的完整最終文字。不得把累積快照全部串起來造成重複。
- 不要求模型生成程式、抽取規則、`evidence.path`、`each` 或供本機執行的操作序列；
  不以重新設計一套解析 DSL 作為這條路的前置條件。
- 模型結果可用固定 JSON envelope 表達狀態與文字；例如 final 有 `status`、`text`，
  其他結果用固定安全原因碼。這只是輸出格式，不是網站的答案欄位契約。
- 同一 run 固定模型設定、prompt 版本與結果 schema，每題獨立請求，不帶跨題聊天歷史。
  重複測試同一題仍分別擷取與抽取，不重用前一次答案。

### 禁止的工作與可信宿主檢查

- 不提供 function calling、網路、browser、shell、Python 執行、檔案、環境變數或修改
  專案的工具。即使模型回傳程式碼，也只是不符合結果契約的資料，永不執行。
- 模型不得讀取 `expected_answer`、評分結果或另一題材料。題目若原本被網站回應引用，
  是本 request 的證據，不額外提供整份 dataset 或正確答案引導模型。
- 不代答、摘要、翻譯、潤飾、校正錯字、修正事實、補字或刪掉否定與不確定性。
  原有 Markdown／換行等答案文字保留；不以評分方便為由正規化掉差異。
- 回應內容、欄位名稱、事件名稱中的指令一律是不可信資料，不能改 prompt、啟用工具、
  改目的地、擴大預算或影響排程。不得向模型要求隱藏推理過程作為驗證依據。
- 固定宿主僅檢查結果 schema、型別、大小、可信 request 對應、已觀察完成狀態與授權。
  JSON 合法、非空、HTTP 200 或模型自稱成功，都不是忠實度證明。
- 若保留原文對照或其他輔助檢查，不得變成另一套答案選擇器、固定欄位名單、舊 parser
  路徑或替模型改寫結果的機制。文字一致也不能單獨證明沒有漏選片段。
- 無法辨認完整回答時回報 incomplete／ambiguous／unsupported，不猜測或補寫。

### 真實內容傳輸與保存

這一版會讓抽取模型看到必要的**真實回應內容**，與舊版「只傳合成結構」不同。
移除 Cookie 不代表回答已非敏感資料；內部知識仍可能在答案、欄位或事件名稱中。

- 先揭露實際 provider 目的地、模型、資料範圍、模型請求數及費用上限，取得明確回應
  傳輸同意。完整揭露後的一次 YES 同時核准兩個範圍，內部仍分別記錄授權。
  已有 API key、網站登入、舊版僅核准網站的 YES 或先前合成測試同意都不足以授權。
- 不傳 Cookie、Authorization、password、token、provider key、browser storage、
  專用 profile、原始 request headers/body 或無關流量。必要敏感資料處理也涵蓋本文。
  若無法在排除禁止材料的前提下忠實抽取，就停止並說明，不靜默傳輸或改答案。
- 網站 CLI 固定要求人工操作的終端流程，不提供非互動模式。程式化 runner 若採非互動
  呼叫，仍須已有所有必要同意及可信核對途徑，否則停止。拒絕傳輸的結果是 browser
  mode 無法繼續，不提供程式解析作為替代。
- 原始回應與模型 prompt 預設只在本機記憶體中存活到本次處理結束，不保存完整 provider
  回應／thought／原文到一般 log 或 cache。另行匯出診斷需明確範圍與同意。
- 獨立人工核對允許將該次驗證回應與抽取文字顯示在互動 terminal，並先告知捲動紀錄／
  終端錄影可能留存內容。不將核對本文送到一般 logger、sidecar、非 TTY 管線或檔案。
- 最終 `actual_response` 仍依既有三欄契約保存在 responses／trials，可能含敏感內容；
  保留原有 eval／report 使用範圍，不因抽取同意自動授權其他模型或目的地。
- 安全 sidecar 記錄直接模型抽取身分、請求階段、prompt/schema 版本、模型、固定原因碼、
  時間、呼叫次數及 provider 實際提供的 token 用量；沒有用量就標示未知，不臆測費用。
- 對未正常完成的 provider 結果，保留經白名單映射的完成原因；未知值一律固定安全碼。
  區分網路／認證／限流／超限／截斷／結果無效，不輸出原始錯誤訊息或機密材料。

### 費用、時間與重試

- 不再有「生成一次 parser、整批重用」的成本模型。每次成功擷取的回應都需一次抽取；
  一般 N 次 dataset trials，含校準與一次獨立驗證，上限為 N + 2 次模型呼叫。
  N 以實際排程 trial 數計算，不是唯一題目數。
- 本版初始限制：每次抽取最多 1 次模型呼叫、8,192 output tokens、60 分鐘期限，
  不做隱藏 SDK／HTTP 重試。整批須另受使用者同意的總模型次數與總費用／時間上限約束。
  這是新路徑的待驗收設定，不沿用或重置任何先前真模型測試的額度。
- 輸入須同時符合本機捕捉上限、provider payload 上限及所選模型的 context budget。
  具體 provider 輸入限制須於實作時明列並測試，不靜默截斷或用摘要代替原回應。
  本機實作的輸入硬上限為含 JSON 封裝的 120 KiB UTF-8，輸入加固定 prompt 不超過
  128 KiB；這是保守的位元組預算，不是假定 tokenizer 比例或模型宣告的 context window。
  所選模型若拒絕其 context／payload，仍須停止，不重試或分段。模型回應另限 256 KiB。
- 8192 tokens 不保證容納任意長答案；輸出被截斷即失敗，不保存半段為 actual_response。
  增加上限、額外抽取、拆段模型工作或重播網站皆需新的明確預算，不能隱式追加。
- 取消或預算耗盡立即停止相關呼叫並記錄已消耗次數；失敗的模型請求也扣額度。
  改用直接抽取不會消除 provider 輸出未完成或服務失敗的可能性。

## Calibration, Verification and Artifacts

1. 驗證 dataset、目標來源與 CLI 選項；無效參數在啟動 browser 或模型前拒絕。
2. 開啟專用 browser，使用者登入並在正常 UI 送校準 marker，等完整回應。
   缺少抽取模型憑證不延遲這個登入／擷取交接。
3. 顯示安全的 method、origin、path shape、transport、session 重用狀態、驗證／dataset
   請求數與模型目的地、真實資料範圍、可能費用、呼叫／輸出／時間預算；一次 YES 核准
   兩個範圍。任何尚未核准的範圍被拒絕時，零模型呼叫、零自動網站請求。
4. 模型抽取校準回應；成功不表示已完成獨立驗證。
5. 在固定抽取設定下送出另行核准的一次驗證問題，抽取這個新 request 的回應。
   以本機的完整來源及可信參考核對；沒有可自動核對的參考就請使用者確認，否則停止。
   抽取模型必然會看到驗證回應，但不會收到人工參考答案或自行評定驗收通過。
6. 通過後才派送 dataset。每次回應各自抽取；校準與驗證通過不宣稱後續每題必然忠實。
   事後修改 prompt／設定須重新驗證，新增請求仍須授權。
7. 最終文字原樣填入 `actual_response`。未取得可靠完整結果者為 null，原因寫入
   trials／run sidecar；停止受影響的後續派送，保留已完成資料。
8. `eval` 再判斷目標答案品質；不得把抽取模型補寫的內容拿來評分。
   對外明示結果為模型抽取，抽取忠實度與答案正確性是不同的驗收項。

獨立性由可信宿主關聯的新 request 確認，不靠文字必須不同；兩次獨立請求可以得到相同
答案。舊錄製重跑、換 request 名稱或模型自寫斷言都不算新的網站驗證。
來源對照留在本機，不因人工核對而上傳更多內容。沒有核對能力時不得假稱 verified。

人工核對直接使用互動 terminal（stderr），不建立 HTML／瀏覽器核對分頁。以青色來源
標題、紫色抽取標題、黃色注意事項及固定文字標籤區分；顏色不表示答案正確。
尊重 NO_COLOR 與 TERM=dumb，純文字仍可核對。完整內容不截斷，每行標為引用文字；
ESC、C0/C1、CR、雙向／隱形格式等控制字元須可見轉義，不能清畫面、移游標、建立
連結或改剪貼簿。僅顯示層轉義，模型輸入與 actual_response 原文不因此改寫。
沒有互動 terminal 時，須提供既有可信 reference_reader，否則模型／重播前停止。
正式 dataset 答案仍不新增逐題 MATCH，也不新增原始串流保存。

## Removal and Migration Decisions

以下為實作階段的刪除要求，不代表本次文件修改已刪除程式：

- 移除 browser 生成 parser 的提案、離線試跑、凍結／重用、程式 hash 驗證與執行路徑。
- 移除 browser 內建答案欄位／事件規則、宣告式 response plan 與所有 fallback。
  可保留確實共用的標準傳輸處理，不可留下「模型失敗就走舊解析器」的隱藏分支。
- 移除僅供該路徑使用的 Monty runtime、`pydantic-monty` 依賴、parser-cache 模組、
  專屬測試與封裝資產；共用的擷取、登入、記錄及專案 adapter 測試改接新契約而非刪除。
- 刪除 `--parser-policy`（含 `model-assisted`／`builtin-only`）、`--allow-parser-execution`、
  `--allow-parser-model-transfer`、`--parser-cache`、`--no-parser-cache`、`--clear-parser-cache`。
  舊參數應在任何模型／網站活動前明確報錯，不能當別名靜默切換成真內容傳輸。
- 新的明確傳輸核准介面為 `--allow-response-model-transfer`，只核准當次所揭露目的地、
  真實回應資料範圍與預算；`--confirm-browser-run` 仍只核准網站。任一單項旗標都不擴權；
  互動模式有缺少的授權時，以完整揭露的一次 YES 補齊；兩旗標均給定則免授權提示。
  不保留 TRANSFER／EXECUTE prompt；核准 flag 不能跳過獨立核對。
- 舊 parser cache 不載入、不執行、不遷移成答案。既有私人檔案不在升級時自動刪除；
  如需清理另行確認精確範圍。專用 browser profile／有效登入狀態仍保留，不被當成 cache 刪掉。
- 同步 pyproject、lockfile、CLI help、README 中英版、網站中英版與 release CI。
  README／網站必須在實作完成後才宣稱新命令可用。
- 歷史驗收資料及使用者變更保留；不要改寫舊失敗成成功。非 browser 的專案分析、
  Python adapter、callback、排程、三欄輸出、`eval` 與 `report` 不因移除 browser parser 而刪除。

## Testing Decisions and Acceptance

測試以公開 Interface／CLI、實際 request log、產物及使用者可觀察的行為為準。
固定 provider 僅驗證資料流與限制，不能證明真模型抽取能力。
本機 browser fixture 使用合成 session、合成回答，與真實內部網站分開。

1. **DE1 — 唯一路徑與移除：** 所有已支援文字格式都使用直接模型抽取；無生成 source、
   Monty、舊 policy、parser cache 或隱藏 fallback。舊 flags 在任何副作用前報錯；
   封裝不帶舊 browser runtime 或私人資料，沒有模型憑證時不偷偷改走規則抽取。
2. **DE2 — 封裝與片段忠實度：** corpus 包含 plain text、巢狀／字串內 JSON、NDJSON、
   SSE delta、replacement、累積快照、混合進度／工具／debug／完成訊息、重複／重排、
   未知欄位／事件、多候選、損壞與截斷。依獨立 fixture 原文逐字比較或明確失敗，
   不要求任何 `final_text` 名稱；不得只看非空。涵蓋已宣稱能擷取與尚不能擷取的協定。
3. **DE3 — 抽取不是代答：** 改變 expected_answer 不影響模型輸入；錯誤數字、否定、
   不確定、拒答、Markdown 與換行原樣保留。任何摘要、補字、翻譯、修正事實、
   把進度當答案、漏字或重複拼接都不能記作忠實度通過。
4. **DE4 — 身分與完成：** 校準、獨立驗證、各 trial 不混用材料；前一題、無關 response、
   偽造身分、提前的 partial body、逾時或模型自行宣告完成都不繞過可信宿主檢查。
   驗證未通過前零 dataset 請求；本機參考不以抽取模型自己的答案作為唯一依據。
5. **DE5 — 同意與隱私：** 網站同意不授權模型；舊合成同意／舊 flags 不授權真內容。
   檢查 headers、body、欄位、事件與錯誤中的合成 secrets 不外洩，明確核准的答案
   可傳給指定模型。console、sidecar、快取、封裝無禁止材料；原文診斷匯出另行同意。
6. **DE6 — 注入與無執行：** 惡意回應要求讀檔、連網、執行程式、擴權或改答案時，
   無工具或宿主執行入口；模型回傳 source 只會被當無效資料。驗證攻擊不改排程與授權，
   並單獨量測其對抽取內容的影響，不把無工具誤稱為內容絕對安全。
7. **DE7 — 有界呼叫與安全診斷：** 驗證 N trials 對應 N + 2 的事前上限、失敗扣額度、
   零隱藏重試、60 分鐘期限、輸入／輸出超限、取消及 partial-result 保存。模型失敗不
   重送網站；未完成原因可安全區分，未知 provider 訊息不直接印出。不可無限送 chunk。
8. **DE8 — 不退化：** Browser profile 重用／fresh／過期手動交接、請求校準、
   project/service/callback、packaged skills、三欄／trials、eval／report 與輸出防覆寫
   仍維持契約；新的抽取錯誤寫既有 sidecar，不新增使用者必填欄位。
9. **DE9 — 真模型與真網站分開：** 經新授權，以 Gemini 3.8 Flash 對純合成未知封裝
   執行直接抽取並和獨立原文比較；再另行核准真實回應傳輸與有界網站驗收。
   報告完整抽取率、逐字一致／遺漏／重複／改寫與失敗情境、呼叫數、延遲及可得 token
   用量，不以一次成功聲稱通用。真網站仍需手動登入、校準與完整抽取核對。
10. **DE10 — 單次授權及 terminal 核對：** 沿用公開 CLI／BrowserTarget、外部網站與模型
    fixture 的 Seam，驗證只出現一次完整授權提示；單項旗標不擴權、拒絕時零外部派送、
    MATCH 不能被 YES 或旗標取代。以注入 terminal 串流驗證彩色／純文字、全文不截斷、
    控制碼與偽造提示安全引用、非 TTY 不洩漏、顯示與保存的文字契約不混用。真 Chromium
    回歸確認登入／擷取照常、無核對分頁，網站及模型呼叫額度不增加。

新增 transport 需公開擷取 seam 的測試與 browser fixture 回歸，不改排程／記錄格式。
Browser 測試在明確配置 Chromium 的專用 CI job 執行；非 browser 測試不啟動 Chromium。
真網站 identifying URL、opaque path、Cookie、原文問答與流量不得進入 repository fixture。
既有內部 SSE 網站仍是手動驗收目標，使用者不需貼 DevTools 契約或 Cookie。

開發順序：檢視本版規格 → 移除舊路徑並依 Interface 實作／測試直接抽取 →
本機合成整合與安全測試 → 分別取得真模型及真網站授權並驗收 → 封裝與發布 commit CI。
歷史通過項不自動標示 DE1–DE9 已完成；此文件修改不觸發安裝、模型、網站請求或發布。

## Out of Scope

- 保留程式解析為選項、後援、隱藏捷徑或套在直接抽取外面的必經 DSL。
- 模型生成／執行程式、操作 browser、登入、修改專案或取得通用工具。
- 模型代答、修正網站答案、評分，或以 model confidence 代替獨立忠實度驗收。
- 從單一裸 API URL 猜契約；OpenAPI／cURL 匯入、任意 HTTP／API-key 目標模式。
- 讀取日常瀏覽器 profile、跨使用者／裝置同步登入，或上傳 storage／登入資訊。
- 自動密碼、MFA、OTP、CAPTCHA、付款、帳號恢復、任意表單／DOM 自動化。
- 與已觀察問答工作流無關的新增、刪除、上傳、下載、管理或其他副作用。
- 通用解密／binary 解碼、無限 context、無限模型／網站重試或無授權原始診斷匯出。
- 宣稱抽取通過等同答案正確、權限安全、前端完整覆蓋或服務全面驗證。

## Further Notes

「通用」是模型能理解已擷取材料的不同結構，不是框架／網站／欄位名單，也不是零失敗
保證。模型抽取可能比程式生成更適合某些封裝，但其忠實度、費用與穩定性須實測。
使用者負擔應維持頁面 URL、正常登入、校準與必要確認，不能改成請使用者設計抽取 schema。

本版有意採單一路徑，不再維護多種答案解析政策。代價是每題模型成本與真實回答的資料
傳輸要求；不同意資料傳輸時就不執行 browser mode。這個限制必須在開始批次前清楚揭露。
