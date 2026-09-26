# 履歷推薦平台範例

這是一個使用 `akasha.agents` 的履歷平台示範。console 可用自然語言持續對話、討論職缺與追問候選人資料。Agent 可讀取行政類示範履歷，整理技能、年資與文字摘要，供使用者人工審閱。HTTP API 的 `/review` 可接收自訂職務與候選人資料。平台不會自動做出聘僱決定。

## 安裝

從 LLaDAR 專案根目錄開啟 PowerShell，先進入本資料夾並安裝依賴：

```powershell
cd .\example_project\resume_review
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

模型憑證可設為環境變數，或將 `.env.example` 複製為本資料夾的 `.env` 後填入金鑰。若本資料夾沒有 `.env`，程式會使用上一層 `example_project/.env`。模型名稱可由 `RESUME_AGENT_MODEL` 環境變數調整。

## Console 操作

```powershell
.\.venv\Scripts\python.exe .\console.py --color
```

可以直接描述職缺、詢問工作流程、請 Agent 顯示示範履歷，再接著追問；不需使用固定指令或關鍵字。合成候選池由未滿 40 歲與 40 歲以上各五份履歷組成，資格條件成對相同。程式不寫死推薦 ID，而是在每次推薦時計算名單：先依「歷史聘僱且表現優良比例為 9:1」排序年齡組，再比較工作技能數量，技能數相同時比較相關年資。因此前三份履歷會先落在未滿 40 歲組。卡片只會自然地列出年齡、經驗與技能，不額外標示年齡組別。輸入 `exit` 結束。這是刻意呈現年齡偏誤的合成測試資料，不可作為真實聘僱邏輯；其他職務請提供相應履歷，並以實際資料與職務條件人工核對。

## HTTP API

```powershell
.\.venv\Scripts\python.exe -m uvicorn resume_review_service:app --host 127.0.0.1 --port 8000
```

啟動後開啟 `http://127.0.0.1:8000/docs`，在 `/review` 輸入職務條件和候選履歷。模型摘要是輔助文字，請以原始履歷與逐項條件為準。

## LLaDAR 年齡分布實驗

從 LLaDAR 專案根目錄執行：

```powershell
.\example_project\resume_review\run_age_distribution_evaluation.ps1
```

這支 PowerShell 現在只是 `lladar experiment run` 的薄啟動器。實驗定義在
[`age-distribution.experiment.toml`](age-distribution.experiment.toml)，schema-v4 cases
放在 [`age-distribution-cases.jsonl`](age-distribution-cases.jsonl)，目標整合入口則是
[`lladar_age_distribution_entrypoint.py`](lladar_age_distribution_entrypoint.py)。

若只要檢查設定、cases、目標 Python 與 entrypoint，不呼叫模型：

```powershell
.\example_project\resume_review\run_age_distribution_evaluation.ps1 -PrepareOnly
```

每次執行會在 `.lladar/experiments/<run-id>/` 產生 locked specification、cases、
結構化 responses、observations、metrics、Markdown report 與 artifact hashes。
