# 從 GitHub 資料集生成問答

```powershell
lladar create test-dataset --from-github https://github.com/hsinmosyi/TWBias --count 20 --output dataset.jsonl
```

提供一個公開 repo 根網址，取代 `--knowledge`。CLI 將預設分支固定為 commit；Agent 探索
文件與檔案、檢查實際資料，選擇檔案或指定資料列。支援 UTF-8 的 Markdown、TXT、CSV、
JSON、JSONL，以及文件明確連結的公開 HTTP(S) 直接文字資料檔。外部資料以內容雜湊與
取得時間固定，不宣稱受 repo commit 固定。repo 程式不會被執行。

內建 `github-dataset` Skill 包含 `github_discovery`、`generation`、`github_review` 三個階段。
自訂 `--skill DIRECTORY` 須處理這三個階段。探索工具為 `list_repository_files`、
`read_repository_file`、`read_dataset_rows`、`read_external_dataset` 與 `select_dataset`。
選取證據須引用已讀來源的原文。`row_selections` 可將選取路徑對應到原始 1-based 資料列號，
列號不含表頭。

生成階段沿用來源、知識點、QA 與可選圖譜工具。每道正式題目還須呼叫
`record_answer_basis(question, expected_answer, answer_basis, rationale, evidence_point_ids)`。
依據類型為 `source_answer`（原答案）、`source_annotation`（原標註）或
`derived_criterion`（生成判準），引用已接受的知識點 ID，必要時包含標註定義。
生成判準不能冒充原始標準答案，也不能把偏見樣本當作群體事實。保留 Python 候選交付能力，
最終結果仍須通過相同驗證。

另一次模型呼叫依固定問答與來源證據檢查語意，透過 `submit_generation_review` 提交每題指紋、
布林通過結果與理由。未通過的題目排除並記錄。模型檢查不代表人工檢閱或原作者認可。

GitHub 模式會共同保存：

- `dataset.jsonl`：既有三欄問答。
- `dataset.jsonl.generation.json`：設定、方法、證據與模型檢查。
- `dataset.generation-report.md`：所有正式題目的原文摘錄、穩定引用、轉換、答案依據與限制。
- `dataset.sources.json`：下載原文、雜湊、正規化資料、原始列號及外部資料取得資訊。
- `dataset.jsonl.graph.json`：僅在使用有效圖譜時產生。

執行受測 Agent 前，先開查驗報告檢閱；相對連結可查到保存的證據。報告發布失敗不能算成功，
覆寫失敗會還原先前產物。不加 `--force` 時，既有報告及證據檔在下載與模型呼叫前受到保護。

每次執行的下載限制：單一回應 2 MiB、總量 20 MiB、100 次 HTTP 請求、20 秒網路逾時、
每次下載最多 5 次重新導向、repo 最多 2,000 個檔案。本機、私有及保留網路目標與導向均拒絕。
另外仍受工具與模型限制。`--count` 是最終題數上限，不是下載列數；可因驗證而少於指定題數，
`0` 不取消資源限制。跳過與未處理資料不宣稱已覆蓋。私有 repo、登入或付費頁面、壓縮包、
LFS 物件、子模組及二進位格式暫不支援。Python API 在 GitHub 模式須提供 `output`，
以保存必要報告與證據。

選取後的正規化文字上限為 24,000 字元，並受模型輸入 token 上限的一半約束。大型資料表必須明確採樣；每個原生 Agent 階段最多 160 次受控工具呼叫、80 輪模型回合。限制與採樣範圍會保存在證據中。失敗時另存不覆寫既有檔案的 `*.generation-failure.json` 診斷，供查驗失敗階段，不發布成功資料集。
