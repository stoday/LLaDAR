# 網站 Agent 探索：本機實作與驗證紀錄

2026-10-13，Asia/Taipei。對應 [PRD v0.5](PRD-browser-agent-discovery.zh-TW.md)。目前已接上通用探索、受控執行與驗證流程；本次修改依使用者要求只做靜態檢查，**原生 Streamlit 網路接入、ChatGPT、Gemini 與 EIP 的新版完整驗收尚未完成**。既有真 Akasha 隨機本機 JSON 服務探索及 adapter 重載紀錄只代表較早版本。

本文件區分程式回歸、真 Chromium、真控制／抽取模型與指定網站驗收。Skipped 不算通過；本機 fixture 成功不能代替真網站成功。私有候選程式、原始封包、瀏覽器設定檔、憑證與其所在的個人路徑不在此公開。

## 已實作的流程

公開入口是 `lladar run-agent DATASET --page-url URL`。網站探索及答案抽取均透過 Akasha 建立模型；控制 Agent 的模型與受測網站介面所選模型是不同角色。程式不依 `chatgpt.com`、`gemini.google.com` 或 Streamlit 名稱選擇固定 adapter，也不將 provider 固定為 Gemini。

| 責任 | 現行實作 | 邊界 |
|---|---|---|
| 網站探索與資格化 | [browser_target.py](../src/lladar/browser_target.py) | Agent 提交候選；host 執行隔離 probe、一次獨立驗證、正式題全文核對與重載驗證 |
| 真 Akasha 工具迴圈 | [browser_agent_loop.py](../src/lladar/browser_agent_loop.py) | 使用可終止的子程序與 IPC；驗證實際 SDK client 的模型目的地，在模型／工具呼叫前記帳 |
| 瀏覽器操作與錄製 | [browser_discovery_driver.py](../src/lladar/browser_discovery_driver.py) | 原生 HTTP、串流、WebSocket 與 DOM 事件；host 綁定分頁、回合、來源及提交 receipt |
| 分頁式觀察 | [browser_inspection.py](../src/lladar/browser_inspection.py) | Agent 可分頁讀取不可變快照；片段不能當成完整回答、完整 frame 或完成證據 |
| 生成協定程式 | [protocol_runtime.py](../src/lladar/protocol_runtime.py) | Monty 隔離執行 `adapt(payload)`；不提供 host 檔案、環境設定、provider key、任意網路、shell 或 CPython import |
| 公開協定研究 | [protocol_resources.py](../src/lladar/protocol_resources.py) | 讀取公開文件及鎖定版本／digest 的套件 schema、原始碼資產；不執行安裝 hook |
| 來源與完成驗證 | [browser_provenance.py](../src/lladar/browser_provenance.py) | 驗證原生來源、同頁同回合、提交後時序、完整材料、完成引用與 binary hash；hash 不證明未知協定語意 |
| 錄製材料傳輸 | [browser_discovery_driver.py](../src/lladar/browser_discovery_driver.py)、[browser_target.py](../src/lladar/browser_target.py)、[answer_extraction.py](../src/lladar/answer_extraction.py) | 依使用者修訂取消錄製材料遮蔽、投影及私密值阻擋；已授權網站原文可交給所設定模型。保留來源、完整性與全文核對 |
| 答案抽取與獨立核對 | [answer_extraction.py](../src/lladar/answer_extraction.py)、[browser_reference.py](../src/lladar/browser_reference.py) | 抽取與探索分工；每題須由獨立完整原文基準或同回合人工全文 `MATCH` 核對 |

固定的是授權、來源、額度與成功條件。網站操作、欄位、會話流程及終止事件由 Agent 依當次觀測建立。`test_protocol` 的本機工具輸入為 `{"phase":"inspect","events":[...]}`；網站整合另外使用 describe／setup／send／observe／restore 契約。

提交前保存 intent；已送出但完成狀態未知的題目不自動重送。探測與驗證不得污染正式會話；`existing` 政策保留正式頁面／會話，`new_each` 才允許逐題另開。帳本累積探索、工具、探測、驗證、正式及修復用量，續作不重設原時鐘與計數。舊固定選路及已退役 parser／browser 模組已移除；舊 Streamlit DOM 校正紀錄只保留為[歷史證據](streamlit-browser-validation.zh-TW.md)。

