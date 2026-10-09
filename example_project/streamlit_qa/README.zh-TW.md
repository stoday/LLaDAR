# Streamlit 問答測試頁

使用原生 `st.chat_input`、`st.chat_message` 與 `st.session_state`，每題固定回覆 `Answer: 問句`，不呼叫外部模型，也沒有另外加 HTTP API。

在 LLaDAR 專案根目錄啟動：

```powershell
uv run --extra test --extra streamlit-test streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

開啟 http://127.0.0.1:8501，在下方輸入問題。Ctrl+C 停止測試頁。

執行真瀏覽器相容性測試：

```powershell
uv run --extra test --extra streamlit-test pytest tests/test_streamlit_browser.py -m browser
```

需要已安裝 Playwright Chromium。測試自行啟動 loopback Streamlit、使用獨立瀏覽器 profile，結束時關閉自己啟動的服務。

以 Streamlit 1.65.0 實測：原生二進位、持續開啟的 WebSocket 仍無法走 transport-only 重播。Browser mode 現在可在校正階段選擇 DOM 路徑，觀察本題 running→idle 完成，再用原生聊天元件執行驗證及資料集問答。測試同時保留通訊擷取的負向案例，以及 BrowserTarget／CLI 成功案例與安全 evidence；選定路徑後固定使用，失敗停止後續派送。

使用自己的資料集（需 Gemini 抽取憑證及互動式 YES／MATCH 確認）：

```powershell
uv run lladar run-agent path/to/dataset.jsonl --page-url http://127.0.0.1:8501
```

整合測試使用固定抽取 provider，驗證瀏覽器流程與產物，未驗證真模型的抽取忠實度。

`agent.py` 提供獨立 `ask_agent(question)`，讓日後專案模式適配可直接使用；一般測試驗證網頁、browser transport 邊界與 DOM 完整流程；未執行自動 adapter。


真 Gemini 的 opt-in CLI 測試已用 `gemini-3.8-flash` 驗證四次抽取及兩題中文答案逐字一致，詳見 [真模型驗收紀錄](../../docs/streamlit-browser-validation.zh-TW.md)。需明確設定 `LLADAR_LIVE_GEMINI=1` 並選擇 `-m live_gemini`；一般測試會跳過此付費 API 案例。
