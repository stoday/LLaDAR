# 一次示範與獨立 adapter：實作及驗證紀錄

2026-10-13，Asia/Taipei。對應 [PRD v1.1](PRD-browser-single-demonstration.zh-TW.md)。新版已接入 `run-agent --page-url` 的預設入口；真網站四目標驗收尚未完成。

## 已接入的流程

- [demonstration_recorder.py](../src/lladar/demonstration_recorder.py)：從初次導頁持續記錄，終端等待維持事件處理，保存 scoped Cookie／storage、示範材料與資源後關閉錄製瀏覽器。重新登入按需開啟，保存後關閉。
- [recording_store.py](../src/lladar/recording_store.py)：SQLite 事件索引、篩選、關聯引用與游標搜尋；原文按引用分頁讀取，返回完整性及 hash。
- [independent_target.py](../src/lladar/independent_target.py)：單一 Akasha 探索／修復角色，九個工具，候選提交、一題試跑與兩題新驗證；正式失敗保留原題證據供修復。
- [independent_adapter.py](../src/lladar/independent_adapter.py)：純 Monty 計畫、宿主 HTTP／持續串流／文字或 binary WebSocket 通訊；沒有瀏覽器物件或 CDP 操作介面。提交前保存 intent，未知提交不重送，修復可重新組裝尚未核對的原始材料，已核對答案維持原文。
- [independent_reference.py](../src/lladar/independent_reference.py)：三個可信回合資格化原生 JSON 答案位置與結構；符合本題關聯、原結構及完整自然結束時，以獨立宿主 reader 核對正式回答。缺少可信基準時暫時開啟核對頁。

新版命令使用 `--demo-question`、`--adapter` 與 `--conversation-policy independent|continuous`。舊 `--probe-question`、`--browser-session-policy` 與分開核准的兩個旗標已從 CLI 移除。Python runner 的部分內部參數名稱保留供其他功能呼叫，不是舊 CLI 別名。

## TDD 與本機證據

測試介面已由使用者確認：CLI、Agent 記錄查閱工具、獨立 adapter 執行／恢復。切片先觀察失敗，再完成實作；測試 source 位於 [test_single_demonstration.py](../tests/test_single_demonstration.py)。

已測情境包含大量背景事件中的示範定位與分頁讀取、保存 Cookie 的獨立問答、提交後收取恢復、不重送、假完成引用拒絕、未授權目的地不派送、保存材料損壞、Cookie 輪替後重載、保持開啟的 HTTP 串流與二進位 WebSocket、真 Chromium 示範後關閉瀏覽器、CLI 正式輸出，以及同一 Agent 角色修復。

上述 Chromium 自動測試使用 **headless 真瀏覽器**，由 harness 模擬人工操作；一般測試中的生成模型是明示的替身。它們不代表使用者已完成實際網站登入或真人視覺驗收。

最終分組結果：非瀏覽器新測試 13 通過、headless 新測試 4 通過、CLI／pipeline／擷取政策回歸 111 通過；另有 Agent／runtime／進度／恢復回歸 68 通過。測試集可能重疊，不加總為唯一案例數。其後加強「答案結構改變」的修復測試，先觀察失敗再修正，單項重跑 1 通過：不把 intermediate progress 當成最終答案，原題只送出一次。

wheel／sdist 已建置，`scripts/verify_browser_package.py` 兩項通過：新版模組存在、過時模組與私有 runtime 材料未包入；另完成語法編譯與 `git diff --check`。產物只在本機 `.tmp/single-demo-dist`，未發布。

## 真 Akasha 模型與隨機化服務

使用真 Chromium 與本機 HTTP 問答服務，endpoint、問題欄位與答案欄位隨機產生。控制模型為 `gemini:gemini-3.8-flash`，透過 Akasha 與已解析的 SDK 目的地執行。網站後端及答案 oracle 不交給探索 Agent。

最新已完成的真模型測試：

| 項目 | 結果 |
| --- | --- |
| 總耗時 | 137.74 秒 |
| 初始模型／工具呼叫 | 11／10 |
| 初始自動網站請求 | 8：一題 probe、兩題 held-out verification、五題正式問答 |
| 人工示範 | 由 headless harness 在正常 UI 模擬一題；保存後關閉瀏覽器 |
| 重載 | 同一 manifest 在新 workspace／runtime 載入保存狀態；沒有開啟瀏覽器，模型呼叫 0 |
| 重載網站請求 | 4：一題 probe、兩題 held-out verification、一題正式問答 |
| 全文核對 | 獨立宿主 oracle／經資格化的宿主 JSON reader；不是語意相似評分 |