## 測試證據與範圍

下表來自已存在的 JUnit XML／安全摘要，本次撰寫未重新執行長測試或模型。不同 focused run 可能覆蓋相同案例，**不能將各列通過數直接相加**。其中私密值遮蔽／投影案例記錄已取消的歷史政策，不是原文傳輸修訂的驗證證據。

| 證據 | 已確認結果 | 能證明的範圍 |
|---|---|---|
| 非 browser／非 live 全 suite 歷史快照 | 769 項中原為 765 pass、2 fail、2 skip；修正後兩項重測皆 pass，合併快照為 **767 pass、2 skip** | 該次 checkout 的回歸快照；此後仍有新增測試及修正，不是最新 source 的完整 suite 結果 |
| 失敗案例重測 | 2 pass | 當時的公開 Target 正向案例及背景回應拒收案例已修正 |
| 混合 CLI／recovery focused | 16 pass | 新 `--probe-question` 與非 browser 行為、故障續作契約 |
| 公開 Target 真 Chromium | **10 pass** | JSON、保持開啟的 HTTP 串流、job 輪詢、持續 binary WS、DOM 多段正文、漏尾段拒收、正式頁面保留、獨立 DOM 核對及未資格化送題拒絕 |
| 原生瀏覽器 receipt focused | 4 pass | host 原生來源與提交關聯；另有一個選取零案例的 XML，未計為通過 |
| 本機 harness 真 Chromium | 3 pass | 隨機 JSON／SSE 頁面正常 UI，以及原生 Streamlit 持續 binary WS 乾跑 |
| 真 Akasha 非付費 wiring／harness 初始 focused | 5 pass | 使用已安裝 Akasha 與假模型檢查 callbacks／工具回合，以及 URL 限制；沒有外部模型請求 |
| Streamlit 中斷／續作 harness focused | 8 pass | 已核准且未送題的失敗／中斷可保留帳本；未知、已接受或已完成提交不得重送 |
| 最新 CLI／provider／帳本／reference focused | **67 pass** | CLI、runner 續作、provider 路由、可調額度、乾跑／付費 opt-in、reference 及未送題觀察契約；不含真網站 |
| 私密協定最新 focused | **79 pass** | 39 投影案例、20 host／真 Monty 邊界案例、20 去識別化案例；原始與常見編碼、重疊、來源、上下文、新增保護值及送題 intent |
| 最新真 Chromium focused | **15 pass** | 11 個公開 Target 案例（含私密 binary 投影）、2 個兩種會話政策的未送題修復、2 個 401／403 封鎖後續網路與模型案例 |

對應 XML 檔名：`browser-discovery-full-audit-20261013.xml`、`browser-discovery-full-failed-recheck.xml`、`browser-mixed-final.xml`、`discovery-public-final-contract.xml`、`receipt-browser.xml`、`native-harness-browser.xml`、`native-harness-offline.xml`、`streamlit-interrupted-harness.xml`。

新增 focused XML：`discovery-cli-final.xml`、`protocol-private-complete.xml`、`discovery-chromium-final.xml`。二進位公開 Target 的兩項真 Chromium 案例（正常完整封包／已知私密 session 投影）亦通過，見 `private-binary-native.xml`；探索與抽取仍是明列的替身，不能當成 Streamlit 真 Agent 驗收。

公開 Chromium 案例使用真瀏覽器與 host 持有的全文 oracle，但探索 Agent／抽取模型是明列的替身；它們證明工具、協定與資格化資料流，不能當成真模型自行探索。其[測試來源](../tests/test_browser_discovery_acceptance.py)明列此限制。

## 真 Akasha：隨機本機 JSON 服務

使用 [randomized harness](../scripts/verify_browser_discovery_live.py) 與 headless Chromium。頁面節點、路徑、欄位及 receipt 隨機產生，初始沒有 adapter；正常載入的客戶端資源可讀，Python 服務後端、隱藏設定及答案 oracle 不提供給 Agent。控制及抽取模型使用 `gemini:gemini-3.8-flash`，是透過 Akasha 的真模型請求，**不是 Gemini 網站驗收**。

