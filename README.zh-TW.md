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

執行目標 Agent。LLaDAR 會檢查專案副本，替真實公開流程建立暫時 adapter：

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

也可以用 `--entrypoint` 指定進入點。它會從 `LLADAR_QUESTION` 收到問題，並將最終
回應輸出到 stdout。

自動評估：

```bash
lladar eval responses.jsonl --output evaluation.json
```

或直接指定評估目標：

```bash
lladar eval responses.jsonl --prompt "評估事實正確性與答案完整度。" --output evaluation.json
```

產生報告：

```bash
lladar report evaluation.json --output report.md
```

`eval` 會讓一個 Agent 固定 boolean、categorical 或 numeric 評估維度，再逐筆判讀。
筆數、比率、分佈、平均數、中位數、最小值與最大值由 Python 計算。`report` 只解讀
已儲存的事實，並附上逐筆稽核表。

## 輸出

- `create test-dataset`：`actual_response` 為 `null` 的三欄 JSONL
- `run-agent`：完成後的三欄 JSONL 與 `<output>.run.json`
- `eval`：包含評估計畫、逐筆判讀、覆蓋率與統計彙整的 JSON
- `report`：包含固定表格、解讀、限制與逐筆附錄的 Markdown

既有輸出預設不會被覆寫，只有明確加入 `--force` 才會覆寫。完整行為與安全邊界請見
[目前的產品契約](docs/PRD-simple-agent-evaluation.md)。

## 開發

```bash
python -m pip install -e ".[test]"
pytest
```