JUnit 證據：本機 `.tmp/single-demo-real-agent-reload.xml`。它證明隨機本機 HTTP 服務的真模型生成與重載，不證明真模型已生成 Streamlit binary codec，也不是 Gemini 網站驗收。

較早嘗試曾因模型 profile／目的地核對，以及動作 `type`／`kind` 契約不清而失敗；修正後的兩次真模型測試分別通過。失敗不可計入成功，重複 focused suites 的通過數不可相加。

## 尚未驗收的範圍

### 2026-10-13 真 EIP 執行

真站 `https://eip.iii.org.tw`、人工示範題「請假的流程」、預定五次正式問答。使用者確認已送出含識別碼的完整示範題並看到回答；19:07 執行仍以 `demonstration_submission_missing` 中止，沒有生成 adapter，沒有自動 probe／驗證／正式回答。保存紀錄包含 277 筆原生 HTTP 請求及登入流程，未包含該示範送出事件；尚不能判定第一次漏錄根因。

後續真站診斷發現重啟錄製時事件 ID 與舊資料衝突，已改成每次錄製的唯一前綴；同一真站、同一保存目錄重新開啟已不再出現 UNIQUE 衝突。日誌關閉後仍被保留的 logging stream 寫入也已修正，待完整真站退出再確認。直接開啟對話網址得到空白頁，不視為通過；EIP 前端使用 SSO 初始化，正式測試必須保留 EIP 登入及點入對話的實際路徑。

19:20 已使用相同設定恢復真 EIP 錄製。新版找不到示範送出事件時保留瀏覽器並顯示錄製分頁，允許再次檢查；尚未取得本次成功結果。依使用者要求，此後不再跑模擬網站，這輪修改未執行模擬測試；只進行語法／差異檢查及真站診斷，不能據此宣告 EIP 接通。

該次恢復的真實紀錄之後包含 EIP 頁面內送往 `cai2.iii.org.tw` 的示範題 POST、完整識別碼、HTTP 200 與 request-finished；對話沒有出現獨立的第二分頁。使用者按 Enter 後，程序長時間卡在 `RecordingStore.append()`：每筆事件都重新以 `SUM(size)` 計算所有已錄資料，真站數千事件時持續阻塞 Playwright 的事件處理。已改成 SQLite 交易內的固定成本配額計數，並先用索引搜尋示範識別碼。卡住的測試程序已定界終止，原始紀錄留存；19:30 以同一工作階段啟動修正版真站錄製。

19:39 的真站示範已成功錄下本次送出，瀏覽器已關閉且登入狀態保存。Agent 執行 41 次模型與 40 次工具呼叫，最後只回分析文字，沒有呼叫 `submit_adapter`；主程式當時未檢查候選是否存在便進入驗證，造成空值例外並模糊顯示 `browser_failed`。已修正為 Agent 返回後必須有已提交候選及通過的 `test_live`，缺少時回報明確原因；也把共用執行器殘留的舊任務文字改為此流程的專屬任務。這次沒有執行正式題。

保存資料庫同時有前幾次錄製。已用本次錄製游標隔開舊錄製事件，但 EIP 會在新頁面重新載入先前聊天，因此本次 DOM 與活動回應仍可能含舊訊息。Agent 現在從本次送出事件、關聯 HTTP 回應和本次識別碼查找答案；單靠錄製時間不能把網站重新載入的歷史內容當作新答案。

隨後使用相同保存登入及示範資料續跑真 EIP，未重開錄製瀏覽器。模型呼叫達原核准上限 50 次、工具呼叫 52 次，仍未呼叫 `submit_adapter`，以 `browser_agent_budget_exhausted` 停在 `agent_generation`；沒有候選、probe、驗證或正式回答。此次只做錄製材料搜尋／讀取。已從真錄製中定位本次識別碼所在的活動回應及後續非空活動回應，將其事件引用直接提供給 Agent。

使用者明確同意把同一工作階段的模型總上限提高至 80 次。直接修改 `LLADAR_BROWSER_DISCOVERY_MODEL_CALLS` 被 checkpoint 的設定一致性檢查擋下，未消耗模型呼叫；因此新增 `run-agent --extend-model-calls TOTAL`，僅允許恢復 `--page-url` 時提高 Agent 模型總額，其他 session 設定不變，用量與延長紀錄保存在 ledger。此入口經 CLI help／語法／差異檢查及真 EIP 恢復啟動確認。真站再跑到 80 次模型／85 次工具呼叫，仍沒有 `submit_adapter`；最終 `browser_agent_budget_exhausted`，沒有候選、probe、驗證或正式回答。額度已用完，不再自動續跑。