結果為 `passed`，總耗時 542.781 秒。由公開 `BrowserTarget` 入口完成；未執行此 harness 的付費 CLI 入口或付費 SSE 模式。

| 階段 | 模型啟動 | 工具 | 已保留探測額度 | 新驗證 | 正式答案全文符合 | 抽取 | 網站請求 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 初始探索及執行 | 40 | 29 | 2 | 3 | 2 | 6 | 17 |
| 新 profile／workspace 重載同一 adapter | 7 | 0 | 0 | 3 | 1 | 4 | 10 |

初始兩次探測額度中，一次為未送出的 no-op 候選，另一次完成；初始實際提交計數為 6，重載為 4。初始正式頁面保持不變。兩階段 adapter SHA-256 同為 `cd311e1aff7a099392cbace8950020930272ac835b2dc897f27cdca977c532c0`。

歷史摘要的 `exploration_model_calls=34` 表示初始模型總數減抽取數，**包含獨立 DOM reader 建立等非抽取工作，不能全算探索 Agent 呼叫**；現行 harness 改稱 `non_extraction_model_calls`。重載的 7 次模型啟動也包含 4 次抽取及 3 次獨立 reader 工作。

此 fixture 每題獨立，沒有 backend 對話歷史，也不是持續連線服務。頁面保留不能證明 ChatGPT／Gemini／EIP 的 conversation ID 與歷史語意。這是 BD-28 的本機真模型證據，尚不符合四站共通「至少五個正式試次」及其真網站要求。

## 原生 Streamlit

使用既有 [app.py](../example_project/streamlit_qa/app.py)、Streamlit 1.65.0、headless Chromium 與 loopback 頁面。harness 透過原生聊天 UI／通訊操作；未新增 REST 包裝、未直接 import `ask_agent`，未預填 Streamlit adapter 或 codec。答案 oracle 只由 host 持有，不提供給探索 Agent。

### 不呼叫模型的乾跑

乾跑 `passed`，10.453 秒，模型呼叫 0。兩題在同一正式頁面與同一條仍開啟的原生 WebSocket 上完成，問句、完整答案及對話歷史皆保留。host 原生 receipt 分別記錄 binary 發送／接收 **1／12** 與 **1／16**。

這證明正常 UI、持續 binary WS 觀察與 host harness 可用；`binary_decoding_proven=false`，不能證明 Agent 已自行產生可用解碼器，也不是新版 BD-32／BD-35 已通過。

### 兩次付費探索均未完成資格化

| 嘗試 | 結果與安全原因 | 本次新增模型／工具／請求 | 實際送題／抽取／新驗證／正式答案 |
|---|---|---|---|
| 初次，探索限時 600 秒 | `failed`，`browser_agent_time_exhausted`；含啟動／清理共 621.641 秒 | 73／73／147 | 全部 0 |
| 同工作區續作，phase 設為 1200 秒 | 因反覆 `protected_protocol_material` 主動中斷；摘要原因 `host_interrupted_for_protected_protocol_material` | 58／58／2 | 全部 0 |

第二次保留第一次的計數、候選歷史與全域時鐘，沒有從零重來。最終累積 **131 次模型啟動、131 次工具、149 次請求**，其中網站請求 145、研究請求 4；探測額度累積保留 2 次，但兩個回合均 `not_attempted`，實際網站送題仍為 0。新驗證、正式執行及 adapter 重載均未到達。中斷時全域累計時間為 2357.739 秒；不能把它誤寫為第二次單獨耗時。

離線診斷確認：第一版候選選取首個 textarea，未按 `active_page_index` 限定分頁；其啟發式 protobuf 解碼也會把 schema 的 string 欄位誤判為子訊息。第二版只是診斷程式，沒有完整送題／收取流程。host 在續作前保存 `codec_schema_type_mismatch` 審查，Agent 可讀取自己既有策略與公開 schema 修復；host 未替它改寫 decoder。

當時的續作碰到原始 binary 包含已知私密值時，在本機解碼前被拒絕。宿主隨後曾以私密值投影修復這個邊界；使用者現在已明確要求取消此阻擋及投影。以上仍是當時的失敗紀錄，**現有兩次付費紀錄不能證明原文傳輸修訂後已接通**。

