# LLaDAR

[English](README.md) | [繁體中文](README.zh-TW.md)

LLaDAR 是一套精簡的 Agent 評估流程，只保留四個工作項目：

1. 從知識文件產生問題與預期答案。
2. 執行目標 Agent，收集真實回應。
3. 由評估 Agent 自動選擇或遵循指定方法，再由 Python 確定性地計算統計結果。
4. 產生有證據邊界的 Markdown 報告。

## 安裝

```bash
python -m pip install lladar
```

支援 Python 3.11 與 3.12。預設模型 provider 會從 `.env` 或程序環境讀取憑證。

使用 `lladar --version` 查看已安裝的版本。

## Record 格式

JSONL 的每一行固定只有三個欄位：

```json
{"question":"台灣的首都是哪座城市？","expected_answer":"台北","actual_response":null}
```

`question` 與 `expected_answer` 必須是非空字串；`actual_response` 可以是字串或
`null`。不再使用 schema 版本，未知欄位會直接被拒絕。

## 快速開始

準備知識文件與目標 Agent 專案後，依序執行：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
lladar eval responses.jsonl --output evaluation.json
lladar report evaluation.json --output report.md
```

開啟 `report.md` 檢視結果。瀏覽器測試、自訂評估需求與多輪情境的用法見下方說明。

## 使用選項

產生資料集：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

預設使用隨套件安裝的 Akasha `knowledge-point-qa` skill，讀取知識段落並生成有來源依據的問答。
Agent 依選定 skill 選擇直接問答，或使用圖譜產生概念映射及受控變體 probe；圖譜不是必經流程。
也可使用 Python 產生或修正候選結果，正式輸出前會驗證最終資料。
每份資料集附有 `dataset.jsonl.generation.json`；只有使用有效圖譜時才產生 `dataset.jsonl.graph.json`。

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

該選哪個目標參數？

- `--project PATH`：從專案程式碼與文件理解對外入口。只有專案也可能足夠：啟動資訊與必要執行環境設定完整時，LLaDAR 可以在隔離的專案副本中啟動服務、測試，最後只關閉自己啟動的服務。
- `--project PATH --service-url URL`：仍檢查專案，但改用已啟動的測試服務。URL 提供的是**服務基底位址**，不是啟動指令或 API 契約；HTTP 方法、路徑、Payload、驗證需求與回應格式仍從專案理解，不會啟動或關閉該既有服務。
- `--page-url URL`：不必提供專案原始碼，改用正常問答網頁。在 LLaDAR 瀏覽器登入並送出校正題後，錄製請求供重播，再由模型從擷取的回應中抽取答案。

已啟動測試服務的範例：

```bash
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

`--service-url` 是專案模式的選用參數，不是任意 API 的自動探索功能。
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

若只有已登入的問答網頁，不必找 API method、payload 或 Cookie。先安裝與目前
Playwright 版本相符的 Chromium，然後提供「問答頁面 URL」：

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl
```

可加上 `--calibration-question "你們的客服服務時間是什麼？"`，讓手動校準與自動驗證
原樣使用同一句正常問句，不附加識別碼。未指定時保留原有隨機 `LLaDAR` 校準與驗證
問句。此引數僅適用於 `--page-url`，不可為空或全空白。

網站模式固定引導你完成登入、校正、授權與 `MATCH`，不必選擇互動模式。
不要加 `--interactive` 或 `--no-interactive`：這兩個旗標僅供專案模式使用，
與 `--page-url` 合用會被拒絕。請直接在 stdin、stderr 都連接終端的環境執行；
若以管線提供輸入或重新導向 stderr，CLI 會在開瀏覽器前停止，核准旗標也不會略過此要求。

每次網站請求預設最多等待 60 分鐘（`--timeout 3600`）。
瀏覽器啟動、首次導頁與重新導頁各自最多等 5 分鐘（300 秒）；
`--timeout` 不會縮短或延長導頁等待時間。
瀏覽器模式由沒有工具權限的模型讀取經同意的完整回應，還原原本的答案。
僅瀏覽器模式預設使用 `gemini:gemini-3.8-flash`，可用 `--model gemini:MODEL` 指定；
模型是否可用仍取決於供應商帳號。

LLaDAR 開啟專用 Chromium 視窗，由你自行登入、原樣送出校正題，等答案完成才按 Enter。
一次看完網站請求、模型目的地及預算後，輸入一次 `YES`，同時核准一次驗證題、
畫面列出的資料集請求，以及將**真實回應內容（包含內部答案）**傳到
`https://generativelanguage.googleapis.com`。請先確認組織允許這些內容外傳。
`--confirm-browser-run` 與 `--allow-response-model-transfer` 各自核准網站請求與回應傳送；
只有一個旗標時仍缺另一項授權，兩者都提供才免授權提示，但不能跳過核對。

