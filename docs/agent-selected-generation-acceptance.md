# Agent 選用生成工具：驗收紀錄

日期：2026-10-07。規格：[PRD](PRD-agent-selected-dataset-generation.zh-TW.md)。

## 實作結果

`create_test_dataset()` 與 `lladar create test-dataset` 保留原有介面。
主程式提供同一次生成工作的來源、知識點、問答、圖譜與計畫工具，
由 Agent 依 Skill 選用方法，不再強制執行圖譜階段。
直接問答、圖譜問答及混合生成都有逐題方法與來源紀錄。
Python 可以交付候選檔或修改工作區；發布前重建快照並驗證來源位置、
題型及適用的圖譜／probe 契約。有效結果可在 Agent 遞迴錯誤後保留。
空圖譜與失效引用會被拒絕；覆寫為直接問答時也會移除舊圖譜檔。

generation sidecar 保留 v5，下游 eval/report 沒有新增讀取完整圖譜的要求。
既有概念映射及受控配對資訊仍透過 run snapshot 傳遞。
更新了內建 Skill、作者指南、CLI help、雙語 README 及網站說明。

## TDD 與離線回歸

使用已確認的公開介面：生成 API／CLI，以及 run-agent → eval → report。
外部 Agent fixture 控制提交內容，來源驗證及輸出讀寫使用真實程式。

新增 21 項測試通過，涵蓋直接問答、Python 檔案與原生 Python 工具交付、
最終工作區驗證、空圖譜、遞迴錯誤復原、多題共用段落、配對去重、
覆寫保護、明確受控選項、三種 typed QA、下游流程與可修正的工具回饋。
各行為先確認失敗，再加入實作；schema 回饋改善也各有 red → green 紀錄。

聚焦生成／圖譜／原生 Skill 測試通過 44 項；範本及題型測試通過 34 項。
最後回歸排除已知 deadline 測試後為 **350 passed、2 skipped、51 deselected**。
其中 50 項 browser 測試由專案預設 marker 排除，另 1 項為以下 deadline 測試。
`git diff --check` 通過。

```powershell
uv run --frozen --extra test python -m pytest tests/test_agent_selected_generation.py
uv run --frozen --extra test python -m pytest
uv run --frozen --extra test python -m pytest -k 'not test_deadline_kills_dripping_worker_and_does_not_retry'
```

已知限制：完整套件中的既有
`test_deadline_kills_dripping_worker_and_does_not_retry` 失敗，單獨重跑亦失敗。
其 2 秒期限內尚未觀測到 worker 對本地 provider 發出請求，斷言收到
0 次請求而非 1 次。本案未修改 extraction provider 或該測試，
也未放寬期限。不能將本次驗收描述成完整測試全部通過。

## 真實模型產物

使用 `gemini:gemini-2.5-flash` 與既有本地憑證；只送出
`tests/fixtures/skill_generation/knowledge.md` 的虛構服務說明。
沒有變更正式資料集生成預設模型。

| 方法 | 題數 | 最終驗證 | 圖譜檔 | 成功執行次數 |
| --- | --- | --- | --- | --- |
| direct-live | 3 | passed | 無 | 1 |
| graph-live | 3 | passed | 有，3 個實體與 3 筆事實 | 1 |

這裡的執行次數是成功產物的 generation sidecar 紀錄；此前診斷執行曾失敗。
初次直接問答加入多餘欄位，圖譜提交則未滿足實體事實契約。
已補明確的工具 schema 說明及驗證回饋，重新執行後成功。

實際人工檢查結果：兩種方法都正確涵蓋入門方案 30 天、企業方案 60 天，
以及客服週一至週五 09:00–18:00，沒有遺漏客服日期條件。
直接方法的事件沒有圖譜工具；圖譜方法包含 submit_semantic_graph、
read_semantic_graph、submit_test_plans。兩者都核對逐字引用、來源字元位置、
dataset SHA-256、逐題生成方法及最終驗證狀態。

本機產物保留在 Git 忽略的
`.lladar/optional-graph-live-20261007-153251/`：
`direct-v2.jsonl`、`graph-v2.jsonl`、對應 generation sidecar、graph sidecar，
以及 direct／graph 的 responses、evaluation、report。
真實模型可透過 `scripts/verify_skill_generation_live.py --skill DIRECTORY --output FILE`
重跑；輸出檔必須是新檔或明確指定 `--force`。

兩份真實模型生成的資料都已走過公開 run-agent → eval → report。
下游 target、strategy、evaluation 與 report 使用離線 fixture，逐題狀態為 evaluated。
這驗證了資料相容性，未聲稱測量真實目標 Agent 的回答品質。
離線測試另外包含概念映射／受控配對，確認 probe 保留其專用評測方式。

來源位置與結構驗證不能證明任意自然語言答案的語義正確性；
本次真實模型產物另有以上人工來源核對。