## 指定網站與未完成能力

| 目標 | 目前狀態 |
|---|---|
| `chatgpt.com` | 使用者延期；尚未完成新版探索、新 probe、一次獨立驗證、至少五題正式試次與重載 |
| `gemini.google.com` | 使用者延期；本機使用 Gemini API 作控制／抽取不算此網站通過 |
| EIP | 使用者延期；既有歷史校正或替身不能抵作新版驗收 |
| 原生 Streamlit | 乾跑可用；真 Agent 網路接入尚未通過，修復中 |

仍需記錄及驗證的邊界如下：

- **未知 binary 會話綁定**：已觀測原文可供 Agent 調查，但取得 session 值不等於已建立可靠重綁定；未知二進位模板仍須證明新回合可行性，缺口須明確回報。
- **schema 語意與完整 envelope**：原始 hash、成功返回 JSON、甚至消耗全部 bytes 都不證明 string／message、enum、數值及本題完成解讀正確。需要真 schema、完整來源與每回合獨立全文核對。
- **壓縮、加密與其他未知編碼**：材料不再因私密值被拒絕，但能讀原始 bytes 不代表已能正確解碼；無法還原完整本題材料時仍是協定能力缺口。
- **獨立 DOM 原文不足**：reader 需要至少三個不同回合資格化，且當回合完整 DOM、穩定 subtree 與 `aria-busy` true→false 等證據有效。不可取得或不能逐字還原時，仍需同回合人工全文 `MATCH`，否則未驗證；兩模型同意不能取代原文。
- **既有頁面 WebSocket 計費覆蓋**：目前摘要明列新 document／host send 可攔截，啟用前已存在的網站 WS send 有覆蓋缺口。不能將部分攔截稱為所有網站請求都已完整計量。
- **完整 CLI 與四目標發布驗收**：本機真模型 JSON 的公開 Target 成功不代替真網站正式 CLI、五個正式試次、會話語意或全部資料集完成。

## 可重現命令

一般測試不授權付費模型呼叫。需要已安裝測試依賴；browser 測試另需已配置 Playwright Chromium。以下命令是重現方式，本文件撰寫時未重新執行。

不啟動真網站或外部模型的 focused 回歸：

```powershell
uv run python -m pytest tests/test_browser_discovery.py tests/test_browser_discovery_security.py tests/test_browser_provenance.py tests/test_protocol_runtime.py tests/test_protocol_resources.py tests/test_browser_agent_loop.py tests/test_extraction_policy.py -m "not browser and not live_gemini"
uv run python -m pytest tests/test_browser_discovery_live.py tests/test_streamlit_discovery_live.py -m "not browser"
```

使用替身 Agent／抽取模型的本機真 Chromium 公開 Target 驗證：

```powershell
uv run python -m pytest tests/test_browser_discovery_acceptance.py -m browser
```

原生 Streamlit 乾跑：若尚未啟動示範頁，在另一個終端啟動既有 app，保留原生 binary WS；不要加 REST 路由。

```powershell
uv run --extra test --extra streamlit-test streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8517 --server.headless true --browser.gatherUsageStats false
uv run python scripts/verify_streamlit_discovery_live.py --page-url http://127.0.0.1:8517 --timeout 30
```

**以下會使用真模型，可能產生費用。** `--paid` 明確啟用原生 Streamlit 探索／抽取；預設不加此旗標只做乾跑。付費 harness 的成功條件包含網路收取、新 probe、一次獨立驗證、五題正式輸入及冷重載驗證，DOM 收取不能替代 binary 網路驗收。

```powershell
uv run python scripts/verify_streamlit_discovery_live.py --paid --page-url http://127.0.0.1:8517 --timeout 1200
```

同一個尚未送題的失敗／中斷工作區可續作；`PATH_TO_FAILED_RUN` 是原輸出目錄，不是新的目錄。增加 phase timeout 不重設全域 deadline，餘額不足須停止。

```powershell
uv run python scripts/verify_streamlit_discovery_live.py --paid --resume --timeout 1200 --output-root PATH_TO_FAILED_RUN
```