模型從環境或 `--env-file` 讀取 `GEMINI_API_KEY`／`GOOGLE_API_KEY`，
取得校正及同意後才初始化。若排程共有 N 次試跑（重複題也計次），模型最多呼叫 **N+2 次**：
校正、驗證，以及每次試跑各一次；每個回應不重試，每次最多 8,192 輸出 tokens，
所有呼叫及中間驗證共用同意後的 60 分鐘期限，不自動跟隨重新導向。
這些限制約束用量，不保證固定金額。內建排程仍在本機執行；自訂 run skill 可能另用模型。

送出資料集前，直接在 terminal（stderr）比較新驗證題的抽取全文與完整錄製回應，
忠實且完整才輸入 `MATCH`，不再開 HTML 核對頁。核准旗標不能跳過此步驟；
CLI 在任何瀏覽器操作前就會確認輸入與核對輸出都連接終端。
青色標題表示原始回應、紫色表示抽取答案、黃色表示提醒；顏色不代表正確性。
`NO_COLOR` 或 `TERM=dumb` 改用純文字。完整內容不截斷，逐行引用，反斜線及控制／格式字元
以可見轉義呈現，不改動保存的答案。終端捲動紀錄或錄影可能保留私密內容。
登入與擷取仍需要 Chromium；此調整不是無瀏覽器模式，也不新增正式題目的逐題 MATCH。
新請求可以回答相同文字，但把舊錄製改名不算獨立驗證。
模型回傳合法格式、或一次核對通過，**都不保證後續每題忠實，也不代表目標答案正確**。
評分仍另外執行 `lladar eval`。

支援文字、JSON／+json、NDJSON 與 SSE 作為模型輸入。模型不得自行回答問題、摘要、
修正答案或改動數字、否定詞、空白與 Markdown；宿主只檢查來源、完成狀態、容量及固定輸出格式，
不綁定答案欄位名稱。串流需實際關閉，或校正時有本次回覆的人工完成確認；
自動重播若一直不關閉，會逾時停止，不靠終止事件名稱猜答案。

擷取上限為 HTTP 解壓後的 1 MiB；模型輸入另有較嚴格的 **120 KiB UTF-8 JSON**
上限（含封裝），在本機 128 KiB 位元組預算內為固定 prompt 保留空間。
這不是對供應商 tokenizer 或宣告 context window 的保證。超限不截斷、不摘要、不自動分段。
回應若疑似含憑證，或回顯已知 request 憑證，會整筆阻擋傳輸；保守偵測不能保證找出所有敏感內容。
不傳 Cookie、headers、request payload、profile 或標準答案；一般產物不保存原始回應、
完整 prompt、模型診斷或思考內容。最終答案仍以 `actual_response` 存入 responses／trials；
run sidecar 只記錄安全狀態、模型與 prompt 版本、次數、時間及供應商提供的 token 用量，缺值為未知。

登入狀態沿用 `.lladar/browser-profiles`；`--fresh-browser-profile` 改用暫時的未登入 profile，
不複製日常 Chrome／Edge。401/403、抽取失敗或超限會停止後續送題並保留已完成答案；
需要重新登入、取得新同意再執行，不暗中重送。

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

`--page-url` 的 verbose 輸出會顯示瀏覽器載入、校準擷取、請求解析、等待網站回應與
模型答案擷取。長時間等待每 5 秒顯示已等待時間，收到完整回應時顯示位元組數。
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
`playwright_driver.py`、`response_capture.py` 與 `answer_extraction.py` 的瀏覽器操作與回答擷取。
