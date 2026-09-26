# Skill-first create MVP 驗收紀錄

日期：2026-09-23。範圍依 [核准 PRD](../PRD-skill-first-create-mvp.zh-TW.md)，
限單一本地 `knowledge-point-qa` create skill；不含安裝管理及其他三階段。

## 結果

- Python 3.12.13：完整離線測試 **134 passed, 5 skipped**。
- Python 3.11.15：完整離線測試 **134 passed, 5 skipped**。
- 5 個略過皆為既有 graph／REST 整合測試：1 個缺 graphifyy、4 個缺 Node。
  本期 skill 測試未略過。Akasha 的 langchain-experimental 棄用警告不影響通過。
- Akasha 1.8.3 + `gemini:gemini-2.5-flash`：兩次真實呼叫都完成，
  各產生 3 個知識點、3 筆 QA，所有來源讀完、無失敗。
- 使用者已在對話中確認三組答案符合預期，作為人工語意驗收。
- 後續失敗證據、YAML 錯誤與成對發布競態的補強，已納入上述最終離線回歸。
- `uv lock --check` 通過；範例 SKILL.md 格式檢查通過。
- 由乾淨 sdist 建置 wheel 並實際匯入、解析 skill CLI 通過；僅驗證本機產物，未發布。

真實執行使用 [固定虛構文章](../../tests/fixtures/skill_generation/knowledge.md)，
沒有對外送出專案私有知識。兩次使用同一份
[SKILL.md](../../src/lladar/skill_assets/knowledge-point-qa/SKILL.md)（此連結為現行版本；驗收時從專案根目錄載入的版本雜湊記於下方，後續已移入套件並移除 guidance 指示）。
憑證只由本機 `.env` 供應，未保存於本目錄。

## 人工語意核對

使用者確認：「可以，這三組符合預期」。確認內容為入門方案 30 天、企業方案 60 天、
客服週一至週五 09:00–18:00，問句分別詢問這三項資訊且答案保留完整條件。

| 問題 | 首次實際答案 | 核對 |
| --- | --- | --- |
| 入門方案的資料保留期限是多久？ | 入門方案的資料保留期限為 30 天。 | 與原文一致 |
| 客服服務時段為何？ | 客服服務時段為週一至週五 09:00–18:00。 | 星期與時間條件完整 |
| 企業方案的資料保留期限是多久？ | 企業方案的資料保留期限為 60 天。 | 與原文一致 |

第二次重跑的企業方案答案簡化為「60 天」，其餘兩組相同；已由助理逐筆對照原文，
問句仍明確限定企業方案，沒有新增原文以外的事實。
此個案核對不代表所有未來資料的語意品質皆有自動保證。

## 保存的證據

- [accepted.jsonl](accepted.jsonl)：使用者確認的第一次結果。
- [accepted.jsonl.generation.json](accepted.jsonl.generation.json)：第一次執行證據。
- [final.jsonl](final.jsonl)：加入容量、原生工具審計等限制後的第二次重跑。
- [final.jsonl.generation.json](final.jsonl.generation.json)：第二次執行證據，含 native
  `load_skill`、有效工具清單、工具呼叫及 host 提交紀錄。

Sidecar 的本機 repository 絕對路徑以 `<REPOSITORY>` 取代；其餘資料、問題、答案、
引用位置和內容雜湊未修改。Dataset 檔案本身保持原始位元組內容：

| 檔案 | SHA-256 |
| --- | --- |
| accepted.jsonl | `29cf65b39866a5809541dcd39754836133be5dab31b282c45effe483018ef7d0` |
| final.jsonl | `e4b45c7cea0563744b3c57e662f13cdf2989e7727d45109aa766cfe2ae4cc047` |
| SKILL.md | `56753d5b30d721503176458a38df7d1b6bb4bc47b2652aef7ebf0a42f51b1fca` |
| knowledge.md | `f2de413969ebc1c58b3f9ef2a24674dd38149db4db8c01dd2d65eafc711c5737` |

第二次執行的 4 個 work items 依序為一次抽取、三次 QA；每個 item 都先原生載入
`knowledge-point-qa`，使用 3 次工具呼叫。有效工具只包含所屬階段工具、
`load_skill` 和 `read_skill_resource`；不含 `python_execute`。
每個 item 的限制為 40 次工具呼叫、30 回合，輸入 32000／輸出 4096 token 設定。

## PRD 對照

主要契約測試：[test_generation_skills.py](../../tests/test_generation_skills.py)。
原生 Akasha 測試：[test_skill_agent.py](../../tests/test_skill_agent.py)。

| 條件 | 可核對證據 |
| --- | --- |
| AC1 基本流程相容、移除三旗標 | `test_create_rejects_retired_chunking_options`、既有 `test_simple_pipeline.py`；CLI help 已核對；中英文 README 已同步 |
| AC2 本地載入、缺入口與重複選項 | `test_native_skill_loads_instructions_and_only_controlled_tools`、`test_missing_skill_entry_is_reported_without_model_work`、CLI 單一 skill 測試 |
| AC3 原生指引與分階段執行 | native model 測試驗證載入後指引進入模型；final sidecar 的 extraction → QA 執行順序、載入 hash 與工具事件 |
| AC4 三點三題且可定位 | `test_local_skill_generates_one_question_per_knowledge_point`、已保存的真實三點三題結果及原文 offsets |
| AC5 拒絕假引用、未知 ID、額外欄位與越權 | `test_tool_contract_rejects_wrong_ids_ranges_and_extra_fields`、修正拒絕候選、工具過期、不可變知識點、原生 Python 拒絕及 resource 越界測試 |
| AC6 單點失敗可部分完成 | 三次重試與 failed point 測試、完全已讀但拒絕未修正來源測試、CLI partial 訊息測試 |
| AC7 去重與 count 的全部關聯 | count 0／1／2、重讀點去重及全部引文位置測試；候選統計與 `qa_ids`／`knowledge_point_ids` 行號映射測試 |
| AC8 舊格式與 runner | `test_saved_dataset_has_traceable_sidecar_and_runs_with_existing_runner` 真正呼叫 `read_records` 和 runner answer callback |
| AC9 輸出安全 | 既有任一輸出預檢、force 成功、序列化失敗、第二檔發布失敗復原、新建檔案競態保護測試 |
| AC10 真實模型與人工核對 | 本文件保存的兩次真實 Akasha 執行及使用者確認，非離線替身替代 |
| AC11 方法自主閱讀、分頁及 partial | 分頁續讀、跨讀取範圍 evidence、未讀區間 partial 測試；`api.py` skill 分支在建立 simple provider／呼叫切分器前返回 |

額外驗證包括 YAML／metadata 錯誤、skill 執行中內容變動、模型容量及全工具呼叫上限、
失敗回合證據、provider 錯誤脫敏，以及三欄輸出不含評分欄位。

## 重跑

```bash
pytest
python scripts/verify_skill_generation_live.py --output /tmp/lladar-skill-live-new/dataset.jsonl
```

真實驗收需要憑證並會呼叫付費模型；不屬於例行離線 CI。
詳見 [使用與限制](../skill-generation.md)。

先前兩份 PRD 保留原文，驗證雜湊為：

- graph PRD：`36c64cea87cd2352e9a98e23cdb34e4cfb1027b844d0bd98dda381eb657bd701`
- knowledge-point-qa PRD：`7683330a0cbcac6c02375b65dc1dee87195feb5a5b5acb9603d02bb4ec61d8d9`
