# Graphify 與 REST 入口驗證

需求：[PRD](PRD-graph-rest-discovery.md)。日期：2026-09-19。

## 實作

- `run-agent` 預設使用 Graphify；`--no-graphify` 關閉，`--graphify-python` 指定獨立工具 interpreter。
- 已安裝的 uv tool Graphify 0.9.61，與 LLaDAR、待測 Python 各自獨立。只執行結構萃取，不執行待測 source，不使用 Graphify 文件語意 API。
- 每次對 run 的過濾副本建圖，保留原始方向、來源位置、推論標記、版本、雜湊與涵蓋資訊；先提供有限入口鄰近節點，再允許 agent 查詢。圖譜不替代來源核對。
- 缺少工具、失敗、超時、超過 2000 檔／50 MB 時回退原始碼探索。停用與回退有不同狀態。
- REST 候選描述啟動、就緒、請求、回應、涵蓋、未涵蓋與缺漏。缺漏需要補充，不能選候選強行略過。
- 生成的 Python adapter 透過原有 HTTP API 呼叫服務，使用獨立 localhost port 與 stdlib helper 啟動、等待、關閉服務；保留應用 trace 與服務日誌。
- `--service-url` 明確指定既有測試服務；禁止憑證嵌入 URL，保存設定並重新探索，不應啟停既有服務。此模式有合約驗證檢查，未包含在下列完整付費驗收中。

## 本機驗證

60 項聚焦檢查通過，涵蓋真實 Python／TypeScript Graphify 萃取、有界方向查詢、停用／缺少工具回退、來源證據、暫停續跑、HTTP 合約及環境隔離。
加入舊 PID 保護後，另重跑 13 項 Graphify／REST 檢查全部通過（包含一項新增檢查）。

服務生命週期測試啟動真實 Node server，檢查正常完成、就緒失敗、adapter 出錯、整體 adapter 超時四種情況，確認 port 已關閉。沒有 mock HTTP server 或 provider。

Wheel 成功建置，核對包含 graph_discovery.py、service_runtime.py 與 interfaces.py。

## 付費驗收

可從 repository root 執行（實際呼叫模型，會產生費用）：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/verify_rest_graph_live.py --target-python ..\VIDE-TESTING\examples\langchain_agent\.venv\Scripts\python.exe --env-file ..\VIDE-TESTING\.env
```

測試專案 `tests/fixtures/rest_agent` 包含 TypeScript 範例 client、Node REST server 與 Python agent。使用 Gemini 真實 provider 與 LangChain 工具；測試 API `POST /api/chat` 是專案本來的公共路由。沒有新增測試路由，也沒有手動撰寫或修改生成的 adapter。

第一輪停在 `needs_confirmation`：探索正確找到 HTTP 入口，但把已提供的環境與依賴當成缺漏。加入具名變數／執行檔「是否存在」工具，以及不洩漏值的執行環境說明；以配置完整的測試設定重新驗收。第一輪紀錄保留在 `.lladar/rest-graph-live.log`。

第二輪完整通過：`.lladar/live-rest-graph-20260919-141541-992944/verification.json`，日誌 `.lladar/rest-graph-live-v2.log`。
Graphify 0.9.61 成功處理 Python、JavaScript、TypeScript，圖譜查詢結果有保存在探索 audit。
兩次獨立 replay 與三筆正式測試全部成功；逐一確認 `server_started → http_received → initialized → knowledge_loaded → tool_called → model_answered → http_postprocessed` 事件、`API客服：` 最終加工、服務停止紀錄與 port 已關閉，原始 fixture 雜湊不變。

驗收後另加上 request ID 對應的清理保護，避免讀到複製專案裡的舊 PID 紀錄時關閉不相關程序；此變更以真實服務超時與舊紀錄拒絕測試檢查。

## 限制

- 本版 Python adapter 可以啟動已安裝的 Node 等 runtime，仍需獨立 Python interpreter 作為 adapter runtime。
- 同步 JSON 的真實服務是本次完整驗收範圍；SSE、工作佇列輪詢、登入與瀏覽器完整流程仍需各專案的實際驗證。
- 靜態圖譜不能證明所有動態或跨服務關係，未解析檔案與前端業務處理需另行核對。
- `verified` 是 adapter 可重播，不是答案品質評分，也不是任意專案的入口正確性保證。
