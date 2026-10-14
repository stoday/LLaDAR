# Streamlit 問答測試頁

使用原生 `st.chat_input`、`st.chat_message` 與 `st.session_state`，每題固定回覆 `Answer: 問句`，不呼叫外部模型，也沒有另外加 HTTP API。

在 LLaDAR 專案根目錄啟動：

```powershell
uv run --extra test --extra streamlit-test streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

開啟 http://127.0.0.1:8501，在下方輸入問題。Ctrl+C 停止測試頁。

使用 LLaDAR 瀏覽器模式前，先安裝 Playwright Chromium：

```powershell
uv run python -m playwright install chromium
```

目前的 `--page-url` 流程由 Akasha Agent 探索實際頁面操作與通訊，產生協定／會話程式，再經獨立探測題與新驗證回合資格化。通用工具會觀察原生 Streamlit 的二進位 WebSocket；核心不固定選擇 Streamlit DOM 路徑，也不預裝專用 Streamlit codec。生成的解碼仍須通過宿主來源檢查及獨立完整回答核對。

保持啟動 Streamlit 的終端開啟，再用另一個終端執行資料集：

```powershell
uv run lladar run-agent path/to/dataset.jsonl --page-url http://127.0.0.1:8501 --output streamlit.responses.jsonl
```

在本機設定選定的 Akasha 模型；`--model PROVIDER:MODEL` 選擇探索、修復與無工具抽取所用的模型。測試網頁本身不呼叫模型，但 LLaDAR 的探索與抽取可能產生付費模型呼叫。依終端提示同意網站操作／模型傳輸，再完成全文核對。

正式回答的自動核對，需要至少三個不同回合經可信的全文資格化，且當回合的來源結構、完成證據與完整 DOM 仍有效。目前的獨立 reader 要求觀測到 `aria-busy` true→false，並逐字符合完整 subtree。若此頁無法提供這些證據，或原始格式無法逐字還原，受影響的回答仍須人工 `MATCH`；模型彼此同意不能代替這項核對。

[原生 Streamlit harness](../../scripts/verify_streamlit_discovery_live.py) 提供不呼叫模型的乾跑模式：

```powershell
uv run python scripts/verify_streamlit_discovery_live.py --timeout 30 --output-root .tmp/streamlit-discovery-dry
```

預設連接已啟動的原生頁面 `http://127.0.0.1:8517`；上方範例使用 8501，請加上 `--page-url http://127.0.0.1:8501`。乾跑只驗證原生 UI 回答、對話歷史與持續二進位 WebSocket 觀察，不能證明 Agent 已生成可用的二進位解碼。加上 `--paid` 才明確啟用真 Akasha 探索／抽取及通訊解碼驗收。結果以[探索 PRD](../../docs/PRD-browser-agent-discovery.zh-TW.md)及[現行驗收證據](../../docs/browser-agent-discovery-validation.zh-TW.md)為準；有付費 harness 不等於已通過驗收。

[2026-10-09 驗收紀錄](../../docs/streamlit-browser-validation.zh-TW.md)保留的是已退役的人工校正／固定選路實作，不能證明目前 Streamlit 探索通過。一般回歸測試不授權付費模型呼叫。

`agent.py` 另提供 `ask_agent(question)`，可供專案模式適配；這份文件示範正常的 Streamlit 問答網頁。
