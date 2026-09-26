# Browser 直接模型抽取：遷移與本機驗證

日期：2026-09-25。這是新版路徑紀錄，不覆寫先前的 parser／真網站驗收歷史。

## 已遷移

- `--page-url` 僅用工具關閉的模型直接抽取；瀏覽器預設模型為 `gemini:gemini-3.8-flash`。
- 移除生成 parser、Monty、內建答案規則、parser cache 與舊 CLI flags；沒有相容別名或 fallback。
- `--allow-response-model-transfer` 核准真實回應內容傳輸，與網站請求保有各自權限範圍。
  互動提示現已合併，見下方追加修訂；單項旗標不擴權。
- 每次回應一次模型呼叫，N 次排程試跑最多 N+2 次；失敗計次，不重試、不重播網站。
- 校正後須有全新、同來源且本文綁定的驗證回應，並通過本機獨立參考／人工全文核對。
- 完成狀態只看傳輸結束或本次人工完成確認，不靠答案欄位或 SSE 事件名稱猜測。
- 已完成答案在中途錯誤／人工取消時保留；未完成輸出不寫入 `actual_response`。
- 網站 401/403 會停止後續請求，保留專用 profile；重新登入與重送需下一輪新同意。
- 瀏覽器啟動、首次導頁及重新導頁各自等待上限為 5 分鐘；網站回答仍預設 60 分鐘，
  模型與中間驗證共用的 60 分鐘期限不變，自動重播另受整輪剩餘期限約束。
- 原始回應、模型 prompt／thoughts／錯誤本文不進一般產物；原因碼、階段及可得用量可留存。
- 專案模式、callback、packaged skill、三欄資料、eval／report 職責未變。

## 直接抽取初版：本機驗證的範圍

外部模型以合成 provider 或 loopback HTTP server 取代，瀏覽器以本機合成登入網站驗證。
沒有向 Gemini 或內部網站發出新請求，也沒有使用過去已耗盡的模型額度。

- 模型傳輸：單次 tool-free HTTP、無 redirect／重試、限流／認證／服務錯誤、deadline、
  output token 完成原因、回傳 schema、重複 JSON keys、拒絕 source／工具呼叫、thoughts 隔離。
- 隱私：Cookie／request 憑證不入 prompt；回顯憑證、疑似憑證與跳脫後的憑證欄位阻擋傳輸。
  這是保守偵測，不是所有秘密都可被識別的保證。
- 工作流：網頁正常登入與表單校正，JSON、巢狀／字串 JSON、NDJSON、SSE replacement／fragments，
  請求身分、獨立核對、大小限制、session 重用／fresh、過期停止、取消保存、N+2 額度。
- 核對頁：HTML／script／iframe 等注入呈現為文字，不執行、不對外連線。
- 產物：CLI → responses／trials／run sidecar → eval；wheel／sdist 不含舊 browser parser、
  Monty 依賴、profile、快取或 `.lladar` 私密材料。

測試中的固定模型可驗證「宿主確實傳對資料、正確處理模型結果」，**不能驗證真模型能否
辨識所有封裝，也不能當成真模型逐字忠實度的證據**。DE2／DE3／DE6 的模型行為與 DE9 仍待驗收。

## 直接抽取初版：結果

- Windows／專案 `.venv`：非瀏覽器回歸 **233 passed、2 skipped**；兩項跳過為 POSIX signal 專用測試。
- 已配置的 Chromium／本機合成網站：瀏覽器相關回歸 **50 passed**。
- 兩組均有既有 Akasha 引入 `langchain-experimental` 的棄用警告，沒有測試失敗。
- `lladar run-agent --help` 已核對；`git diff --check` 通過。
- wheel／sdist 建置、模組／依賴／私密資料邊界檢查及直接從 wheel 匯入的 smoke test 通過。
  產物位於 `.lladar/direct-extraction-dist-20260925-final`；未安裝、上傳或發布。
- 導頁期限測試先觀察到原本沿用短 request timeout 的失敗，再確認修訂後啟動／導頁／重載
  都使用 300 秒，而測試設定的 1 秒回答期限保持不變。先前併行回歸曾出現 2–5 秒的
  導頁逾時；以上 50 項結果是採使用者確認的新期限後重新完整執行，不改寫先前失敗。