隨機 JSON 的真模型案例亦有明確 opt-in 測試。此旗標授權實際模型呼叫；以下只選 JSON，避免一併啟動未驗收的付費 SSE：

```powershell
$env:LLADAR_LIVE_BROWSER_DISCOVERY = "1"
uv run python -m pytest tests/test_browser_discovery_live.py -m "browser and live_gemini" -k "json"
Remove-Item Env:LLADAR_LIVE_BROWSER_DISCOVERY
```

模型及 env 檔可使用 harness／測試所提供的參數指定；provider 憑證留在本機設定，不貼入命令、文件或模型材料。網站錄製原文依本版授權範圍可傳給模型。所有重現結果須附當次 source 與計數，不能沿用本文件快照假定新增修正已完整驗收。

## 歷史：私密 wire 投影修復

本節保留當時實作與驗證的快照。下述遮蔽、投影與私密值拒絕已依使用者要求取消，不再是現行錄製材料傳輸規則。

宿主當時會先遮蔽已知私密值及常見 UTF-8／Base64／hex／URL／JSON escape 表示，再把同長度投影交給 Monty。原始 wire 與其雜湊只留在宿主；Agent 的 `payload_sha256`／`source_hashes` 表示它實際讀到的投影，避免原始雜湊形成短私密值的猜測線索。驗證使用當次宿主發出的不可偽造來源紀錄，並重新核對原始事件；新增私密值後，下次解碼必須使用新檢視。

所有工具輸出、協定上下文、動作結果與基準都經同一私密值檢查。只有精確符合宿主當次來源紀錄的投影 `binary_b64` 能進入 VM；Actor 自稱的二進位欄位沒有此豁免。新識別的私密值會同步加入回答抽取檢查；送題意圖也檢查 Base64／byte-list、宿主 request reference 及 DOM 草稿。以上修復已通過 focused 及已知私密 session 的真 Chromium 二進位資料流測試。

這只能保護已知值及已識別表示。位置、長度與相等關係仍可見；未知壓縮、加密、任意重新編碼與 checksum 語意不在保證內。不能安全遮蔽或完整解碼時會拒絕，不把被遮蔽的答案当成原文發布。

原生 Streamlit 原探索帳本的 3600 秒時鐘已到期，保留其失敗紀錄與累積用量；修復後未重設該帳本再跑付費探索。只有重新通過原生 Streamlit probe、一次獨立驗證、至少五題正式試次及重載，才能改寫該目標的驗收狀態；目前不宣稱 PRD 完成。

隨機本機 JSON harness 的 CLI 現在也要求明確 `--paid`：

```powershell
uv run python scripts/verify_browser_discovery_live.py --paid --mode json --entry target --timeout 600
```

wheel／sdist 已在乾淨暫存 checkout 建置，檢查新模組、Monty 依賴、退役模組與私密檔案包裝邊界；本機版保留版本號 `0.10.3`，未發佈 release。LLaDAR-test 已安裝本機 wheel 及 Monty 的 client／runtime 子套件；12 個關鍵模組與 source 的 bytes 相符、6 個退役模組不存在，該 Python 3.11 環境的原生 Monty 執行通過，模型呼叫為 0。安裝後 CLI 有 `--probe-question`、`--browser-session-policy` 與 browser `--adapt`，不再提供 `--calibration-question`。預設輸入／輸出 token 額度分別為 131072／65536，可依設定調整；這不是對所有 provider 實際容量的保證。安裝前的套件有本機備份，登入資料與 `.env` 未更動。

## 人工登入對話框競態修復

使用者在 EIP 登入期間遇到 `Page.handleJavaScriptDialog: No dialog is showing`，按 Enter 後流程停在 `awaiting_consent / browser_failed` 並清理瀏覽器。該次宿主帳本的計數為空：探索模型、Agent 工具與探測／正式送題尚未開始。`awaiting_consent` 原本也錯誤涵蓋了後續瀏覽器設定階段，不能據此判定問題在同意資料傳輸。

