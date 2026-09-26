# LLaDAR 自動 adapter：從探知介面到執行

`lladar run-agent DATASET --project PATH` 的目的是以待測專案**既有的公開流程**回答資料集問題。LLaDAR 不要求專案預先符合某個函式名稱或回傳型別；它在受管理的專案副本中探索出入口，產生一個獨立 Python adapter，先重播驗證，再用它執行整份資料集。

這份文件說明 adapter 的資料流、產物與手動重播界線。它不是答案品質的保證；`verified` 只表示 adapter 在驗證 probes 中可以重播。

## 一覽

```text
待測專案原始碼
    │  複製（原始專案不會被修改）
    ▼
探索 agent：讀程式碼並提出公開入口、流程與可觀察輸出
    │  驗證介面計畫，選擇公開邊界
    ▼
生成 agent：寫出獨立的 Python harness / adapter
    │  以兩個 probe 在新的專案副本中重播
    ▼
verified adapter
    │  每題各以新的專案副本執行
    ▼
responses.jsonl（只填入 actual_response）與 run evidence
```

## 1. 建立受管理的待測專案副本

`run-agent` 接到 `--project` 後，會先複製專案到目前工作目錄下的 `.lladar/runs/<run-id>/<project-name>/`。複製時排除 `.env`、`.git`、`.venv`、`node_modules`、既有 `.lladar` 等內容，因此 adapter 不會寫回原始專案。

目標 Python 預設使用待測專案的 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`（Unix）；若專案不採用這個位置，必須以 `--target-python PATH` 明確指定。

## 2. 探索：找出真正的入口與輸出

`AutoAdapter.prepare()` 會建立第一個探索 agent。它只有下列唯讀能力：

- `list_files`
- `read_file`
- `search_code`
- `query_graph`
- `check_runtime`（只確認環境變數或執行檔是否存在，不讀取密值）

探索 agent 的工作是提出候選的**公開介面**，例如 CLI、HTTP route、既有 service API 或專案對外包裝流程，並說明：

- 問題從何處送入（entrypoint）
- 必須經過哪些中介流程（flow）
- 最終回答如何被觀察與取出（output）
- 來源檔與行號證據，以及尚未釐清的條件

LLaDAR 驗證此計畫後，會以原子寫入保存為：

```text
.lladar/runs/<run-id>/adapter/interfaces.json
```

若候選不唯一、有未解問題，或服務啟動資訊不足，流程會停在 `needs_confirmation`；不會猜測內部方法，更不會直接繞過 middleware、route 或專案自己的 Agent 流程。

探索階段刻意沒有 `write_harness`。這是為了先把「應測哪個公開行為」和「如何寫測試橋接程式」分開，避免尚未選定介面就修改或執行任意程式。

## 3. 選定介面後，由第二個 agent 產生 adapter

選定一個完整的公開介面後，LLaDAR 才建立第二個 coding agent。這個 agent 收到：

- 已選定介面的完整描述與來源證據
- 使用者提供的 intent 與澄清
- 最多兩個不含預期答案的 probe 問題

它可使用前述探索工具，另外取得：

- `write_harness`：寫一個獨立 Python 檔
- `run_harness`：在新專案副本中執行、觀察失敗並修正整合問題

`write_harness` 只允許在複本內寫入簡單檔名且語法可編譯的 Python 檔；語法錯誤不會覆蓋上一個可用版本：

```text
.lladar/harnesses/<adapter-name>.py
```

因此生成的是橋接程式，不是對待測專案入口／出口原始碼的修改。橋接程式必須沿著選定的公開流程送出問題，不能改接內部模型或 Agent 方法來取得看似正確的答案。

## 4. adapter 的 I/O 協定

adapter 從標準輸入讀取一個 JSON 物件：

```json
{"request_id":"request-001","message":"測試問題"}
```

它在標準輸出只能印出一個 JSON 物件：

```json
{
  "request_id": "request-001",
  "output": "目標專案實際觀察到的最終回答",
  "observation": "此回答是如何從公開流程取得的"
}
```

`request_id` 必須完全相同；`output` 與 `observation` 都必須是非空字串。目標服務的 log、診斷訊息與進度輸出必須寫到 stderr，不能混入 stdout JSON。

## 5. 獨立驗證與資料集執行

生成 agent 回報 harness 檔案後，LLaDAR 會：

1. 讀取 harness 原始碼並保存雜湊。
2. 對每個 probe 建立新的專案副本與新的程序。
3. 在副本中執行 adapter，檢查 stdout JSON、request-id 關聯、非空答案與 observation。
4. 檢查 adapter、service helper 與待測專案程式碼在執行期間沒有被更改。
5. 所有 probes 成功才把狀態標為 `verified`。
6. 對正式資料集的每一題，再各建立新的副本與程序執行 adapter，並只寫入輸出 JSONL 的 `actual_response`。

任何生成、協定、程序、HTTP、逾時或答案觀察驗證失敗，都會進入最多 50 次的通用修正迴圈。每次修正使用新的 coding agent，並重新提供固定 I/O 協定、完整的 `harness`／`explanation`／`blockers` proposal 格式、最後一個送交獨立驗證的 adapter 候選、外層修正歷史、所有驗證失敗的去重目錄與完整時間線，以及 `write_harness` 等工具層錯誤。相同錯誤文字只在目錄保存一次，時間線仍保留每次發生的位置，避免大量重複訊息擠掉其他舊錯誤。Agent 依這些證據自行判斷目標專案的修法；LLaDAR 不會把特定錯誤對應到硬編碼修補。

`run_harness` 的 `message` 參數是工具呼叫時使用的純文字 probe；真正啟動 adapter 時，LLaDAR 一律將它包成第 4 節的 JSON stdin。每次 coding-agent 回合都有獨立的 `--max-tool-calls` 預算，稽核紀錄則跨回合保留。

若 adapter 程序已完成但後續協定驗證失敗，`run_harness` 會在 `verification.diagnostic` 附上 adapter 的 stderr 尾端；已知憑證值會先遮罩，provider debug metadata 也不會傳回。這讓 agent 能觀察目標程式的真實 stdout/stderr 診斷，不必根據同一個表面錯誤反覆猜測。

常見證據位於：

```text
.lladar/runs/<run-id>/adapter/
├── interfaces.json       # 探索與選定的公開介面
├── adapter.py            # 被驗證的 adapter 原始碼副本
├── harnesses/            # 從工作副本保存的生成 harness
├── audit.json            # 探索／寫入／執行工具事件
├── run.json              # proposal、選擇、驗證結果與狀態
└── observations.jsonl    # 每次驗證或資料集執行的觀察記錄
```

`run.json` 的 `status: "verified"` 代表重播協定與整合成功，**不代表**資料集答案必然正確，也不代表所有未覆蓋的使用路徑都已驗證。

## 可以手動執行 harness 嗎？

可以，但請把它當作除錯或人工重播，而非 LLaDAR 完整驗證的替代品。最安全、最可重現的方式仍是重新執行 `lladar run-agent`，因為它會提供新副本、timeout、暫存目錄、環境注入、雜湊檢查與每題隔離。

若要手動重播一個已驗證 request，請使用 `observations.jsonl` 中該筆的 `workspace` 欄位。它指向當次保留的待測專案副本；同一層目錄已有 LLaDAR 當次實際執行的 `adapter.py`，以及需要時的 `lladar_service_runtime.py`。使用待測專案的 Python，並在該 workspace 作為目前目錄執行：

```powershell
# 以實際路徑、問題與 request id 取代這三個值。
$workspace = 'C:\path\to\.lladar\runs\<run-id>\adapter\requests\<request-run>\<project-name>'
$targetPython = 'C:\path\to\target\.venv\Scripts\python.exe'
$requestId = 'manual-replay-001'
$request = @{ request_id = $requestId; message = '測試問題' } | ConvertTo-Json -Compress

