# Editing this test-dataset Skill

## 作者指南：用途、修改與工具選擇

本檔供作者參考；以下範例不是要在每次執行全部照做。實際規則在 `SKILL.md`。
指南不會自動成為另一個 Skill，但 Agent 可以把它當資源讀取。
修改 `SKILL.md` 中對應階段的步驟，例如把問題改成客服語氣、強調條件與例外，
或選擇來源能支持的測試類型。引用、驗證、ID 與檔案寫入仍由主程式處理。

### 哪個情境使用哪個工具

| 階段 | 情境 | 完整 LLaDAR 工具清單 |
| --- | --- | --- |
| `knowledge_points` | 從原始文件找可獨立回答的事實 | `list_sources`, `read_source`, `submit_knowledge_points` |
| `semantic_graph` | 把已接受事實組成實體、屬性及概念 | `list_knowledge_points`, `submit_semantic_graph` |
| `test_plans` | 從已驗證圖產生直接問題、概念問題或受控對照 | `read_semantic_graph`, `submit_test_plans` |
| `qa` | 主程式指派一個知識點生成候選題時 | `read_knowledge_point`, `list_knowledge_points`, `submit_qa` |

`stage` 由主程式決定。作者不能透過 Skill 自行切換階段或取用下一階段的工具。
`source_id` 是來源識別碼、`read_id` 是一次讀取的識別碼、`knowledge_point_id` 是已接受事實的識別碼。
都使用工具或請求傳回的真實值；不能從範例複製 ID 到真實資料。

### 最小提交範例：知識點 → 圖 → 直接事實題

以下各區塊是工具參數的 JSON 值；每段只能在自己的階段提交。
假設來源文字是 `Service A keeps data for 30 days.`。
讀取指定來源後，使用回傳的 `read_id` 呼叫 `submit_knowledge_points(points)`：

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

工具回傳 `accepted`（ID list）及 `rejected`（包含 index、error 的 list）。
修正被拒絕項目；讀完所有範圍且沒有未解決拒絕才完成。
`read_source` 的位置是從零開始的字元位置，不是行號；長來源會分頁，沿 `next_start` 讀到 null。

圖階段呼叫 `list_knowledge_points()` 取得物件 list（包含 id、statement、topic、evidence）。
假設工具已接受 `kp_000001`，呼叫 `submit_semantic_graph(graph)`：

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

節點 ID 可自行定義，證據 ID 必須來自已接受的知識點。
節點 type 可為 `entity`、`attribute`、`concept`；concept 還必須有至少兩個不同 entity 的 `member_ids`。
origin 可為 `source` 或 `inferred`，推導概念仍須有來源證據。
每個 entity 至多一個可比較 fact；`unit` 可為空字串。
若需要關係，edge 物件完整形狀是
`{"from":"node-a","relation":"belongs-to","to":"node-b","origin":"source","evidence_refs":["kp_000001"]}`，
兩端都必須是實際提交的 node ID，關係與引用須受證據支持。
成功回傳 `accepted: true`、nodes 與 facts 數量。

計畫階段先 `read_semantic_graph()` 取得含 nodes、edges、facts、evidence 的已驗證圖，
再呼叫 `submit_test_plans(plans)`：

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

`expected_answer` 必須等於圖 fact 的 value 與 unit 組合。提交成功後主程式產生資料集。
概念題的完整欄位是 `type: concept_mapping`、`concept_id`、`question`、`expected_answer`；
預期答案是該概念所有成員的完整來源候選集合，不能只挑一項。

### 受控對照何時適用

只有來源規則未限定相關條件時，才可採用 `controlled_invariance` 比較改變一個條件後的回答。
一組至少兩個 plans，完整欄位為 `type`、`pair_id`、`source_concept`、
`source_support: group_unspecified`、`answer_contract: invariant`、`varied_dimension`、
`control_value`、`question_template`、`question`、`expected_answer`。
`varied_dimension` 是物件：`id`、`label`、`semantic_scope`、`mutual_exclusivity` 均為非空字串，
`coexists_with` 是 dimension ID 的字串 list；`mutual_exclusivity` 不能是 `unknown`。
每個問題包含自己的 control_value，question_template 必須與 question 相同；
把各題的 control_value 替成同一佔位符後，問題骨架須相同。
同組 source_concept、維度與完整候選答案須一致，control_value 必須各異。
這是進階對照方法；一般直接問答可先使用上面的 direct_fact 範例。

### `qa` 的最小工具參數

`read_knowledge_point(knowledge_point_id)` 回傳該指派知識點的完整物件，
`list_knowledge_points()` 可用來比較來源事實；`submit_qa(record)` 的 free 回答範例：

```json
{"knowledge_point_id": "kp_000001", "question": "How long does Service A keep data?", "expected_answer": "30 days"}
```

此 ID 必須是這次指派的知識點。型別化回答另有對應 question type 契約，不能直接套用 free 範例。

## 階段執行契約

`SKILL.md` contains the active method. Run it with
`lladar create test-dataset --knowledge KNOWLEDGE --skill DIRECTORY --output DATASET.jsonl`.
The host sends a request with a `stage` for each work item. Tools expire after
that work item; use only those available in the current stage.

## `knowledge_points` stage

The request names one `source_id` and reports unread source ranges. Available
tools:

- `list_sources()` returns each source ID, name, and text length.
- `read_source(source_id, start_char=0, end_char=None)` returns source text,
  `read_id`, character range, and `next_start`. Follow `next_start` to cover
  the source.
- `submit_knowledge_points(points)` takes a list of objects with `statement`,
  `topic`, and `evidence: [{read_id, quote}]`. Each quote must appear in a read
  page. It returns accepted IDs and rejected indexes with reasons. Correct
  rejected points before finishing.

Completion: all source ranges were read and no rejection remains unresolved.

## `semantic_graph` stage

- `list_knowledge_points()` returns accepted fact IDs and statements.
- `submit_semantic_graph(graph)` accepts one object with `nodes`, `edges`, and
  `facts`. Every source item must cite accepted fact evidence. It returns the
  accepted graph summary or a validation error.

Completion: one evidence-backed graph was accepted.

## `test_plans` stage

- `read_semantic_graph()` returns the verified graph for this work item.
- `submit_test_plans(plans)` accepts a nonempty list of `direct_fact`,
  `concept_mapping`, or `controlled_invariance` plans. A direct fact plan has
  `type`, `entity_id`, `question`, and `expected_answer`; the answer must match
  the graph fact. The tool returns accepted and pair counts or a validation
  error. The selected Skill describes the additional concept and pair rules.

Completion: the host accepts the plans. The host assigns IDs, validates
evidence and paired variations, and writes the dataset and provenance.

## `qa` stage

When the host requests question candidates, use `read_knowledge_point(id)` for
the assigned point, `list_knowledge_points()` for comparison context, and
`submit_qa(record)` with `knowledge_point_id`, `question`, and
`expected_answer`. Typed answer protocols require additional fields described
by the selected question type. The host validates each candidate and returns
acceptance or an error.

The Skill controls how source facts become questions. LLaDAR controls source
evidence, graph validation, question contracts, deduplication, record writing,
and the requested count. Test changes on a small knowledge file first.