新版 driver 漏了人工交接的原生對話框保護。Playwright 在沒有 dialog listener 時會預設自動關閉對話框；這會與使用者手動處理競態，不能當成純被動錄製。[Playwright 官方說明](https://playwright.dev/python/docs/dialogs)

修正會在瀏覽器開啟前進入人工交接，對 context 註冊不接受／不關閉對話框的 listener，涵蓋主頁與 popup。登入、YES 同意及 MATCH 人工核對時，原生對話框由使用者處理；交接返回或中斷後移除臨時 listener，自動操作沿用一般行為。按 Enter 後先用不讀頁面正文、不發網站請求的原生連線檢查確認瀏覽器存活，再檢查頁面、模型目的地及設定錄製。連線失效會明確回報 `checking_browser_connection / browser_connection_lost` 並停止；不啟動 Agent 修復失效的瀏覽器連線。

本機反事實重現只停用這個 listener，原生 Chromium 即在模擬使用者處理對話框時重現相同 `No dialog is showing`，見 `dialog-without-listener-red.xml`。修正後 **7 項通過**：2 個 Target 流程與錯誤階段、4 個主頁／popup 正常與中斷交接、1 個原生關閉頁面連線檢查，見 `manual-handoff-final.xml`。另有 **56 項** CLI／續作／進度／核對回歸與 **80 項** 語系／交接 focused 通過，見 `dialog-source-regression.xml`、`dialog-localized-green.xml`；這些案例有重疊，不相加成總測試數。

這次修復不等同於 EIP 真網站已通過 Agent 接入驗收；未讀取登入憑證、未自動完成登入或驗證碼，也未啟動付費模型探索。使用者原有的 blocked run 與帳本保留，可依原參數加 `--resume` 續作。

較廣的 Chromium 回歸共 39 案例，原為 38 pass、1 fail；另修正 request-budget callback 的未知錯誤代碼正規化，保留已知授權／帳本失敗原因。該失敗案例與 401／403 授權封鎖、人工交接的 **10 項重測全部通過**，見 `dialog-budget-auth-green.xml`；原回歸快照見 `dialog-driver-regression.xml`。不將 focused 重測與原快照相加成新完整 suite。

LLaDAR-test 的已安裝版另用本機 headless Chromium 通過原生對話框交接與交接後預設行為檢查；12 個關鍵模組 bytes 與本次 source 相符，原 blocked run 的 bundled skill 身分相符，可以依同一設定續作。這項安裝後驗證的外部網站請求及模型呼叫均為 0；不更改使用者原始帳本、登入設定檔或 `.env`。

## 登入完成後的對話頁交接

`--lang zh-TW` 的登入提示已改為：

> 可見瀏覽器已開啟。請完成登入並處理完網頁對話框，開啟可對話的頁面／視窗，保持輸入框空白。準備好後，再回到這個終端按 Enter：

使用者只需先準備真正可問答的頁面，不必在這一步手動送出校準題或輸入 MATCH。多個分頁／視窗會在終端列出編號及服務 origin，讓使用者明確選擇；單一頁面直接綁定。選擇同時核對穩定 page ID，避免分頁關閉或被替換後將相同編號誤認為原頁面。列表不包含頁面標題、完整網址、query 或正文。

交接完成後捕捉正式頁面與所有既有頁面。Agent 檢視工具及生成的 Monty 程式均不能把這些頁面用作探測；清理也不得關閉它們，包括仍列在宿主 `_owned_pages` 的既有頁面。這項檢查針對瀏覽器頁面的保護，不證明任意後端 API 的會話識別已正確隔離。

本機交接套件 **10 項通過**，包含 **7 項真 headless Chromium**，見 `browser-manual-page-final.xml`；再加強其中同一公開 Target 案例，確認省略／指定編號的關閉動作及 probe 清理都保護既有頁面，見 `browser-manual-owned-closure-green.xml`（1 pass、9 deselected）。語系與探索 focused 回歸 **84 項通過**，見 `ready-page-language-regression.xml`。這些結果有重疊，不累加為新的完整 suite。

擴大的 Chromium／交接回歸快照為 20 pass、1 fail，見 `ready-page-browser-regression.xml`。該程序載入新關閉 guard 修正前的 source，同時包含加強後的省略編號關閉斷言；之後的修正版只重跑上段公開 Target 案例並通過，不宣稱完整 suite 已重跑通過。使用者要求停止後續測試，未再執行測試或真網站探索。

新版 wheel／sdist 已建置並通過包裝檢查。使用者明確要求同步更新後，已將本機 wheel 安裝到 `LLaDAR-test/.venv`；安裝前另備份套件，不改 `.env`、登入設定檔或工作帳本。安裝後只讀取已安裝的中文提示確認交付內容，未跑測試、模型或網站驗收。已啟動的程序仍持有啟動時載入的程式碼，須重新執行才能使用新的登入提示及頁面選擇。先前「12 個模組與 source 相符」仍是先前安裝驗證的快照，不代表本次重做了那些測試。

## 錄製材料原文傳輸：使用者追加修訂

使用者於 2026-10-13 明確要求取消 `protected_protocol_material` 與同類錄製材料隱私阻擋。已授權網站錄製的 URL、headers（含 Cookie／Authorization）、請求／回應 body、客戶端 JavaScript、文字及 binary 封包，以及協定工具的解碼結果，均可原樣交給所設定的探索／抽取模型；不再先遮蔽、投影或因已知私密值及其編碼表示拒絕，也不新增 opt-in 或核准選項。

這項修訂保留 `.env`／provider key／browser storage 隔離、DOM 密碼／OTP 欄保護、服務 origin 範圍、網站請求與模型呼叫帳本、正式／探測會話隔離，以及來源、完成證據與完整原文核對。一般摘要與公開報告不印錄製原文；後續依使用者要求加入的本機 `[AKASHA]` verbose trace 例外列出 Agent 實際工具材料。能讀取材料不等於已接通；既有 Streamlit 失敗與四目標未完成狀態不因此改寫。

依使用者要求，本次沒有執行測試、模型呼叫或真網站驗收。已建置本機 wheel／sdist，並將 wheel 重新安裝到 `LLaDAR-test/.venv`；安裝前另備份套件，未更動 `.env`、登入設定檔或工作帳本。已啟動的程序需要重新執行才能載入本次修改。上文測試數均為先前版本快照，不能視為本次放行修改或 EIP 流程已驗收。

## EIP 第一版的分頁定位與送題前準備修正

第一版候選失敗在正式送題之前。候選的頁面搜尋會混合所有分頁的 frame；`setup` 開啟另一個 EIP 分頁並回報尚未完成後，宿主仍直接進入 `send`，候選因而取到非目前作用分頁的控制項。宿主以 `browser_action_page_not_active` 拒絕該動作；該回合沒有執行填入或提交，也沒有正式題目送出紀錄。

宿主現改為只把目前作用分頁的可操作 frame 與連線交給候選。`setup` 每執行一批動作後都會重新檢查瀏覽器、取得新頁面觀察並再次執行；必須得到 `complete=true` 且該次動作清單為空，才會建立題目回合、開始錄製本題並呼叫 `send`。若候選回報未就緒卻沒有下一步動作，流程以 `adapter_setup_incomplete` 停止，不會嘗試送題。終端在 verbose 模式顯示準備步驟與 ready 狀態；新候選 revision 也會清除舊 revision 的 context 與 action results，避免沿用失效的頁面引用。

依使用者要求，本次不執行自動測試、真 EIP、瀏覽器或模型驗收；只做靜態差異檢查、套件建置與 `LLaDAR-test/.venv` 更新。因此這份紀錄證明已修正控制流程，不宣稱 EIP 已實際接通。修改前已啟動的程序仍使用舊程式，必須重新執行才會載入本修正。

## 校對錄製與接口建立順序修正

檢查確認舊流程在第一次探測前即使用 `submit_adapter` 保存完整候選，之後才由 `test_adapter` 送出內建探測題並進行全文校對。這使「用 UI 錄第一題的暫定程式」與「根據已校對錄製建立的接口」共用同一名稱與資格；若暫定程式靠 DOM 通過，Agent 也可能直接停止。

最後採用人工校對先行。登入、分頁選擇、資料傳輸揭露及授權完成後，宿主在啟動探索模型前建立 `calibration-*` 回合，於終端顯示含唯一標記的完整題目。使用者在目前作用對話頁手動送出，等待完整最終回答，再回終端按 Enter。宿主必須確認題目、同回合回答材料與人工全文完成確認都已保存，並以抽取全文和網站完整回答的 `MATCH` 完成資格化；只有題目而沒有回答材料時，以 `manual_calibration_response_missing` 停止。Agent 起始時只取得這個已資格化校對回合的事件清冊，登入及頁面載入事件不混入；之後才允許 `submit_adapter`，由 `test_adapter` 使用新的探測回合驗證，再由宿主執行一次獨立 verification。

本次沿用使用者要求，不執行自動測試、瀏覽器、EIP 或模型驗收。既有執行程序不會動態載入此變更，必須重新執行。

## Agent 終端探索追蹤

舊版在 verbose 模式主要顯示工具名稱、狀態碼與每十秒一次的用量，例如 `read_event` 或 `stage=discovering`；Akasha worker 同時使用 `thinking=False`、`verbose=False`，因此終端無法說明 Agent 正在找輸入框、檢查網路事件、分析協定或修正候選。

新版保留宿主的階段、帳本、證據摘要與下一步說明，並在 CLI `--verbose` 開啟時將 Akasha 建構為 `verbose=True`。worker 的 stdout 專供 JSON RPC；Akasha stdout／stderr 經另一條 pipe 逐行轉送成 `[AKASHA]`，因此可看到 Akasha 自己輸出的模型回合、工具呼叫參數、工具回傳內容、時間與最終訊息，不會破壞 host RPC。`--no-verbose` 令 worker 使用 `verbose=False` 並停止顯示該 trace。這是 Akasha 公開 trace，沒有把 `thinking` 改成 true，也不宣稱能取得模型未公開的 chain-of-thought。

Akasha verbose 可能含已授權的 URL、headers、Cookie／Authorization、封包、頁面內容、客戶端程式與候選程式。這符合本次使用者要直接看實際探索材料的要求；終端及 `--log` 產物必須視為私有，不附入公開報告。

依使用者要求，本次只做靜態差異檢查、套件建置與安裝檔案一致性核對，未執行自動測試或真網站／模型驗收。

首次交付 Agent 終端追蹤時，`_agent_instructions` 的多行回傳式漏掉結尾右括號，造成 `browser_target.py` 在 CLI 啟動時回報 `SyntaxError: '(' was never closed`。該命令在匯入 browser target 階段即停止，未開啟瀏覽器或送出網站題目。修正後重新建置並以正常 wheel 流程安裝至 `LLaDAR-test/.venv`；已確認安裝環境可匯入 `lladar.browser_target` 與 `lladar.browser_agent_loop`，且 `lladar run-agent --help` 正常退出。這是啟動 smoke check，不是 EIP、瀏覽器或模型驗收。

2026-10-13 的 `responses-eip-trace-20261013` 實際執行顯示，光靠提示沒有強制先錄製校對回合。帳本在尚無任何 turn、未提交探測錄製器時已累積 27 次模型呼叫與 27 次工具呼叫：包含 3 次 `read_dom`、3 次 `read_events`、8 次 `read_event`、7 次 `read_client_resource` 及 4 次 `test_protocol`。四個 protocol candidate 仍只是檢查 event 數量、欄位及 `text/body` 是否存在，尚未建立可送題的錄製器。當時 277 個 website requests 均為瀏覽器載入期間流量，不能視為校對題提交。為停止無效用量，在確認沒有 turn 後終止該次特定程序樹，保留 `.work` 帳本。

宿主現在直接在模型啟動前完成人工校對回合；`read_events`、`read_event` 與 `test_protocol` 的初始可見範圍限制在該回合。若 adapter 的新探測失敗，範圍才切換到該失敗回合供局部修正。工具完成後仍輸出精簡證據摘要，下一個工具開始時仍說明受限動作；`[AKASHA]` 則提供使用者要求的實際 verbose 明細。

依使用者要求，本次不執行自動測試、瀏覽器、EIP 或模型驗收。只進行語法／JSON／差異檢查、套件建置與 `LLaDAR-test/.venv` 安裝檢查；因此仍需下一次真實 EIP 執行確認人工校對事件可被該網站錄製，以及 Akasha trace 的實際內容。