$env:LLADAR_REQUEST_ID = $requestId
Push-Location $workspace
try {
    $request | & $targetPython (Join-Path (Split-Path $workspace -Parent) 'adapter.py')
}
finally {
    Pop-Location
}
```

輸出應是一個符合上述協定的 JSON 物件。若 adapter 需要 API key、服務 URL、Node 等 runtime，手動環境也必須以相同方式具備它們；LLaDAR 不會把 `.env` 或秘密複製到 run artifact。

也可查看 `adapter/harnesses/*.py` 或 `adapter/adapter.py`，但不要假定任一檔案能在任意目前目錄直接執行：某些 HTTP/service adapter 需要與 `adapter.py` 同層的 helper，並預期目前目錄是待測專案副本。直接從原始專案目錄執行亦失去「不碰原始碼」與每題隔離保護。

## 程式位置

- `src/lladar/runner.py`：建立專案副本並呼叫 `AutoAdapter.prepare()`。
- `src/lladar/auto_adapter.py`：兩階段 agent、介面選擇、adapter proposal、probe 驗證與每題執行。
- `src/lladar/interfaces.py`：驗證與原子保存 `interfaces.json`。
- `src/lladar/adapter_workspace.py`：限制 `write_harness` 只能寫入受管理的 `.lladar/harnesses/*.py`。

## 手動重播的限制

- 只應對受信任的專案使用。專案副本是狀態隔離，不是作業系統安全沙盒；生成的 Python 仍以使用者權限執行。
- 手動成功只證明該次輸入能跑通，無法取代 LLaDAR 的 probes、原始碼雜湊檢查或整份資料集的隔離執行。
- 不要把 adapter 的 stdout 改成 log；stdout 是機器可讀的唯一回應通道。