目前同一個 Agent 角色在每次 `--resume` 重新建立執行實例；錄製與帳本持久化，但探索對話並未持久化。反覆延長提示與額度仍可能讓它從頭查材料。後續需將研究與生成設成有界階段，保存已選證據，讓生成階段產出可解析的程式並呼叫 `submit_adapter`／`test_live`；純分析文字或耗盡讀取額度須明確失敗。這項改造與真站成功驗收尚未完成。

進一步對保存的真 EIP 錄製做結構核對（未新增網站或模型請求）：本次 HTTP POST 含示範識別碼；活動回應中的使用者訊息帶同一識別碼，後續機器人訊息以 `replyToId` 指向該訊息。回答位於 Adaptive Card 附件的多個中文文字欄位，而非活動最外層的 `text`。錄製亦包含 KeyAuth SSO 回應、botRegister 兩步驟及其成功回應、Direct Line 對話查詢與活動輪詢；值的相等關聯顯示 SSO 值進入註冊，註冊權杖用於對話查詢，查詢回傳權杖用於本次送題。登入 Cookie 與既有 conversation ID 存於保存狀態。這些資料足以支持提交並實測一個候選，不支持「材料明顯不足以寫任何程式」的判斷。但錄製沒有直接出現建立全新 conversation 的請求；獨立對話政策能否重建仍需調查客戶端資源及真站試跑，尚不能宣稱完整 adapter 一定可用。80 次額度內 Agent 未提交候選、未試跑，也未形成有證據的缺料結論。

ChatGPT、Gemini 與原生 Streamlit 的新版完整網站驗收尚未進行；EIP 已開始實測，尚未通過。真網站登入、MFA／CAPTCHA 與一次人工示範需使用者參與；已有本機自動測試不能代替這些證據。無法重建的瀏覽器依賴須回報為未接通。

### 2026-10-13 真站候選與通用性檢查

從保存的真 EIP 示範，直接給 `gemini-3.8-flash` 有界材料可生成完整 `adapt(payload)` 程式；同樣材料搭配工具式 Agent 時，12 個模型回合與 11 次提交仍沒有通過候選介面。LLaDAR 因此加入有界材料包、直接產生程式與 `submit_adapter`／`test_live` 門檻；不能再把純分析文字視為完成。核心材料選取包含示範提交、關聯回應、登入／連線初始化、客戶端程式片段，並保留 HTTP 與 WebSocket 的一般記錄路徑。站方 URL、Cookie 名稱及 Adaptive Card 欄位只存在於本次候選程式與私有真站材料，不寫入通用執行器。

真站候選修正後曾實際送出問題、收到 82 字答案，並與獨立讀取同回合原始卡片的 82 字基準完全一致；這只證明該回合的送收與原文抽取。後續回合顯示候選沿用示範的 Direct Line conversation ID；數次 token 各異，但 conversation ID 相同。以現有 token 呼叫建立對話端點得到 HTTP 200 及同一 ID，因此尚無新對話證據。宿主現於送題前檢查可識別的 Direct Line 對話 ID，若與示範或不允許重用的先前回合相同，回報 `conversation_policy_violation` 並停止送題；新增公開 adapter 介面的失敗先行測試，確認拒絕發生在題目提交請求之前。此檢查不依賴 EIP 網域。

**驗收狀態仍是未接通。** 目前沒有證據顯示可建立與示範分離的新正式對話，也沒有完成五題正式資料集。試跑與驗證目前僅使用示範題的不同識別碼變體，已在產物標示 `demonstrated_question_repeats`，不能推論任意題型皆可忠實擷取。針對 EIP 的站方會話建立方式只應由其生成 adapter 實作；若站方不提供可重建的獨立建立方式，應明確停在未接通，而非把重用舊對話算成成功。

未知 binary／串流的獨立全文基準、實際 popup／MFA 流程、跨站動態 token 與特定網站 session 恢復仍需各站實測。核心通訊提供受控 HTTP／WebSocket 操作，生成程式不能任意 import CPython 套件或原生 codec；缺口明示，不自動改為常駐瀏覽器。

## 真網站重現入口

`lladar run-agent DATASET --page-url URL --output responses.jsonl --lang zh-TW`。程式會提示何時登入、送出示範、按 Enter 與等待；保存後關閉錄製瀏覽器。先前本機服務與 headless 測試結果僅供開發史參考；依使用者要求，後續驗收只用真網站，且不能把候選提交前的執行視為接通。
