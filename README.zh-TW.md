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

## Record 格式

JSONL 的每一行固定只有三個欄位：

```json
{"question":"台灣的首都是哪座城市？","expected_answer":"台北","actual_response":null}
```

`question` 與 `expected_answer` 必須是非空字串；`actual_response` 可以是字串或
`null`。不再使用 schema 版本，未知欄位會直接被拒絕。

## 快速開始

產生資料集：

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

預設使用隨套件安裝的 Akasha `knowledge-point-qa` skill，先抽取知識點，再逐點產生 QA。
無須另外安裝 skill；會另產生 `dataset.jsonl.generation.json` 保存原文引用與執行狀態。

需要 Akasha 1.8 以上。`--skill DIRECTORY` 可改用一個可信任的本地 skill；
不需要額外 manifest，尚未加入 skill 安裝管理命令。內建方法不屬於可移除的使用者安裝項目。
詳見 [skill 生成使用說明](docs/skill-generation.md)。

相容性調整：資料集只透過 skill 生成，`create test-dataset` 不再接受 `--method`、
`--chunk-size`、`--overlap`、`--strict`、`--prompt`、`--prompt-file`；Python
`create_test_dataset()` 也不再接受對應的舊參數或 `provider`。所有階段都使用內建或
本地 skill；`run-agent`、`eval` 不再接受 prompt 指引。

執行目標 Agent。LLaDAR 會檢查專案副本，替真實公開流程建立暫時 adapter：

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

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

網站模式固定引導你完成登入、校正、授權與 `MATCH`，不必選擇互動模式。
不要加 `--interactive` 或 `--no-interactive`：這兩個旗標僅供專案模式使用，
與 `--page-url` 合用會被拒絕。請直接在 stdin、stderr 都連接終端的環境執行；
若以管線提供輸入或重新導向 stderr，CLI 會在開瀏覽器前停止，核准旗標也不會略過此要求。

每次網站請求預設最多等待 60 分鐘（`--timeout 3600`）。
瀏覽器啟動、首次導頁與重新導頁各自最多等 5 分鐘（300 秒）；
`--timeout` 不會縮短或延長導頁等待時間。
瀏覽器模式只保留一條答案抽取路徑：由沒有工具權限的模型讀取經同意的完整回應，
還原原本的答案。不再產生 Python，也不保留內建答案規則、Monty、解析器快取或策略選項。
僅瀏覽器模式預設使用 `gemini:gemini-3.8-flash`，可用 `--model gemini:MODEL` 指定；
模型是否可用仍取決於供應商帳號。專案探索及評分的模型預設不變。

LLaDAR 開啟專用 Chromium 視窗，由你自行登入、原樣送出校正題，等答案完成才按 Enter。
一次看完網站請求、模型目的地及預算後，輸入一次 `YES`，同時核准一次驗證題、
畫面列出的資料集請求，以及將**真實回應內容（包含內部答案）**傳到
`https://generativelanguage.googleapis.com`。請先確認組織允許這些內容外傳。
`--confirm-browser-run` 與 `--allow-response-model-transfer` 仍只各自核准原有範圍；
只有一個旗標時仍缺另一項授權，兩者都提供才免授權提示，但不能跳過核對。
舊的「只准網站請求」或「只傳合成證據」同意不能取代這次完整授權。

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
需要重新登入、取得新同意再執行，不暗中重送。舊解析器旗標直接拒絕；
既有私密快取不讀取、不遷移，也不刪除。

自動評估：

```bash
lladar eval responses.jsonl --output evaluation.json
```

可用 `--skill ./skills/my-verdict` 指定本地評估方法。若存在 trials sidecar，eval 會讀取
每次嘗試並由 Python 計算逐題穩定性。

產生報告：

```bash
lladar report evaluation.json --output report.md
```

`eval` 會讓一個 Agent 固定 boolean、categorical 或 numeric 評估維度，再逐筆判讀。
筆數、比率與穩定性由 Python 計算。`report` 只解讀已儲存的事實，並附上逐次稽核表；
可用 `--skill ./skills/my-report` 指定本地報告方法。

## 輸出

- `create test-dataset`：`actual_response` 為 `null` 的三欄 JSONL；skill 模式另有 `<output>.generation.json`
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
