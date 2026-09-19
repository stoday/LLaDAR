# 最外層入口探索與人類確認：驗證紀錄

日期：2026-09-19。需求見 [PRD](PRD-lladar-interface-confirmation.md)。

## 已驗證行為

- 探索階段只提供列檔、讀檔、搜尋工具；先保存入口候選、流程與可核對的來源行。
- 多個對外功能不自動猜選。互動模式可以選擇、補充說明或保存離開。
- 非互動模式以 `needs_confirmation` 保存並回傳 exit code 3，尚未生成 adapter 或呼叫待測 agent。
- 新 process 可以用 `resume-agent RUN --candidate ID` 或 `--clarification TEXT` 續跑。
- 保存離開、無效 ID、再次非互動續跑不會使等待中的 run 失效。
- 續跑核對原始專案、保存副本與資料集雜湊，並使用排他鎖避免同一 run 同時續跑。
- 產生的 adapter 必須使用已選定的最外層介面；保留應用程式產生的檔案與紀錄。

## 真實 API 驗證

使用 `gemini:gemini-3-flash-preview` 探索；待測專案也使用真實 Gemini API、LangChain agent 與 `shop_policy` 工具，沒有 mock provider。
測試專案是 `tests/fixtures/layered_agent`，刻意提供兩個同樣有效的最外層功能：`public_input.chat` 與 `public_input.ticket`。
控制端與待測端使用不同 venv；測試沿用本機 VIDE-TESTING 範例的 target Python 與環境檔，未保存憑證內容。

可從 repository root 重跑（會產生 API 費用）：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_interface_confirmation_live.py --target-python ..\VIDE-TESTING\examples\langchain_agent\.venv\Scripts\python.exe --env-file ..\VIDE-TESTING\.env
```

候選 ID 續跑成功證據：`.lladar/live-interface-20260919-134943-316814/verification.json`。
兩次獨立 replay 加三筆正式資料均成功；三筆答案保留「客服回覆：」最終加工，執行紀錄包含初始化、知識載入、工作流、真實工具呼叫、模型回答與後處理，沒有工單輸出事件。

補充需求續跑證據：`.lladar/live-interface-confirmation/clarification-resume.log`，run 為 `.lladar/runs/20260919-134656-863864`。
補充「只測客服對話」後重新唯讀探索，收斂至 `public_input.chat`，兩次 replay 與三筆正式資料成功。
此輪生成的 adapter 刪除了 trace 檔，不能用它證明完整執行事件；因此新增明確保留應用輸出的生成規則，並另行重跑驗收。未手動修改生成的 adapter。

新增保留規則後的完整驗收亦通過：`.lladar/live-interface-20260919-135257-424230/verification.json`。
最新版驗收腳本逐一核對最後五個 request（兩次獨立 replay、三筆正式資料）的 trace，全部包含上述完整流程事件。每次 observation 同時保存執行副本位置與 adapter SHA-256，方便回查實際執行版本。

第一次探索曾回報錯誤的來源行號，驗證器在執行目標前拒絕。讀檔工具已提供行號，並加入最多三次唯讀修正機會；錯誤保留在 discovery evidence。

## 本機檢查

48 項聚焦檢查通過：入口證據與選單、真實 stdin subprocess、跨 process 狀態讀取、來源變更拒絕、續跑鎖、環境隔離、既有明確入口 runner/CLI 與資料驗證。
這些檢查不替代上面的真實 provider 驗收。

`uv build --wheel --out-dir .lladar/build-interface` 成功；已核對 wheel 包含 `interfaces.py`、`run_context.py` 與更新的 bundled skill。

## 邊界

來源行與 replay 證據提高可信度，不能形式化證明任意專案沒有隱藏的更外層流程。
`verified` 表示 adapter 獨立重播成功，不代表回答品質合格。
本版只續跑 `needs_confirmation`，不是任意失敗階段的 checkpoint。
保存的執行目錄不是 OS sandbox；強制終止可能留下 `.resume.lock`，需確認沒有存活程序後再處理。
