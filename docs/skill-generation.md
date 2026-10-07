# Skill-based dataset generation / Skill 生成資料集

`create test-dataset` 透過隨套件提供或本地的 Akasha Skill 讀取知識段落並生成問答。
Agent 依 Skill、來源與使用者選項選用直接問答或圖譜；主程式不強制建立圖譜。
內建方法保留來源事實題及有來源支持的概念映射指引。

## 使用

需要 Python 3.11／3.12、Akasha ≥1.8 與可用模型憑證。

```powershell
lladar create test-dataset --knowledge .\knowledge --output dataset.jsonl
lladar create test-dataset --knowledge .\knowledge --skill .\my-skill --output dataset.jsonl
```

`--knowledge` 可重複指定，支援 `.md`／`.txt` 檔案與目錄。
`--output` 接受 `.jsonl` 檔案或用來建立時間戳檔名的目錄。
本地方法必須有符合 Akasha 命名規則、與目錄同名的 frontmatter name、description
及非空的 `SKILL.md` 正文。不需要額外 manifest 或方法選擇旗標。

使用 `lladar create test-dataset-skill --output my-skill` 取得可編輯範本與
`AUTHORING.md`。範本的參考例子不取代 `SKILL.md` 的有效方法。
直接問答 Skill 可要求依文章順序閱讀、保留完整知識段落、每段一組自然問答；
不必建立圖譜，也不必使用 Python。題數與知識段落的對應由方法定義。

## 可選工具與交付

一次生成工作提供來源讀取、知識點、直接／題型化問答、圖譜與測試計畫工具。
`read_source` 使用解碼後文本的零起算、尾端不包含的字元區間；分頁是傳輸限制，
不是固定切分方法。長來源依 `next_start` 續讀。

直接問答附知識段落 ID、問題、預期答案及可定位的原文引用，沒有圖譜也可成功。
圖譜方法提交實體、事實、關係與推論概念，再產生依賴圖譜的題目。
空圖譜不能通過驗證；一般直接事實圖譜可以沒有概念或關係邊。

Agent 可使用 Akasha 原生 `python_execute` 探索、計算與修正候選，包括直接調整
執行中的工作區。也可依 request 的 `candidate_contract`，直接寫入預先指定的
`candidate_path` JSON。候選檔存在時代表完整交付快照，不是與工作區合併的增量；
不掃描目錄猜測輸出。作者指南提供 reads、knowledge_points、qa、graph、plans
及 method_reason 的欄位說明。

結果不要求全部經工具提交。主程式收集並固定最終快照，再重新檢查原文位置、
問題格式及適用的圖譜／probe 契約；不能沿用早期 accepted 當成免驗證證明。
正式寫檔仍遵守輸出路徑、覆寫保護與發布復原規則。

## 圖譜 probe 與題型

概念映射觀察目標如何選擇來源支持的同位階候選，不併入一般題答對率。
受控變體預設不產生，其維度由 Agent 提出並經驗證；不內建領域或人口統計分類。

```powershell
lladar create test-dataset --knowledge .\knowledge --controlled-variant-probes
lladar create test-dataset --knowledge .\knowledge --controlled-variant-topics customer_context
```

互動選項需要終端輸入；非互動選項使用已記錄、同一語料指紋的維度 ID。
明確要求受控變體時，需要有效圖譜與可比較配對，不能悄悄改成一般問答。
每對只改一個已宣告控制值，保留問題骨架、來源候選與答案契約。

`--question-type` 契約不變。明確指定單選、複選或排序時，直接事實題須用 typed QA
契約提交；來源必須支持選項與比較關係，不能用自由回答題充數。

## Count、重試與狀態

- `--count 0` 保留所有去重後候選；正整數是全域上限。
- 去重正規化空白後精確比較問題與答案；配對題完整保留或完整略過。
- 同段可有多題；重複交付不重複增加題數。來源耗盡與略過原因保存於紀錄。
- 每次生成最多三次嘗試；每次最多 40 次 host 工具呼叫、30 個 Agent 回合。
- request 的 existing_results 與 validation_errors 協助修正未完成資料。
- Agent 交付後才發生回合上限等錯誤時，若最終快照有效，保留產物及錯誤紀錄，
  不要求再次提交。初始化、Skill 載入失敗仍不能假裝成功。
- 完成要求讀完來源並交付有效題目；早期拒絕保存於審核紀錄，修正後的最終快照重新驗證。最終驗證失敗不發布正式產物。
- `--seed` 保存選取設定，不保證模型每次產生相同內容。

## 輸出與下游

正式 JSONL 每行只有 `question`、`expected_answer`、`actual_response: null`。

- `<output>.generation.json` 保持 v5 契約，新增逐題 generation_method、最終驗證、
  方法理由與略過紀錄。保存來源、引用、執行錯誤及 dataset 雜湊。
- `<output>.graph.json` 只在採用有效圖譜時產生。直接問答不產生占位空圖譜。
- `--force` 用直接問答取代原有圖譜資料集時，一併移除該輸出的舊圖譜；發布失敗時復原。
- run-agent 保留題型／probe metadata 的驗證與傳遞。
- eval 不直接讀完整圖譜：一般問答比較問答與實際回答，圖譜 probe 使用 run sidecar
  的候選與配對。report 繼續讀取 eval 結果，不需要原始圖譜。
- 同次生成可混用直接與圖譜方法。經圖譜生成的直接事實題仍可走一般答案評估。

`--log PATH` 保存實際進度、trace、摘要與錯誤。完成摘要顯示 methods、graph_used
與 validation。Sidecar 含原文與本機路徑，按原文的存取方式處理。

Python 保留目前執行方式，本案不新增隔離。來源文本是資料，不是指令。
驗證可檢查結構、ID、精確引文與來源位置，不能自動證明自然語言語意正確或
全文事實抽取完整；數值、條件與答案是否受原文支持仍須檢閱。

## 驗證與重跑

```powershell
uv run --extra test python -m pytest tests/test_agent_selected_generation.py tests/test_generation_skills.py tests/test_controlled_variant_dataset.py tests/test_semantic_graph_probes.py tests/test_skill_agent.py
uv run python scripts/verify_skill_generation_live.py --output .lladar/live/dataset.jsonl
```

真實模型驗收使用虛構來源，讀取 `.env` 而不列印憑證。可用 `--skill` 驗收本地方法。
同一路徑重跑需要 `--force`；自動檢查不能取代人工語意驗收。
早期 [Skill-first 驗收紀錄](skill-first-acceptance/README.md) 是當時流程的歷史證據，
本次可選圖譜重構的離線與真實模型結果另見[驗收紀錄](agent-selected-generation-acceptance.md)。
