# LLaDAR

[English](https://github.com/stoday/LLaDAR/blob/main/README.md) | [繁體中文](https://github.com/stoday/LLaDAR/blob/main/README.zh-TW.md) · [完整文件](https://stoday.github.io/LLaDAR/zh-TW/)

**自動出題、實際測試 Agent，再評估它真正說了什麼。**

LLaDAR 是測試 Agent 與聊天網站的命令列工具。從知識文件產生問題與預期答案，
收集受測 Agent 的真實回答，再產出包含評估判讀、統計與證據的報告。

可以從一組問答開始，也可以建立多輪情境，觀察 Agent 隨著對話發展如何回應。
用自然語言描述想評估的行為；需要更細緻的方法時，再用本地 Skill 自訂。

## 可以測什麼？

| 你想知道的事 | LLaDAR 如何協助 |
| --- | --- |
| Agent 的回答是不是真的出自知識文件的正確答案？ | 從知識文件生成問題與預期答案，再比較受測 Agent 的回答。這不是完整的捏造內容檢查。 |
| 已部署的聊天網站會如何回應？ | 透過引導式瀏覽器流程測試支援的問答頁面，不必提供專案原始碼。 |
| 面對追問，Agent 是否保持一致？ | 建立多輪情境，指定觀察目標與停止條件。 |
| 回答是否符合自己的評估需求？ | 用自然語言描述準則，或提供 Skill 自訂評估方法。 |

例如，你可以拿退款政策檢查客服 Agent 的主要回答、檢查它是否詢問缺少的資訊，
或觀察它在使用者追問後是否保持前後一致。若要檢查回答是否加入文件沒有支持的內容，
還需要足夠的來源證據與適合的評估準則，不能只靠一般文件題目自動得到這個結論。

回答由評估 Agent 判讀，統計由 Python 計算。保存的回答與逐筆證據讓你能檢查
判斷依據，也能用不同準則重新評估同一批回答。

## 快速開始

安裝 LLaDAR（支援 Python 3.11 與 3.12）：

```bash
python -m pip install lladar
```

在 `.env` 或程序環境設定模型供應者的憑證；受測 Agent 也須備妥自己的執行環境設定。
使用 `lladar --version` 查看已安裝版本。

想先釐清測試目標，可啟動互動式規劃：

```powershell
lladar plan "我想測客服是否依退款文件回答" --lang zh-TW --debug
```

planner 依工作流 profile 只詢問缺少的必要資訊，由模型提交結構化 `PlanSpec`，
再由程式驗證本機輸入、能力組合和階段依賴，產生後續指令。
支援文件問答、行為情境、既有回答重評估及既有評估結果報告；規劃本身不呼叫受測目標，
也不執行出題、測試、評估或報告。詳見 [plan v2 規格](docs/PRD-plan-v2.zh-TW.md)。

新的計畫目錄會保存 `plan.md`、`plan.json`、`plan-events.jsonl` 和 `plan.log`。
`--debug` 顯示即時去敏化診斷；`--log PATH` 可指定新的文字 log 檔，未開 debug 仍保存紀錄。
`ready` 計畫另外提供 `commands.ps1`，供檢閱後執行；`blocked` 或 `failed` 計畫提供
`diagnostics.json`，列出錯誤碼、trace ID 及修正方向。`ready` 只代表靜態檢查通過，
不代表受測目標已驗證可用。既有計畫目錄和 log 檔不會覆寫。

假設知識文件放在 `./knowledge`，受測 Agent 專案位於 `../my-agent`，依序執行：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
lladar eval responses.jsonl --output evaluation.json
lladar report evaluation.json --output report.md
```

這四步串起 **文件 → 測試問題 → 真實回答 → 評估 → 報告**。
開啟 `report.md` 檢視結果與逐筆測試證據。也可以使用既有題庫，或重新評估已保存的回答。

注意：從文件出題主要是檢查回答是否符合知識文件裡的正確答案，不等於完整的幻覺或超出文件內容偵測。

### 如果受測目標是聊天網站

將上面的 `run-agent` 步驟換成：

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl
```

請換成自己的問答頁面 URL。LLaDAR 會開啟瀏覽器，引導你完成登入、校準、授權與
回答核對，再執行測試。支援的頁面與設定方式見[目標選擇指南](site/zh-TW/guides/run-agent.html#choose-target)。

### 自己決定什麼算是好的回答

例如，評估 Agent 是否知道資訊不足時應該追問：

```bash
lladar eval responses.jsonl --criteria "檢查回答是否詢問缺少的資訊，而非自行編造事實；引用回答中的內容作為判斷依據。" --output evaluation-missing-info.json
lladar report evaluation-missing-info.json --output report-missing-info.md
```

需要更細緻的控制時，可用 `--skill` 指定含有 `SKILL.md` 的本地目錄，
自訂出題、測試排程、評估或報告方法。各階段的適用方式見下方使用選項。

想測試會隨對話發展調整的情境，可從[多輪情境範例](example_project/situation_demo/README.md)開始。
包含自訂方法的完整操作流程見[使用指南](site/zh-TW/index.html)。

## 使用選項

所有主要階段都可省略 `--output`，也可指定資料夾或完整檔案路徑。
省略時輸出到目前工作目錄下的 `runs/`：資料集使用 `test-dataset-YYYYMMDD-HHMMSS.jsonl`，
其他階段依序使用 `situation.json`、`responses.jsonl`、`evaluation.json`、`report.md`。
指定 `--output ./artifacts` 時，預設檔名與附屬檔都放進 `./artifacts/`。
既有資料夾、結尾分隔符號或沒有副檔名的新路徑視為資料夾；
既有檔案或有副檔名的新路徑視為檔案。含點號的新資料夾可寫成 `./results.v1/`。
既有輸出仍需 `--force`。

回答、附屬檔、續跑工作目錄與匯出的 adapter 都會跟隨主輸出放在 `runs/`；
明確指定 `--output` 或 `--export-adapter` 時，則使用指定路徑。
專案測試的暫存集中到 `.tmp/pytest/`，測試快取放在 `.tmp/pytest-cache/`；
手動指定 pytest 的 `--basetemp` 仍可覆寫位置。
一次清理測試／建置暫存、`runs/`，以及舊的根目錄
`responses.jsonl`／`response.jsonl` 與評估附屬檔，可執行：

```powershell
python scripts/cleanup_generated.py --all-generated
python scripts/cleanup_generated.py --all-generated --apply
```

第一行只預覽；第二行列出清單後，輸入 `DELETE` 才刪除。
刪除執行產物會失去其中的回答、續跑狀態、adapter 與擷取資料。
受版本控制的檔案、根目錄 `.env`／`.venv`、`.lladar` 登入設定仍會保留。
既有產物不會自動搬移。

每個階段成功後都會顯示工作摘要，列出輸出的絕對目錄、各檔案位置與內容簡述，
以及下一步命令或閱讀提示。`--no-verbose` 仍會顯示摘要，`--log` 也會同步保存。

產生資料集：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

預設使用隨套件安裝的 Akasha `knowledge-point-qa` skill，讀取知識段落並生成有來源依據的問答。
Agent 依選定 skill 選擇直接問答，或使用圖譜產生概念映射及受控變體 probe；圖譜不是必經流程。
也可使用 Python 產生或修正候選結果，正式輸出前會驗證最終資料。
每份資料集附有 `dataset.jsonl.generation.json`；只有使用有效圖譜時才產生 `dataset.jsonl.graph.json`。

也可以讓 Agent 自行探索公開 GitHub repo 的實際資料集：

```bash
lladar create test-dataset --from-github https://github.com/hsinmosyi/TWBias --count 20 --output dataset.jsonl
```

`--knowledge` 與 `--from-github` 擇一使用。GitHub 模式讀取文件，檢查 CSV／JSON／JSONL
或文字資料，也可沿文件中的公開直接下載連結取得資料。除生成 sidecar 外，會自動產生
`dataset.generation-report.md` 與 `dataset.sources.json`。報告逐題列出原始樣本、固定版本連結、
轉換方式、答案依據、模型檢查與限制。Agent 生成的判準會與原始答案或標註分開，使用前請檢閱。
repo 程式僅供閱讀理解；私有 repo、登入下載、壓縮包與二進位格式暫不支援。
資源限制與自訂 Skill 階段見 [GitHub 生成說明](docs/github-dataset-generation.zh-TW.md)。

圖譜 probe 用來觀察目標模型如何把「正餐」等推論概念映射至來源實例，沒有唯一正解，
會與答對率分開報告。合成控制是測試輸入，不是來源或人物主張，預設不產生。
在終端可用 `--controlled-variant-probes`，於生成工作列出已驗證維度後選擇；
自動化腳本則用 `--controlled-variant-topics DIMENSION_ID`，指定同一份語料指紋下
已記錄的維度 ID。只接受規劃器已驗證的維度，讓每一組 pair 都維持可比較性。

需要 Akasha 1.8 以上。`--skill DIRECTORY` 可改用一個可信任的本地 skill；
不需要額外 manifest，尚未加入 skill 安裝管理命令。內建方法不屬於可移除的使用者安裝項目。
詳見 [skill 生成使用說明](docs/skill-generation.md)。

執行目標 Agent。LLaDAR 會檢查專案副本，替真實公開流程建立暫時 adapter：

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

若要測試無知識文本的情境，請參閱[可直接執行的情境範例](example_project/situation_demo/README.md)。兩種專案模式共用 coding Agent 的探索、工具與實跑修復；`max_turns=1` 校準只驗證單輪真實回答，多輪設定才額外驗證前文記憶與新 session 隔離。

兩種專案模式都可加上 `--adapt PATH_TO_ADAPTER.py --project PATH` 重用既有 adapter。LLaDAR 跳過探索與生成，在專案副本中重新驗證所選協定；失敗就停止，不改寫指定的 adapter。仍需 `--project`，因為 adapter 依賴應用程式來源及執行環境。

Adapter 探索、生成與修復的模型請求最多等待 180 秒（若目標逾時設定更短則沿用較短值），不進行 SDK 自動重試。模型耗時、安全的 token 用量與完成原因會存入 `adapter/model-events.jsonl`。目標若明確回報可對應本次請求的模型錯誤，或整體目標執行逾時，會停止執行而非觸發 adapter 修復；單憑程序逾時不會判定為模型供應商故障。

該選哪個目標參數？

- `--project PATH`：從專案程式碼與文件理解對外入口。只有專案也可能足夠：啟動資訊與必要執行環境設定完整時，LLaDAR 可以在隔離的專案副本中啟動服務、測試，最後只關閉自己啟動的服務。
- `--project PATH --service-url URL`：仍檢查專案，但改用已啟動的測試服務。URL 提供的是**服務基底位址**，不是啟動指令或 API 契約；HTTP 方法、路徑、Payload、驗證需求與回應格式仍從專案理解，不會啟動或關閉該既有服務。
- `--service-url URL --api-guide FILE`：不需要目標專案，改從 TXT、MD、DOCX 或文字 PDF 的 API 使用說明與範例建立 HTTP adapter。PDF 不做 OCR；沒有可讀文字會直接報錯。
- `--page-url URL`：不必提供專案原始碼，改用正常問答網頁。在 LLaDAR 瀏覽器登入後，由 Akasha Agent 探索實際通訊與頁面操作、資格化協定／會話適配程式，再由無工具模型忠實抽取擷取的回答。

HTTP、持續開啟的 SSE、原生文字／二進位 WebSocket 與 DOM 收取後援共用探索工具及資格化條件。Agent 調查實際 session ID、封包結構與完成證據，不預設網站 schema 或固定 selector。本機 fixture 驗證這些通訊路徑；各真實網站的驗收證據另外記錄。

已啟動測試服務的範例：

```bash
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

也可以只提供 API 使用文件：

```bash
lladar run-agent dataset.jsonl --service-url http://127.0.0.1:8000 --api-guide docs/api-guide.md --env-file .env --lang zh-TW
```

指南必須說明 HTTP 方法、相對路徑、認證或明確不需認證、必要 header／參數與問題欄位、請求及回應範例、答案擷取與完成條件。缺漏時會在呼叫 API 前停止；多個候選需互動選擇。TXT／MD 使用 UTF-8（允許 BOM）；DOCX 讀取段落與表格，PDF 只讀已有文字。文件中的範例主機不會取代 `--service-url`，憑證從執行環境或 `--env-file` 取得。

文件模式支援同步 JSON、具明確完成與關聯條件的 SSE／polling，沿用獨立 adapter 驗證、`--adapt` 重用、`--resume` 與預設 `OUTPUT.api-adapter` 匯出。不能混用 `--project`、`--page-url` 或多輪 Situation config；續跑時必須保留相同指南與服務設定。[文件模式規格](docs/PRD-api-guide-adapter.zh-TW.md)有完整驗收範圍。

使用 `--project --service-url` 生成單題 HTTP adapter 時，會自動產生**可交給別人使用的 API adapter 執行包**，預設存入 `OUTPUT.api-adapter`，不需要額外的匯出參數：

```powershell
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

生成時仍需專案來確認 HTTP 路徑、Payload、認證與答案格式；只有選定既有公開 HTTP 介面、且 adapter 在空目錄與隔離的 Python 中通過呼叫驗證，才會保存交付包。`responses.jsonl.api-adapter` 包含可獨立執行的 `adapter.py`、跨平台 `README.md`，以及附說明的 `.env.example`，列出服務網址、逾時設定與必要憑證名稱；憑證值保持空白。adapter 讀取程序環境變數，不會自動載入 `.env`。指定 `--lang` 時，非英文文件與設定註解會透過 `akasha.ask` 使用 adapter 模型自動翻譯，程式碼、指令及設定名稱保持不變；翻譯會快取供接續執行重用，英文則不需翻譯呼叫。接收者只需 Python 3.11+、該 Python 檔、自己的憑證與能連到 API 的網路，不需要原專案或安裝 LLaDAR。可選用 `--export-adapter api-client` 自訂目錄名稱。

```powershell
$env:LLADAR_SERVICE_URL = 'https://your-api.example.com'
# 依 README 或 adapter.py 的 REQUIRED_ENV 設定自己的憑證環境變數。
python responses.jsonl.api-adapter/adapter.py --message '請問這個服務可以協助什麼？'
```

也可用 `--service-url URL` 覆寫位址。認證方式從專案探索，沿用 Bearer、API key、自訂 header、Cookie 或可獨立重建的登入流程，憑證從執行時環境變數取得。HTTP helper、必要變數宣告、啟動與驗證流程整合在 `adapter.py`，不複製 `.env`、專案、瀏覽器登入狀態或執行證據。`LLADAR_API_TIMEOUT` 設定整題的總等待期限與預設 HTTP 逾時，預設 60 秒，包含 SSE 與 polling；單次請求指定的逾時不能延長總期限。程式介接可省略 `--message`，由 stdin 傳入 `{"request_id":"q1","message":"問題"}`，stdout 回傳含相同 `request_id`、`output`、`observation` 的 JSON。

認證資料不等於資料存取權限：API 伺服器仍須檢查接收者的帳號、角色、租戶與資源權限，並負責金鑰發放、撤銷與到期。缺少必要變數會在送出請求前停止，401/403 不會繞過；HTTP 重新導向會被拒絕，請指定最終服務位址。跨電腦使用時，`localhost` 要換成可連到的伺服器位址，服務監聽與網路設定需另行準備；攜帶憑證的遠端服務應使用 HTTPS。瀏覽器登入、MFA 或跨站認證若無法獨立重建，需先補足認證契約。

交付包的 README 包含 Windows PowerShell 與 Linux／macOS bash/zsh 的 Python 版本檢查、建立本機虛擬環境、設定這份 adapter 所需憑證變數與啟動步驟。目前只需 Python 3.11+ 標準函式庫，不另附 `requirements.txt`，也不需執行 pip 安裝或複製生成者的 `.venv`。可選擇加上 `-I -S`，使用與生成時驗證相同的 Python 隔離模式。生成時的驗證只涵蓋當時執行的系統，不表示已在所有作業系統實測。

目前交付包限單題 `DATASET` 模式搭配 `--service-url` 與 `--project` 或 `--api-guide`。目的目錄必須不存在；`--resume` 只接受內容完全相同的既有交付包，不覆寫修改。重用舊 adapter 時，可加上 `--export-adapter` 將其驗證並保存為可攜版本；文件模式重用也會自動匯出。舊 adapter 必須符合可攜契約。未要求匯出的 `--adapt --project` 流程仍支援一般專案 adapter。生成程式以使用者權限執行；空目錄與 Python 隔離驗證不是作業系統沙箱。
缺少啟動、驗證或請求／回應資訊時仍需補充，不能只靠 URL 補齊。
`--page-url` 不能與 `--project` 或 `--service-url` 合用；詳見[目標選擇指南](site/zh-TW/guides/run-agent.html#choose-target)。

需要不同的選題或重跑方法時，改用本地 skill。內建 `run-agent-stability` 會將每題執行
三次，run sidecar 會保存選題、重跑次數與 seed：

```bash
lladar run-agent dataset.jsonl --project ../my-agent \
  --skill ./skills/random-sample --seed 42 --output responses.jsonl
```

skill 只會取得唯讀 cases，透過 host callback 排定執行；它不會寫入資料集或 responses 檔案。
兩個隨套件提供的 run skill 會直接在本機執行已封裝並留有雜湊證據的確定性
`strategy.py`，不會只為了計算排程而呼叫模型；自訂 run skill 仍由設定的 skill Agent 解讀。

若只有已登入的問答網頁，提供 `--page-url` 即可開始一次完整示範，不必自行找 API、Cookie 或 selector。

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl --lang zh-TW
```

請直接在互動終端執行。程式先揭露網站、實際模型目的地、資料範圍與額度；輸入 `YES` 後開啟可見瀏覽器。
在這個瀏覽器連續完成登入、網站驗證、進入服務、開新對話、送出終端顯示的完整示範題並等待完整回答，最後才回終端按 Enter。
不用分三段操作，也不用開 F12。程式保存事件、快照與必要登入／會話狀態後，會關閉錄製瀏覽器。

宿主先建立有界的錄製索引，材料 Agent 一次選出可能有用的事件與理由。adapter Agent 收到所選材料包後，只能提交完整 `adapt(payload)` 程式，或具體指出缺少哪項觀測；它沒有錄製搜尋或分頁讀取工具。宿主取得程式便先在本機檢查新題是否進入送題動作，通過後立即用真站試跑。失敗診斷與前版程式交回 adapter Agent 再提交；只有明確缺材料才回材料 Agent。初次選材與提交各限一次模型呼叫，未提交就停止並回報，不會在同一階段反覆分析。第一次真站試跑通過就停止生成。
宿主經 Monty VM 執行受控的 HTTP／SSE／WebSocket 操作計畫。預設獨立對話模式在試跑通過後另送兩個全新驗證題，才送正式問題。
正式執行不依賴可見或 headless 瀏覽器。協定能力不足時列出缺口，不自動改成 DOM 派送。

`--demo-question "這項服務能提供哪些協助？"` 可自訂示範題，請複製含唯一標記的整題。
`--conversation-policy independent` 預設每個正式試次建立新對話；`continuous` 在新的正式對話累積歷史，兩者均排除示範及驗證歷史。`--conversation-policy demonstrated` 明示沿用示範對話，以 `akasha.ask` 判斷試跑和正式回答是否具體、切題且合理；回答可能受示範與試跑歷史影響，不能宣稱支援建立新對話。
`--model` 選擇探索／修復模型，預設 `gemini:gemini-3.8-flash`，與受測網站模型分開；`--max-input-tokens`／`--max-output-tokens` 覆寫模型 profile。
探索 Agents 不讀取未來正式題目或預期答案，也不代答、摘要或改寫網站答案。

已授權的網站原文與選定服務必要狀態，包括 headers、Cookie／Authorization、body、客戶端 JavaScript、binary 及需要的 storage 項目，可供模型查閱。
原文與狀態放在本機私有工作產物；`.env`、provider key、完整瀏覽器設定檔及其他網站 storage 不交給 Agent。登入、MFA／CAPTCHA 由你處理，DOM 密碼／OTP 值不錄製。
`--verbose` 顯示 Agent 工具 trace，可能包含已授權原文，保存的日誌應視為私有資料；`--no-verbose` 仍保留必須操作的提示。

每題都檢查來源與完成。若無獨立全文基準，程式會暫時開網站供你核對同回合原文：符合輸入 `MATCH`，不符輸入 `MISMATCH`，未完成輸入 `WAIT`。
三個不同可信回合確認同一個原生 JSON 完整答案位置後，該位置及本題自然傳輸完成證據仍有效時，可由獨立宿主 reader 自動核對正式答案；其他來源仍可能需要人工核對。
核對或重新登入後即關閉瀏覽器；adapter 的正式送題與收取仍獨立執行。

私人 adapter、記錄索引、狀態、帳本與資格證據位於 `OUTPUT.work/browser-discovery`。
用 `--adapter PATH/adapter/manifest.json` 重用，預設不開瀏覽器，但仍需新題驗證；登入失效且無法更新才要求暫時登入。
用 `--resume OUTPUT.work` 續作；已提交或結果未知的題目先恢復原回合，不盲目重送。
舊瀏覽器旗標 `--probe-question`／`--browser-session-policy` 與舊 adapter 格式不相容。

工作量由 `LLADAR_BROWSER_DISCOVERY_*`、`LLADAR_INDEPENDENT_*`、`LLADAR_PROTOCOL_*`、`LLADAR_MAX_*` 集中設定。
失敗證據交回同一 Agent 修復，候選改版後重做試跑及兩題驗證；額度耗盡就保存進度並停止。
新版規格見[一次示範 PRD](docs/PRD-browser-single-demonstration.zh-TW.md)；驗證範圍見[新版驗證紀錄](docs/browser-single-demonstration-validation.zh-TW.md)。
本機真模型成功不能代替 ChatGPT、Gemini、EIP 與原生 Streamlit 的實際網站驗收。

自動評估：

```bash
lladar eval responses.jsonl --output evaluation.json
```

`eval` 預設顯示設定、逐題進度、模型等待提示與結果。用 `--no-verbose` 關閉進度，或 `--log eval.log` 保存。

兩種評估模式都支援 `--max-input-tokens N` 與 `--max-output-tokens N`（正整數）。未指定時使用所選模型 profile，評估 JSON 保存實際上限。

一般使用者用 `--criteria "描述要檢查什麼、什麼情況算符合"` 指定評估需求。
進階使用者改用 `--skill ./skills/my-verdict` 提供完整方法；兩者互斥。
都不指定時使用對應資料類型的預設評估；明確指定其中一種時，所有完成回答
均依該方法評估。若存在 trials sidecar，eval 會讀取每次嘗試，Python 計算彙總；
計畫包含 boolean `correct` 時才計算正確性穩定度。

建立情境時可用 `--max-input-tokens N` 與 `--max-output-tokens N` 分別覆寫
編製模型的 token 額度，兩者須為正整數；省略者使用所選模型的 profile 預設。
設定檔的 `created_with` 保存實際額度。這兩個引數只作用於建立設定。

情境設定為 `--max-turns 1` 時，也可以用網頁入口執行單輪情境，例如：

```powershell
lladar create situation --instructions "觀察回答是否使用簡體中文" --stop-criteria "收到第一個回答" --max-turns 1 --output situation.json --lang zh-TW
lladar run-agent --situation-config situation.json --page-url http://localhost:8501 --num-scenarios 3 --output transcripts.jsonl --lang zh-TW
lladar eval transcripts.jsonl --situation-config situation.json --output evaluation.json --lang zh-TW
```

網頁流程需要互動終端、登入與示範，並使用獨立對話模式；對話是否真正隔離仍取決於網站與 adapter，請檢查資格化證據。網頁多輪情境尚不支援。

`create situation --instructions TEXT`（或 `--instructions-file PATH`）一次規劃
生成、執行、停止與預設評估，保存完整 config。同批情境對話可保留原始 config，
再指定 criteria 或 Skill 重新評估：

```bash
lladar eval transcripts.jsonl --situation-config situation.json --criteria "觀察目標詞，區分引用與直接使用；命中不等同偏見" --output terminology-evaluation.json
```

結果的 `evaluation_settings` 保存實際模式、準則全文與雜湊，或 Skill 來源與資源證據。
重評不修改 config 或原始回答。情境建立的 `--knowledge` 提供給測試生成器；
受測 Agent 使用的知識仍須另外配置。

產生報告：

```bash
lladar report evaluation.json --output report.md
```

`eval` 會讓一個 Agent 固定 boolean、categorical 或 numeric 評估維度，再逐筆判讀。
筆數、比率與穩定性由 Python 計算。`report` 只解讀已儲存的事實，並附上逐次稽核表；
可用 `--skill ./skills/my-report` 指定本地報告方法。

## 作業結果報告

每次 CLI 階段命令在成功、失敗、取消或需要人工確認後，都會自動嘗試產生 UTF-8
Markdown 作業結果報告。涵蓋五種主流程、情境模式及五種 Skill 範本命令；
help、版本查詢與 Python API 呼叫不產生此報告。

例如 `--output artifacts/evaluation.json` 會產生
`artifacts/evaluation.json.operation.md`；範本目錄 `lladar-skills/eval` 的報告放在
目錄旁的 `lladar-skills/eval.operation.md`。重複執行依序使用 `.operation-2.md`、
`.operation-3.md`，即使 `--force` 也不覆寫歷次報告。尚未解析出輸出位置或該位置無法
寫入時，退回原工作目錄的 `.lladar/operation-reports/STAGE.operation.md`。

報告沿用 `--lang`，包含狀態、退出碼、時間、允許記錄的設定、輸入路徑、已知統計、
產物位置、經清理的問題與下一步；區分新增、修改與既有檔案，並在本次失敗提供工作階段
資訊時附上續跑指引。答案不正確是評估結果；執行或判斷錯誤才列為作業問題。
報告由程式產生，不增加模型呼叫，也不改變原本退出碼。

`--no-verbose` 仍顯示報告位置；日誌啟用後也會記入 `--log`。報告省略原始提示詞、
回答、URL、憑證與任意例外訊息，搭配終端日誌、生成依據報告與評估分析報告使用。
若兩處皆無法寫入，只顯示警告；程序被強制終止時無法保證報告存在。
詳細規格見 [作業結果報告 PRD](docs/PRD-operation-result-report.zh-TW.md)。

## 語言設定

所有流程、Skill 範本命令與 `--help` 都接受 `--lang CODE`。
新工作預設使用英文 `en`，包含新生成的問題與說明，不再隱含跟隨來源語言。

| 代碼 | 語言 |
| --- | --- |
| `en` | 英文 |
| `zh-TW` | 繁體中文 |
| `ja` | 日文，接受 `jp`／`Jp` 別名 |
| `ko` | 韓文 |
| `zh-CN` | 簡體中文 |

以上五種語言提供操作介面、固定報告標題與離線 Skill 範本。
其他代碼如 `fr` 可用於模型生成，介面、固定標題與範本使用英文並顯示簡短提示。
代碼接受英文字母與連字號分隔的英數子標籤，例如 `en-US`；接受代碼不代表模型一定支援該語言。
生成敘述仍可保留原始引文與專有名詞。

```powershell
lladar --lang ja --help
lladar create test-dataset --knowledge knowledge.md --lang zh-TW --output dataset.jsonl
lladar run-agent dataset.jsonl --project ../my-agent --lang zh-TW --output responses.jsonl
lladar eval responses.jsonl --lang zh-TW --output evaluation.json
lladar report evaluation.json --lang ko --output report.md
lladar create report-skill --lang zh-CN
```

語言設定涵蓋進度、操作提示、工作摘要與 LLaDAR 新生成內容。
已保存的測試問題、受測 Agent 回答、來源引文、ID、欄位名稱、程式碼與數值保持原值；
不會要求受測 Agent 切換語言，也不新增語言評分條件。
情境模式新生成的自由對話使用指定語言，精確 probe 與校準 token 保留。
網站答案擷取始終保留原回答；技術診斷細節與第三方追蹤可能保留原始語言。

下一步命令會帶上 `--lang`。新階段省略時使用 `en`；續跑省略時恢復保存的語言，
明確指定不同語言會在新工作開始前拒絕。沒有語言紀錄的舊工作階段，請使用原版本續跑或建立新工作。

Python 階段 API 接受 `lang="ja"`；預設 `None` 在新工作解析為英文、續跑時恢復原設定。
工作紀錄區分生成語言要求 `lang` 與實際介面語言 `ui_lang`；範本另有 `language.json`，
記錄 `template_lang`。多語範本維持相同工具與資料契約，保留可執行範例，建立時不需要模型或 API key。

## 作業紀錄

Verbose 出題時會即時顯示逐筆提交的候選預覽，以及最終採用的問句與標準答案；
若 Agent 一次交付整批檔案，則在交付後顯示。`run-agent` 在每次測試前顯示問句與
標準答案，收到回應後立即顯示回應答案；沒有回應就省略。彩色終端的問句為青色、
標準答案為綠色、回應答案為紫色。輸出會立即刷新，方便確認方向並按 Ctrl+C 中止。

在任何工作流程命令加上 `--log PATH`，將終端輸出同步保存為新的 UTF-8 檔案：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl --log logs/create.log
```

log 包含已輸出的進度、Agent trace、摘要與錯誤，移除顏色控制碼。
`--no-verbose` 會同時減少終端和 log 的詳細訊息。自動建立上層目錄；既有 log
會保留，每次執行請指定新檔名。

`--page-url` 的 verbose 輸出會顯示目前探索階段、安全的工具名稱與帳本用量，
等待期間定期顯示進度。工具參數、工具回傳內容與模型推理全文不進入一般日誌。
等待使用者按 Enter、輸入 YES 或 MATCH 時，暫停背景進度輸出。

在 Windows、Linux 或 macOS 互動式終端啟用 `--verbose`（預設開啟）時，執行期間可隨時按 Enter，立即顯示目前階段、
該階段耗時，以及距上次進度訊息的時間，不必等操作完成。適用於檔案檢查、圖譜建立、
adapter 生成／修復、模型等待與受測子程序執行。狀態寫入 stderr，使用 `--log` 時也會同步保存。
遇到登入、選項或 YES／MATCH 等人工輸入提示時，鍵盤監聽會暫停，Enter 維持原本的確認用途。
管線輸入、stderr 重新導向或 `--no-verbose` 不會啟動鍵盤監聽。
Linux／macOS 保留終端原本的行輸入與 Ctrl+C 行為，提早輸入的文字會交給下一個人工提示。
Ubuntu／macOS CI 會使用原生 PTY 測試狀態查詢與人工輸入交接。
模型擷取顯示輸入檢查、等待模型與結果驗證；正式回合僅在獨立全文證據不足時要求人工核對。
這些進度也會寫入 `--log`；`--no-verbose` 會隱藏。
每則 LLaDAR 進度附上含毫秒與 UTC 時差的本地日期時間，以及累計耗時（`[+12.345s]`）。
瀏覽器等待訊息另以 `stage_elapsed` 顯示當前階段耗時；log 保留相同時間標示。

瀏覽器失敗會在 verbose 與 log 顯示安全診斷：失敗階段、原因、有觀察到的 HTTP
狀態碼或逾時上限，以及是否嘗試請求。後續被封鎖的試跑會顯示
`reason=extraction_blocked request_attempted=false blocked_by=...`。
診斷也會存入 trials／run 紀錄；診斷欄位不包含原始例外、URL、headers 或回應內容。

串流失敗會區分 `stream_read_failed`（讀取失敗）與 `utf8_decode_failed`（UTF-8
解碼失敗）。`received_bytes` 是瀏覽器 reader 已收到的 HTTP 解壓後位元組數，
包含無法解碼的資料；`elapsed_seconds` 從啟動 fetch 算到失敗，不含模型抽取時間。

## Record 格式

JSONL 的每一行固定只有三個欄位：

```json
{"question":"台灣的首都是哪座城市？","expected_answer":"台北","actual_response":null}
```

`question` 與 `expected_answer` 必須是非空字串；`actual_response` 可以是字串或
`null`。不再使用 schema 版本，未知欄位會直接被拒絕。

## 輸出

- `create test-dataset`：`actual_response` 為 `null` 的三欄 JSONL 及 `<output>.generation.json`；使用有效圖譜時另有 `<output>.graph.json`
- `run-agent`：完成後的三欄 JSONL、`<output>.trials.jsonl` 與 `<output>.run.json`
- `eval`：包含評估計畫、逐筆判讀、覆蓋率與統計彙整的 JSON
- `report`：包含固定表格、解讀、限制與逐筆附錄的 Markdown

瀏覽器校正、確認或驗證若在資料集送出前停止，只會留下已遮罩的
`<output>.run.json` blocker evidence；不會建立 responses 或 trials 檔案。

既有輸出預設不會被覆寫，只有明確加入 `--force` 才會覆寫。完整行為與安全邊界請見
[目前的產品契約](docs/PRD-simple-agent-evaluation.md)。

## 開發

五個受測範例各自使用獨立的 uv 環境與鎖檔。安裝流程與 certifi 安全版本更新請見[範例建置說明](example_project/README.md)。

GitHub Actions 的真實測試讀取 Repository Secret `GEMINI_API_KEY`，並以 Repository Variable `GOOGLE_GENAI_USE_VERTEXAI` 選擇後端，預設為 `true`，使用 API key 的 Vertex AI Express 模式。使用 Gemini Developer API 金鑰時，請明確設為 `false`。CI 會先核對初始化後的實際端點，再執行真實呼叫；本機 `.env` 設定不會自動上傳到 GitHub。

```bash
python -m pip install -e ".[test]"
pytest
python -m playwright install chromium
python -m pytest -m browser
```

## 目錄結構與檔案用途

以下列出主要原始碼、文件與範例，作為閱讀導覽，而非完整檔案清單；省略產生的資料集、
報告、快取、虛擬環境與建置產物。

```text
LLaDAR/
├── pyproject.toml              # 套件資訊、相依套件與 lladar CLI 入口設定
├── uv.lock                     # uv 使用的相依套件版本鎖定檔
├── README.md                   # 英文總覽與快速入門
├── README.zh-TW.md             # 繁體中文總覽與快速入門
├── qa_agent.py                 # 獨立的 Akasha 問答腳本，非 lladar CLI 入口
├── src/lladar/                 # 安裝後使用的 Python 套件
│   ├── cli.py                  # CLI 參數解析與指令分派
│   ├── api.py                  # 建立測試資料集的 Python API
│   ├── skill_generation.py     # 來源證據、圖譜／出題規劃階段與資料集輸出
│   ├── skill_agent.py          # 銜接 Akasha 原生動態 skill 載入的薄介接層
│   ├── method_skill.py         # run/eval/report 共用的 skill 路徑解析與呼叫
│   ├── skill_assets/           # 套件內建 skills 與輔助資源
│   │   ├── knowledge-point-qa/       # 資料集生成方法（SKILL.md）
│   │   ├── run-agent-stability/      # 重複執行方法（SKILL.md 與 strategy.py）
│   │   ├── run-agent-random-sample/  # 隨機抽樣方法（SKILL.md 與 strategy.py）
│   │   ├── eval-answer-verdict/     # 回答評估方法（SKILL.md）
│   │   └── report-evidence-summary/ # 依據證據撰寫報告的方法（SKILL.md）
│   ├── runner.py               # 執行受測 Agent，記錄回答與逐次試驗
│   ├── run_strategy.py         # 執行排程與策略驗證
│   ├── auto_adapter.py         # 探索、驗證並重播受測專案的 adapter
│   ├── project_profile.py      # 根據原始碼證據描述受測專案
│   ├── browser_target.py       # 瀏覽器受測目標的校正與問題重播
│   ├── evaluation.py           # 回答判讀與確定性的統計彙整
│   ├── reporting.py            # 將已儲存的評估事實與文字解讀組成報告
│   ├── semantic_graph.py       # 將探針回答分類至來源候選項
│   ├── controlled_variants.py  # 選取並驗證 planner 提出的控制維度
│   ├── question_types.py       # 題型與探針契約、指紋及計分
│   ├── records.py              # 資料集紀錄的讀取、驗證與寫入
│   ├── loaders.py              # 載入知識來源文字
│   └── providers/              # 模型供應者介面與 Akasha 實作
├── tests/                      # 自動化測試、瀏覽器測試與受測專案 fixtures
├── scripts/                    # 真實環境驗收、打包與發版驗證腳本
├── example_project/            # 端到端受測專案範例
│   ├── diet/                   # 飲食問答 Agent 與知識來源
│   ├── resume_review/          # 履歷審查服務與終端操作範例
│   └── tainan_tutorial/        # 台南推薦示範、adapters、資料與測試
├── examples/support-demo-agent/ # 客服 Agent HTTP 示範服務
├── akasha-agent-example/       # 簡單的 Akasha Agent 與 hello-skill 範例
├── docs/                      # PRD、設計說明、驗證紀錄與簡報
├── site/                      # 英文文件網站
│   └── zh-TW/                 # 繁體中文文件網站
├── .github/workflows/         # 發版與文件網站部署自動化
└── .codex/skills/             # 開發助手使用的 skills，與執行時的 skills 分開
```

若要追蹤資料集生成，建議從 `cli.py` → `api.py` → `skill_generation.py` 閱讀；
skill 載入實作在 `skill_agent.py`，方法指令則在 `skill_assets/*/SKILL.md`。
其他指令可分別從 `runner.py`（`run-agent`）、`evaluation.py`（`eval`）與
`reporting.py`（`report`）開始。若要自訂方法，使用 `--skill` 指定本地 skill 目錄，
不必修改套件內建資源。

若要了解受測專案整合，可接著查看 `adapter_workspace.py`、`project_inventory.py`、
`graph_discovery.py` 與 `interfaces.py` 的工作目錄檢視及探索流程；
`target_environment.py` 與 `service_runtime.py` 的執行環境及服務管理；以及
`browser_discovery_driver.py`、`browser_agent_loop.py`、`browser_provenance.py`、
`protocol_runtime.py`、`response_capture.py` 與 `answer_extraction.py` 的探索、來源核對及回答抽取。

## 大資料與模型限制

LLaDAR 的下載與抽樣上限是程式設定的保護額度，並非大語言模型的上下文硬性限制。
目前 GitHub 資料集生成採用以下額度：

| 額度 | 目前上限 | 用途 |
| --- | ---: | --- |
| 一般文字下載 | 每次回應 16 MiB | 控制來源下載量 |
| CSV／JSONL 串流抽樣下載 | 單檔 16 MiB | 控制來源檔案大小 |
| 累計下載 | 128 MiB | 控制總傳輸量 |
| 抽樣掃描 | 100 萬個邏輯資料列 | 控制掃描工作量 |
| 抽樣保留 | 100 列 | 控制後續生成工作量 |

抽樣仍須符合選取文字與模型額度；100 列不代表一定放得進模型。
上述限制適用於 GitHub 生成，不是四階段所有輸入或瀏覽器回答的共用上限。

模型的輸入／輸出 token 額度另外控制。內建生成由程式分配按模型輸入 token 預算四分之一計算字元額度的
來源範圍，模型輸入額度較小時會縮小批次。字元、UTF-8 位元組與 token 是不同單位；
下載大檔案不等於把整份檔案一次交給模型。

提高 `--max-input-tokens` 或 `--max-output-tokens` 不會提高下載／抽樣上限，
也不會擴大服務商模型本身的容量。下載上限目前是內建額度，沒有專用 CLI 調整選項。
提高程式額度時，仍須驗證掃描、儲存、跨批整合、跨批關係及執行成本。

已通過的離線案例包括超過 2 MiB 的 10 萬列 CSV、CSV 欄位換行、JSONL 有界掃描、
84000 字元本地文字分批，以及透過 callable 目標完整保存約 1.7 MB 的單筆回答。
callable 案例不代表提高瀏覽器擷取上限。GB／TB 級資料尚未驗證；超限、模型或磁碟
問題仍可能讓階段中止，已有 session 時可依中斷提示接續。保存證據會區分抽樣與
完整資料處理。

已測試範圍及待驗收事項見[驗收紀錄](docs/validation/recoverable-stages-2026-10-11.zh-TW.md)。

## 接續中斷的工作

生成、`run-agent`、`eval` 與 `report` 會在輸出旁保存 session，通常為 `<output>.work`。
中斷時 CLI 會顯示原工作目錄、輸入與輸出位置、實際 session 路徑，以及可直接執行的接續指令。
保留原輸入、模型、方法和選項，使用提示中的實際路徑：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl --resume dataset.jsonl.work
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl --resume responses.jsonl.work
lladar eval responses.jsonl --output evaluation.json --resume evaluation.json.work
lladar report evaluation.json --output report.md --resume report.md.work
```

Python API 接受 `resume=Path(...)`。一般執行仍可使用 `--force`；只有接續時不能與 `--resume` 同用。
原本以 `--force` 啟動的覆寫授權會保存於 session，接續指令會移除 `--force`。
輸入、Skill、準則、模型設定或保存證據改變時，接續會被拒絕。已完成試次不重跑；完成狀態未知的目標請求不自動重送。
私密網站或服務網址由本機輸入，不回顯於提示或 log。工作目錄包含來源證據、草稿與實際回答，請依原始輸入權限保護並保留至恢復結束。

內建生成支援分批；GitHub CSV／JSONL 支援串流抽樣，但有下載、掃描與模型額度限制。
抽樣不等於完整 benchmark 重現。實際範圍见[實作與驗收紀錄](docs/validation/recoverable-stages-2026-10-11.zh-TW.md)。

### 工作量額度設定

各模型呼叫共用所選模型 profile。瀏覽器抽取支援 `--max-input-tokens` 與
`--max-output-tokens`；預設 Gemini 為 1,048,576 輸入與 65,536 輸出 tokens。
未知模型的可設定備用請求額度為 131,072／65,536，這不代表該 provider 保證支援；
實際能力不同時請明確指定 token 參數。

內容大小、下載、DOM 與等待額度集中於
[`resource_limits.py`](src/lladar/resource_limits.py)。每個項目皆可在啟動前以
`LLADAR_` 加上項目名稱的環境變數覆寫，不必再改程式。預設擷取 16 MiB、
模型輸入 JSON 4 MiB、回答文字 4 MiB、provider 回傳封裝 32 MiB、DOM 65,536 節點、
每節點 4,096 次歷史快照、抽取共用期限 3,600 秒。明確覆寫會納入續跑設定，續跑時需保持相同值。

```powershell
$env:LLADAR_MAX_INPUT_BYTES = '8388608'
$env:LLADAR_MAX_CAPTURE_BYTES = '33554432'
$env:LLADAR_EXTRACTION_TIMEOUT_SECONDS = '7200'
lladar run-agent DATASET.jsonl --page-url URL --max-output-tokens 65536
```

瀏覽器示範錄製另有獨立的 500 MiB 總額度（`LLADAR_MAX_RECORDING_BYTES`）；單次回應擷取上限仍為 16 MiB。

工具分頁不會截掉整份內容，可用回傳的下一頁位置繼續讀取。資料格式檢查、
憑證偵測與已授權網站請求次數，仍獨立於工作量額度執行。
