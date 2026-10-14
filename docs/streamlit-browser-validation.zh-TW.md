# 原生 Streamlit browser transport 實測

> 歷史紀錄：以下數字、測試命令與固定 DOM 選路證據屬於 2026-10-09 的舊人工校正架構。相關舊測試與 driver 已刪除，命令不再作為現行入口；這些結果不能證明新的 Agent 探索／生成協定程式架構已通過 Streamlit 驗收。現行條件與結果請看[探索 PRD](PRD-browser-agent-discovery.zh-TW.md)及[新架構驗收證據](browser-agent-discovery-validation.zh-TW.md)。下文保留原始驗證數字與當時的限制，供追溯使用。

日期：2026-10-09。版本：Streamlit 1.65.0、Python 3.11.11、Playwright Chromium。

測試頁：[example_project/streamlit_qa](../example_project/streamlit_qa/README.zh-TW.md)。
當時的測試：`tests/test_streamlit_browser.py`（已刪除）。

```powershell
uv run --extra test --extra streamlit-test pytest tests/test_streamlit_browser.py -m browser
```

結果：`1 passed`，39.96 秒。

- 真 Streamlit 程序以 loopback 位址啟動，Chromium 實際操作原生聊天輸入。
- 一般問句及含中文、引號的校正題皆在 UI 正常收到固定答案，先前對話仍存在。
- 瀏覽器觀察到 `/_stcore/stream` 的送出及收到 frame 都有二進位訊息；完成問答後原連線未關閉。
- transport-only driver 無法把它錄製成文字／JSON 問句模板，校正擷取逾時。測試明確預期並驗證此失敗。
- 測試自動關閉自己啟動的 Streamlit 與 Chromium，不保留服務，不呼叫外部模型。

這是原生 Streamlit 的負向相容性案例，不能當作新 WebSocket 功能的成功驗收。文字 WebSocket 的成功案例仍由 `test_websocket_driver.py` 提供。二進位長連線支援需另外設計，不在使用者本次確認的「每題正常關閉」範圍內。


## DOM 路徑實測（2026-10-09）

同一個未加 HTTP API 的原生 Streamlit 測試頁新增兩個公開邊界案例：

- `BrowserTarget.prepare／answer`：同次校正選 DOM、獨立驗證及兩題問答皆完成；UI 只有校正、驗證及兩題共四次提交，抽取也是四次。
- `run-agent --page-url`：真 Chromium／Streamlit 產生 responses、trial 與 run sidecar；run 包含 `browser_route=dom`、綁定版本與 `busy_to_idle`，不包含 selector 或元件內容。

完成證據來自公開 DOM 的 `data-test-script-state` running→notRunning 及本次新增的 user／assistant 對話；不讀取 Streamlit 私有狀態或解碼 protobuf。每題只傳送完整、可見的本題答案給抽取 provider。

固定 provider 是外部模型的測試替身；這些案例驗證實際網頁操作、選路、額度及產物，未驗證 Gemini 對任意答案的忠實度。其他 Streamlit 版本或不同頁面結構仍須重新校正；找不到可信完成證據時停止。


最終 DOM／Streamlit 驗收：`24 passed`（431.69 秒）。命令：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
uv run --extra test --extra streamlit-test pytest tests/test_dom_browser_target.py tests/test_streamlit_browser.py -m browser -x
```


## 延長時限與真 Gemini 驗收（2026-10-09）

使用者授權延長測試時間並使用真 Gemini。API 模型清單確認本機帳號支援 `gemini-3.8-flash`；憑證從本機 `.env` 讀取，未輸出或寫入驗收紀錄。

- 既有滴流案例的測試期限從 2 秒改為 10 秒，讓 Windows worker 能先建立請求。仍要求 10 秒內不得提早完成、13 秒前須終止、只送一次 HTTP 請求且無重試，並確認 server 觀察到連線斷開。該案例與完整 provider suite 通過：`23 passed`（56.11 秒）。產品的 deadline 行為與預設值未變更。
- 真 Chromium／Streamlit／`run-agent --page-url` 與真 Gemini 完整流程通過：`1 passed`（35.98 秒）。網站等待上限 60 秒，每次模型呼叫上限 180 秒；共四次模型抽取：校正、獨立驗證、兩題中文資料集。
- 中文問句 `今天可以吃什麼？` 與 `不要加糖，份量是 250 克。` 的兩份 `Answer: 問句` 答案均逐字一致，保留否定、標點與數字。responses／trial／run sidecar 正常產生，四份 provider 輸入都只有本題答案；不含 expected-answer sentinel、其他題目、headers 或 cookies。
- 獨立驗證由測試程式另行讀取本機 UI 答案並比對模型結果；這是已知合成頁面的自動化 reference，不是真人 MATCH 驗收。未宣稱任意網站、答案格式或 Streamlit 版本皆通過真模型驗證。
- 本次用量合計：prompt 1272、answer 60、thoughts 725、total 2057 tokens。只記錄數值，不保存模型思考內容或憑證。安全紀錄見 [browser-live-gemini-validation.json](browser-live-gemini-validation.json)。

真 API 測試需明確啟用，避免一般回歸自動產生費用：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LLADAR_LIVE_GEMINI='1'
uv run --extra test --extra streamlit-test pytest tests/test_streamlit_browser.py -m live_gemini -s -x
Remove-Item Env:\LLADAR_LIVE_GEMINI
```

可用 `LLADAR_LIVE_ENV_FILE` 指定本機憑證檔，或 `LLADAR_LIVE_MODEL` 指定已確認可用的 Gemini 模型。上述成功結果取代先前「兩秒時限案例未通過、未呼叫真 Gemini」的驗收狀態；先前紀錄保留為歷史證據。
