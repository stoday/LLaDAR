# 作者指南：怎麼修改題目生成方式

## 先做這件事

這個目錄有兩個主要檔案：

- `SKILL.md`：模型真正執行的規則。想改「產生什麼題目」就改這裡。
- `AUTHORING.md`：本指南，說明可以改哪裡、如何驗證，以及進階工具契約。

一般只需要修改 `SKILL.md` 的「題目規則」區段，然後重新執行：

```sh
lladar create test-dataset --knowledge ./knowledge --skill ./my-skill --output ./dataset.jsonl
```

檢查 `dataset.jsonl` 與旁邊的 `.generation.json`。想比較不同規則時，使用不同輸出路徑；確定要取代既有產出時才使用 `--force`。

## 可以直接套用的規則範例

把下面的規則貼到 `SKILL.md` 的「題目規則」，或依你的領域修改：

```markdown
## 題目規則

- 每個有意義的知識段落產生一題獨立問題。
- 只詢問原文直接提到的事實，不使用外部知識。
- 答案保留數字、單位、條件、例外與否定語意。
- 問題使用自然語言，避免同一段落產生重複問題。
- 每個預期答案都必須附上支持它的原文引用。
```

常見修改方式：

| 目的 | 可以加入的規則 |
| --- | --- |
| 減少題目 | `每個段落最多產生一題。` |
| 增加覆蓋率 | `若一個段落含有不同事實，最多產生三題不重複問題。` |
| 只做事實題 | `不要產生比較、意見或需要外部知識的問題。` |
| 允許簡單推導 | `只允許由原文數字直接計算的推導，並保留使用的來源事實。` |
| 固定專有名詞 | `產品名稱、欄位名稱與領域術語必須沿用原文。` |
| 產生選擇題 | `每個選項都必須能在來源中找到支持，不可自行捏造干擾選項。` |

如果只是一般來源問答，不需要建立圖譜，也不需要寫 Python。只有在來源中有可比較的實體、關係或你確實要測試的受控變化時，才修改規則使用圖譜路徑。

## 不要改的部分

工具名稱、欄位名稱與 JSON 結構是主程式的契約。除非你正在維護相容的自訂方法，否則不要改動：

- `read_source` 的讀取方式與 `next_start` 分頁流程
- `submit_question`、`submit_qa` 的欄位名稱
- `submit_semantic_graph` 與 `submit_test_plans` 的題型欄位
- `knowledge_point_id`、`read_id` 與原文 `quote`

主程式會檢查來源位置、結構、ID、去重、題數上限與最終寫檔；它不會替你判斷自然語言是否合理，所以「題目規則」仍要寫得明確。

## 最小直接問答範例

假設來源是 `Service A keeps data for 30 days.`，讀取後的 ID 是 `read_000001`：

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

接著可以提交：

```json
{
  "knowledge_point_id": "kp_000001",
  "question": "How long does Service A keep data?",
  "expected_answer": "30 days"
}
```

自由問題優先使用 `submit_question(request_id, question, expected_answer, source_refs, answer_basis, rationale)`。使用 `submit_qa` 時，自由題只放 `knowledge_point_id`、`question`、`expected_answer`，不要加入 `id`、`actual_response` 或 `question_type`。

## 讀取與證據契約

使用 `list_sources` 和 `read_source` 讀完每個來源。字元範圍從零開始，結尾不包含在範圍內；遇到 `next_start` 就繼續讀。證據必須使用實際的 `read_id` 與逐字 `quote`。

## 何時使用圖譜

只有在圖譜能產生有意義的比較或概念映射時才使用。提交非空的 `nodes` 與 `facts`，讀回圖譜後再提交計畫。`direct_fact` 的答案是事實值與單位；`concept_mapping` 的答案必須包含完整候選集合。不要為了完成流程而提交空圖譜或虛構概念。

```json
{
  "nodes": [
    {"id": "service-a", "type": "entity", "label": "Service A",
     "origin": "source", "evidence_refs": ["kp_000001"]}
  ],
  "edges": [],
  "facts": [
    {"entity_id": "service-a", "label": "Service A", "value": "30",
     "unit": "days", "evidence_ref": "kp_000001"}
  ]
}
```

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

受控變體每一組只能改變一個已宣告的 `control_value`，並保留任務、概念、候選集合、答案契約與問題骨架。來源沒有支持的值關係不可使用。

## 題型化問題

如果請求明確要求單選、多選或排序，使用帶有 `question_type`、`options` 與正確選項 ID 的 `submit_qa` 契約；自由圖譜事實題不能代替指定題型。

| 題型 | 選項數 | 正確答案 | 協定 |
| --- | --- | --- | --- |
| `single_choice` | 2–5 | 一個 | `one_option_id` |
| `multiple_choice` | 3–6 | 多個，但不可全選 | `option_id_list` |
| `ranking` | 3–5 | 每個選項一次 | `ordered_option_ids` |

## 重試與進階交付

重試時先檢查 `validation_errors` 和 `existing_results`，修正既有結果，不重送已完成的圖譜或計畫。提交結果不確定時，用原本的 request ID 查詢；只有內容完全相同才能重用該 ID。

如果請求提供 `candidate_path` 與 `candidate_contract`，該 JSON 檔是完整且權威的候選快照，不是增量檔案；請依實際 request 中的契約交付，不要讓主程式猜測目錄裡的檔案。

## GitHub 模式

使用 `--from-github` 時，這個 Skill 還必須處理 `github_discovery` 和 `github_review`；這是進階流程，需要時再參考專案的 GitHub 資料集生成文件。

## 方法與提交契約的完整範例

以下範例保留給需要直接操作工具契約的作者。它們不是額外的執行指示；真正有效的規則仍在 `SKILL.md`。

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

```json
{
  "nodes": [
    {"id": "service-a", "type": "entity", "label": "Service A",
     "origin": "source", "evidence_refs": ["kp_000001"]}
  ],
  "edges": [],
  "facts": [
    {"entity_id": "service-a", "label": "Service A", "value": "30",
     "unit": "days", "evidence_ref": "kp_000001"}
  ]
}
```

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

```json
{
  "knowledge_point_id": "kp_000001",
  "question": "How long does Service A keep data?",
  "expected_answer": "30 days"
}
```
