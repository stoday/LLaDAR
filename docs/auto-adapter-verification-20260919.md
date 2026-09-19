# 自動接入待測專案：實測紀錄（2026-09-19）

已將 VIDE-TESTING 的讀碼、生成 adapter、試跑修正及獨立重跑機制整合進
LLaDAR。省略 `--entrypoint` 即使用自動模式；既有指定入口模式保留。

## 真實 API 結果

探索、待測 Agent 與 evaluator 均使用真實 Gemini API，沒有以 mock 代替。
兩個專案的既有 `.venv` 分別提供框架依賴，沒有改寫其模型、查詢工具或原始碼。

| 待測專案 | 自行找到的接法 | 獨立驗證 | 正式資料 | 執行／ID／judge 錯誤 |
| --- | --- | --- | --- | --- |
| VIDE-TESTING/examples/langchain_agent | create_support_session → invoke → 最終 AI 訊息文字區塊 | 2/2 | 3/3 | 0 / 0 / 0 |
| VIDE-TESTING/examples/llamaindex_agent | await process_ticket → 以 request ID 查 SQLite reply | 2/2 | 3/3 | 0 / 0 / 0 |

LangChain 探索模型為 `gemini:gemini-3-flash-preview`；LlamaIndex 探索模型為
`gemini:gemini-2.5-flash`。待測專案保留自身的 `gemini-3-flash-preview` 設定；
兩份 evaluator 報告使用 `gemini:gemini-2.5-flash`，啟用 `--strict`。

每份資料是一個手工編寫的 schema-v2 接入測試群組：原題、資訊刪除題、親屬線索題。
這次驗證的是接入與評分銜接，沒有驗證資料生成器的品質，也不是框架能力排名。
兩份結果的 original accuracy 均為 1、scoring coverage 均為 1、BFS 均為 0。
LangChain 有一筆 completed_no_answer；變體與原題答案存在差異。
接入成功不代表待測 Agent 的偏誤評分通過。

## 可查核證據

- [彙整 JSON](../.lladar/live-auto-adapter-20260919/summary.json)
- [LangChain 答案](../.lladar/live-auto-adapter-20260919/langchain-verified-answers.jsonl)
  · [評分](../.lladar/live-auto-adapter-20260919/langchain-verified-evaluation.json)
  · [生成的 adapter](../.lladar/runs/20260919-131105-516760/adapter/adapter.py)
  · [驗證紀錄](../.lladar/runs/20260919-131105-516760/adapter/run.json)
- [LlamaIndex 答案](../.lladar/live-auto-adapter-20260919/llamaindex-final-answers.jsonl)
  · [評分](../.lladar/live-auto-adapter-20260919/llamaindex-final-evaluation.json)
  · [生成的 adapter](../.lladar/runs/20260919-130928-298597/adapter/adapter.py)
  · [驗證紀錄](../.lladar/runs/20260919-130928-298597/adapter/run.json)

各 run 的 `audit.json` 與 `observations.jsonl` 保存探索操作、獨立驗證及正式題目
結果。`.lladar` 是忽略中的本機產物，不隨 Git 版本保存。兩個待測專案各四個
來源／設定檔已與初始副本比對雜湊，內容保持一致。

## 發現並處理的失敗

- LangChain 回傳的 content 可能是文字區塊陣列；直接作為 `output` 不符合字串協定。
  已補強提示與型別診斷，讓 agent 自行抽取真正的文字。
- 一次 `gemini-2.5-flash` 探索反覆修正失敗，生成程式加入除錯佔位文字。
  該次已人工檢視並停止，未採用其 adapter；中止原因保存在
  `.lladar/runs/20260919-130843-563714/adapter/interruption.json`。
- 已拒收已知的 provider metadata／框架除錯輸出，並遮蔽早期探索紀錄中的
  provider 除錯 metadata。一般錯誤會遮蔽已知憑證值。
- stdout 無合法 JSON 時，會提供受限制的 stderr 診斷；避免只有 JSON parser
  錯誤而無法修復真正的執行問題。
- Windows 預設 pytest 暫存根目錄存在權限問題，改用 `.lladar` 下全新測試目錄後通過。

## 本機驗證

31 項無 mock 的聚焦檢查通過，涵蓋真實子程序協定、狀態隔離、request ID、
空／非字串答案、metadata 拒收、timeout、原始碼修改拒收、錯誤憑證遮蔽、
工作目錄邊界、空資料集、schema 驗證及既有入口／CLI 相容性。
未將包含假 provider 的既有完整測試套件算入此次驗證。

`uv build --wheel` 成功，已檢查 wheel 包含新模組與更新後的技能資產，且未包含
`.env` 或 `__pycache__`。沒有執行 fresh-install 或 Linux 實測。

## 重現

提供 [付費驗證腳本](../scripts/verify_auto_adapter_live.py)，保留相同的手工測試群組，
依序執行兩個專案與 strict 評分。腳本介面和資料驗證已檢查；上表是分別執行等價
CLI 命令所得的結果。從 LLaDAR 根目錄執行：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_auto_adapter_live.py --env-file ..\VIDE-TESTING\.env
```

每次會建立新的 `.lladar/live-auto-<timestamp>/`，會再次產生 API 費用。需保留
VIDE-TESTING 範例及其既有環境，也可用 `--examples` 指定其位置。

目前實測範圍是 Python 訊息回傳與本機 SQLite；HTTP、互動式 CLI、多機服務或
需要人工登入的專案尚未在本次驗證。生成程式的語意正確性仍需檢視，協定檢查
無法證明任意 adapter 絕無造假；專案副本也不是 OS 安全沙箱。

## 後續補強：強制分離套件環境

原版有「找不到待測 `.venv` 就回退到 LLaDAR Python」的行為，已移除。
自動與指定入口模式都要求獨立待測環境；`--target-python` 可以選另一個既有環境，
但不能指回控制端環境。以真實子程序讀取 `sys.prefix` 確認，而非只比較執行檔名稱。
也拒絕啟用 system-site-packages 的 venv，清除繼承的 Python 套件搜尋路徑、
使用者 site 與控制端啟用設定。兩端可以共用相同 Python 基底版本，但各自擁有套件環境。

新增真實 venv／子程序測試，確認同名模組在控制端載入 v1、待測端載入 v2，
不受控制端 `PYTHONPATH` 影響；相關聚焦檢查合計 37 項通過，wheel 再次建置成功。

使用真實 API 完整重跑 LlamaIndex：獨立驗證 2/2、正式答案 3/3 成功。
這輪專注環境與執行驗證，沒有再次執行 evaluator。

- 控制端 prefix：`C:\Users\today\Projects\LLaDAR\.venv`
- 待測端 prefix：`C:\Users\today\Projects\VIDE-TESTING\examples\llamaindex_agent\.venv`
- [環境與驗證紀錄](../.lladar/runs/20260919-132242-430141/adapter/run.json)
- [正式答案](../.lladar/live-auto-adapter-20260919/llamaindex-separated-answers.jsonl)

使用者需先依待測專案的安裝方式準備其環境。LLaDAR 不會自動把待測需求安裝進
自己的環境，也不會靜默建立一個缺少待測套件的新環境。
