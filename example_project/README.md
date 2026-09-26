# 範例專案

- `diet/`：健康飲食問答 Agent、LLaDAR 測試資料與既有評估紀錄。從 `diet/` 執行 `run-lladar-eval.bat`。
- `resume_review/`：使用 `akasha.agents` 的履歷推薦平台範例。安裝與啟動方式見 `resume_review/README.md`。

兩個範例共用本資料夾的 `.env`，不複製憑證。歷史 `.lladar` 執行快照保留在 `diet/.lladar`；快照中的舊路徑僅供回溯，新的執行請使用 `diet/` 路徑。
