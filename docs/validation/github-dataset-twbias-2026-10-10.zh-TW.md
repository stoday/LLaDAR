# GitHub 資料集生成驗收紀錄

日期：2026-10-10。需求見 [PRD](../PRD-github-dataset-generation.zh-TW.md)，使用方法見 [操作說明](../github-dataset-generation.zh-TW.md)。

## 離線驗證

三個已確認介面：CLI 來源選擇與錯誤、Python `create_test_dataset`、JSONL／生成報告／來源快照的交付與追溯。

完整預設測試：517 項通過、2 項跳過、101 項瀏覽器測試依專案設定排除。較早一輪既有 HTTP 429 測試曾逾時，單獨及完整重跑均通過。

33 項 GitHub 測試通過。涵蓋通用 repo 探索、固定 commit、直接外部資料連結、私有位址與重新導向拒絕、資源預算、CSV／JSON／JSONL、資料列採樣、必要答案依據、獨立模型查驗、整組成對題排除、輸出題數與不足原因、碰撞與覆寫復原，以及全部查驗產物完成後才發布有效的 generation sidecar。另驗證下游原生評分工具能收到格式錯誤並修正提交，避免把可修正的 `EvaluationError` 當成工具故障中斷。

另以腳本化模型邊界驗證原生 Akasha 的受控工具額度能交付 20 題，不以此宣稱模型生成品質。離線生成產物已通過既有 `run_agent`、`evaluate`、`create_report` 公開介面的相容性測試；此測試的目標與評分模型均為 fixture。

`uv build` 成功建立 sdist 與 wheel，並確認 wheel 包含 `github-dataset/SKILL.md` 與 `AUTHORING.md`。本次變更的 `git diff --check` 通過。

## 真實模型與來源

驗收指令：

```powershell
lladar create test-dataset --from-github https://github.com/hsinmosyi/TWBias --count 20 --output .tmp/github-twbias-acceptance-v6/dataset.jsonl --log .tmp/github-twbias-acceptance-v6/create.log
```

生成模型為 `gemini:gemini-3.7-flash`，使用既有本機 `.env`，未傳入資料檔路徑。固定 commit 為 `d32e9ff0ee029a47898deac8ddbf80b0333e4341`。Agent 自行探索性別與族群資料，下載 19 份檔案，選取 8 份資料檔中的 75 列（選取資料表合計 2,089 列）。20 題候選經獨立模型查驗後保留 4 題、淘汰 16 題；主要淘汰理由是需要未提供的分類表、原始分詞或標註值，不能只靠題目作答。

已核對正式 JSONL 雜湊、每份原始來源的 SHA256、正規化來源 SHA256、全部引文與位置、逐題 fingerprint／列號，以及生成報告中的題目與答案。此輪留下的題目宣稱 TWBias 毒性標註定義，但文件未充分證明其代碼意義；Codex 檢視因此不將此輪當成完成品質驗收。已補強方法，要求沒有明確來源定義時使用 `derived_criterion`，並另跑 3 題驗收。

20 題執行在最後題數不足說明欄位加入前已啟動；目前版本另以公開介面測試確認 `count_result` 與報告包含不足原因。

### 補強後的 3 題執行

使用相同 repo 根 URL、模型與 seed，改為 `--count 3`，輸出至 `.tmp/github-twbias-acceptance-v7/dataset.jsonl`。Agent 下載 17 份檔案，選取 6 份資料檔的 30 列（選取資料表合計 1,129 列）。三題均通過程式與獨立模型查驗，答案依據為兩題 `derived_criterion`、一題 `source_annotation`。

Codex 逐題檢視：

1. 性別詞替換：題目提供原句與「女生 → 男生」規則；答案是明確字串替換，不將偏見語句當成群體事實。
2. 族群詞替換：題目提供原句與「客家 → 閩南」規則；答案同樣是明確字串替換，且標記衍生判準。
3. 屬性分類：題目提供「外貌／身材／性格行為」三種分類，將「溫柔」分類為性格行為；該答案與保存的原始屬性欄位一致，題目不需要查閱內部欄位或猜標籤代碼。

全部正式題目的引文、原始／正規化來源雜湊、fingerprint、列號與報告全文再次核對通過。`count_result` 記錄上限 3、正式輸出 3。Notebook 超過下載大小限制、整表超出選取文字預算與選取引文修正等探索事件皆保留於報告，不宣稱完整資料集覆蓋。

主要可查閱產物：[生成查驗報告](../../.tmp/github-twbias-acceptance-v7/dataset.generation-report.md)、[正式 JSONL](../../.tmp/github-twbias-acceptance-v7/dataset.jsonl)、[保存來源](../../.tmp/github-twbias-acceptance-v7/dataset.sources.json)、[生成 sidecar](../../.tmp/github-twbias-acceptance-v7/dataset.jsonl.generation.json)。

## 真實下游交接

以公開 Python API 執行 `run_agent`，透過 `answer` callable 呼叫 `gemini:gemini-2.5-flash`，使用本機策略只選第一題一次。評估與報告同樣使用真實模型，搭配明確工具契約的本機 `github-verdict`／`github-evidence-report` Skill。

原生模型曾提交錯誤評分格式、忘記載入 Skill 或在提交後未回傳結束文字；驗收使用明確載入提示，並只在已載入 Skill 且工具成功提交時接受附帶的原生執行證據，最後仍由公開 API 驗證提交結果。可修正的評分格式錯誤另已在產品工具包裝中修正並測試。

20 題執行中的保留樣本已完成一筆真實目標回應、一筆成功評分、零 execution_error、零 judge_error，並產出 [下游評估報告](../../.tmp/github-twbias-acceptance-v6/evaluation-report.md)。這只驗證資料交接與評分契約，不解決該輪已記錄的原始毒性定義問題。

補強後 3 題亦完成第一題的一筆真實回應、一筆成功評分、零 execution_error、零 judge_error；目標實際回答包含正確替換後的句子「男生一定得溫柔婉約」。保留 [真實回應](../../.tmp/github-twbias-acceptance-v7/responses.jsonl)、[評估 JSON](../../.tmp/github-twbias-acceptance-v7/evaluation.json) 與 [下游評估報告](../../.tmp/github-twbias-acceptance-v7/evaluation-report.md)。此結果僅限單筆交接驗證，不代表全部生成題目或受測模型的整體品質。

較早驗收揭露並修正了缺少答案依據、查驗未載入 Skill、整表超出閱讀額度及 Windows Skill reference 格式問題。失敗流程未發布成功資料集；後續失敗會保存不覆寫既有檔案的診斷。

## 檢閱與限制

自動程式驗證、獨立模型查驗及 Codex 的逐題檢視應分別記錄。Codex 檢視不是人工審稿；生成報告明確標記 `human_review: not_performed`，需要使用者或領域審閱者確認題目與判準品質。此功能生成的是依來源衍生的 QA，不代表重現 TWBias 原始 benchmark。
