# 預設圖譜探索與 REST 入口

## 本版要求

- 自動入口模式預設嘗試 Graphify，本機 AST 唯讀建圖，不執行受測程式、不進行文件 LLM 萃取。可用 `--no-graphify` 關閉。
- Graphify 使用獨立工具環境；可指定 `--graphify-python`。缺少工具、解析失敗或超時時記錄原因，回退原始碼探索。未解析檔案與推論關係不得宣稱已驗證。
- 每次 run 對其副本重新建圖，保存檔案雜湊、版本、圖譜與涵蓋資訊。agent 只能取得有限的相關節點與有方向關係，回讀原始碼核對。
- HTTP 入口的候選必須描述啟動方式、就緒條件、請求、回應、涵蓋與未涵蓋流程。缺少啟動/驗證資訊時暫停讓人補充，不能靠選候選略過必要資訊。
- REST adapter 呼叫原有 API，不新增測試路由、不繞過 middleware/路由直接呼叫內部函式。預設啟動獨立 localhost 實例，每次 request 各自隔離；保留日誌、限制等待時間並清理自身程序。
- `--service-url` 可明確提供既有測試服務，模式為 existing，候選 URL 必須完全符合使用者提供值；不啟停該服務。URL 不接受內嵌憑證、query 或 fragment。resume 時新增 URL 需重新探索並保存設定。
- 保持 Python adapter 為協調器，可啟動 Node 等已安裝 runtime；不自動安裝待測依賴。SSE 與 job polling 依來源合約產生對應解析，不把 debug/progress 當答案。
- 有前端處理或跨程序連結無法證明時，列明缺口請人確認。瀏覽器 UI 自動化不在本版承諾。

## 驗收

真實 Graphify 解析 Python/JavaScript 並保留方向；測試關閉、缺少工具與有界查詢。
實際啟動 REST server，經原有 API middleware、知識、工具、模型與後處理；使用真實付費模型，不 mock provider。
核對每次獨立 replay/正式資料的完整 trace，確認服务已關閉。記錄測試與尚未涵蓋的服務形式。