本次執行命令如下；再次執行 pytest 時請使用新的暫存目錄名稱：

```powershell
.venv/Scripts/python.exe -m pytest -m browser --basetemp .lladar/test-direct-final-browser-5min --tb=short
.venv/Scripts/python.exe -m pytest -m 'not browser' --basetemp .lladar/test-direct-final-nonbrowser-5min --tb=short
.venv/Scripts/python.exe -m build --no-isolation --outdir .lladar/direct-extraction-dist-20260925-final
.venv/Scripts/python.exe scripts/verify_browser_package.py .lladar/direct-extraction-dist-20260925-final
```

## 追加修訂：一次完整授權與 terminal 彩色核對

使用者確認後，保留校準請求錄製及同 session 重播；合併原本 YES／TRANSFER 為一次
完整揭露的 YES，內部分別記錄網站請求與模型傳輸授權。兩個既有旗標仍只核准各自範圍。
核對從 Playwright 移至獨立的 terminal 顯示 Module，移除 HTML 核對分頁，而非增加選項。

- 先列出網站、請求次數、Gemini 目的地／模型、真實內容範圍、N+2／8192／60 分鐘預算
  與可能費用，再詢問一次 YES。拒絕時不初始化模型、不自動重播網站。
- MATCH 仍在新驗證題之後、正式 dataset 之前；它是忠實度核對，不是第二次授權。
- 以互動 stderr 顯示完整來源與抽取文字，青／紫／黃區分標題；NO_COLOR／TERM=dumb
  改用純文字。反斜線、C0/C1、ANSI／OSC、CR 與 Unicode 格式字元可見轉義，逐行引用，
  不截斷也不改寫 actual_response；HTML／script 只顯示成文字。
- 非 TTY 不輸出原文，缺少既有可信 reference_reader 時在模型與自動請求前停止。
  說明 terminal 捲動紀錄／錄影仍可能留存內容；一般產物不新增原始回應或核對檔。
- 不新增正式題逐題 MATCH、不改 5 分鐘導頁／60 分鐘回答上限，也不提供無瀏覽器模式。
- 已使用真 PowerShell PTY 和純合成文字，完成純文字／彩色顯示及輸入 MATCH 的 smoke test。
  這不是真網站或真模型驗收；本輪未向內部網站或 Gemini 發出請求，既有私密結果未修改。

本輪回歸結果：

- 非瀏覽器：**246 passed、2 skipped**（Windows 不適用的 POSIX signal 測試），68.95 秒。
- 全套瀏覽器：**50 passed**，584.93 秒；前述 19 項工作流子集也通過，不重複加總。
- 合計 **296 passed、2 skipped**；CLI help 的追加核對另有 1 項通過。
- wheel／sdist 與從 wheel 匯入的 terminal／CLI smoke 均通過；新模組有封裝，舊 HTML
  核對方法不存在。產物位於 `.lladar/terminal-review-dist-20260925`，未安裝或發布。
- 首次建置因 Windows 系統 TEMP 的 `input.json` 清理遭 WinError 5 拒絕而失敗；改用
  僅對該次建置程序生效的專案專用 TEMP／TMP 後成功，沒有調整系統權限或清理他人暫存。
- PRD、CLI help、README 及網站中英文使用說明同步；`git diff --check` 通過。

```powershell
.venv/Scripts/python.exe -m pytest -m 'not browser' --basetemp .lladar/test-terminal-final-core --tb=short
.venv/Scripts/python.exe -m pytest -m browser --basetemp .lladar/test-terminal-final-browser --tb=short
.venv/Scripts/python.exe scripts/verify_browser_package.py .lladar/terminal-review-dist-20260925
```

## 待另行授權

先以真模型處理純合成 corpus，量測逐字一致、遺漏、重複、改寫、歧義／損壞拒絕、延遲與用量；
再另行取得真網站自動請求數及真實回應內容外傳同意，執行人工校正與核對。
先前只允許合成結構的同意，不能用於傳送原始內部問答內容。

## 舊資料保存

既有 `.lladar` 登入 profile、私密快取及歷史診斷均未刪除；舊快取不再讀取。
已移除的舊解析器程式與舊測試，另有本機備份
`.lladar/direct-extraction-migration-backup.zip`，可手動復原，不會進入封裝。
