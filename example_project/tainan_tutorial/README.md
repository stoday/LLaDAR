# 臺南古蹟導覽偏見測試展示系統

這是一套 offline-first 的本機展示系統，用同一個導覽 Agent 的兩組表現呈現落差：

- 固定題測它是否「知道」敘事框架原則。
- 實際導覽題測它在自然任務中是否「做得到」。
- factuality 與 framing 分開顯示，不合併成一個會互相抵銷的分數。
- Replay 使用已核准的 fixture 與 golden verdict，不需要網路或模型金鑰。

## Windows 一鍵啟動

在這個目錄按右鍵以 PowerShell 執行：

```powershell
.\start-demo.ps1
```

預設使用 `127.0.0.1:8764`；若要暫時指定其他連接埠：

```powershell
.\start-demo.ps1 -Port 9000
```

啟動器會先檢查連接埠：若同一套展示服務已在執行，會顯示網址並正常結束；若是其他程序占用，會列出程序資訊並停止，不會再啟動第二個 Uvicorn。

或從 LLaDAR repository root 複製貼上：

```powershell
uv sync --project .\example_project\tainan_tutorial --extra test; uv run --project .\example_project\tainan_tutorial tainan-demo serve
```

瀏覽器開啟 <http://127.0.0.1:8764>。首頁只有「開始完整展示」與「載入既有結果」兩個主要動作。

## 常用命令

```powershell
# 執行離線 replay，輸出 JSON 與獨立 HTML 報告
uv run tainan-demo run

# 執行測試
uv run pytest --basetemp .pytest-tmp

# 選用：透過 akasha-terminal 執行 live 模式
uv run tainan-demo run --mode live --model gemini:gemini-2.5-flash
```

Live 模式不套用 Replay 的 golden verdict；它只顯示 deterministic signals 並標成待討論，避免拿預錄答案的判定錯套到新回答。

## 展示流程

1. 點選「開始完整展示」。
2. 先看固定題原則通過率，再看情境題框架偏差率。
3. 展開題目卡片，檢視 Agent 原答、史實、框架、觸發片段、規則 ID 與理由。
4. 若需人工覆核，在題目卡片內輸入覆核者、結果與原因。
5. 下載 JSON 或可獨立開啟的 HTML 報告。

## 架構邊界

- `domain.py`：公開資料契約，不 import akasha-terminal。
- `adapters.py`：`ReplayAdapter` 與 `AkashaAgentAdapter`；Live agent 不提供任何工具。
- `rules.py`：只產生 deterministic signal，不直接判定 fail。
- `orchestration.py`：逐題執行、錯誤隔離、provenance、雙指標與人工覆核。
- `reporting.py`：安全保存、載回、JSON 與 self-contained HTML。
- `web.py`：FastAPI API 與鍵盤可操作的繁體中文展示頁。
- `cli.py`：Rich console、run/list/serve 命令。

每次 run 記錄 Git commit、lock hash、案例／政策／提示詞版本、model、adapter、temperature、seed、回答、判定、latency、token、retry 與 replay fingerprint。API key、cookie、authorization header 和環境變數不會寫入輸出。
