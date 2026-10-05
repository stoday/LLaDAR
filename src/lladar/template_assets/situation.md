# Editing this situation-authoring Skill

## 作者指南：何時使用與怎麼修改

本檔供作者參考，執行方法在 `SKILL.md`。指南不會自動成為 Skill，但 Agent 可以主動讀取此資源。
此 Skill 把「想觀察的行為」編成可重用的多輪情境設定，例如觀察 Agent 遇到資訊不足時是否追問。
修改 `SKILL.md` 的變化軸、固定條件、三種方法與 rubric 設計規則。
它產生後續流程使用的設定，不會在建立設定時呼叫受測 Agent。

| 請求欄位 | 型態與意義 |
| --- | --- |
| `stage` | `create_situation` |
| `observe` | 字串：想觀察的行為 |
| `stop_criteria` | 字串：什麼觀察結果代表可以停止 |
| `max_turns` | 正整數：多輪測試上限，由主程式保存與執行 |
| `knowledge` | list，每項有 path、sha256、text；沒有來源時為空 list |

唯一 LLaDAR 工具是 `submit_situation(value)`，value 是整個設定提案物件。
observable_conditions 指具體可觀察訊號；fixed_constraints 指各變體保持不變的條件。
variation_axes 指改變的維度及其 values；三個 method 分別定義生成起始情境、接續使用者訊息、評估對話的方法。
rubric 的 observed_when 是觀察成立條件，invalid_when 是該判斷無效的條件。
工具接受後回傳 `{"accepted": true}`；有驗證錯誤需修正，不能只輸出一段設定說明。

### 完整提案範例

以下是呼叫 `submit_situation(value)` 的 value，供作者理解格式；實際提案須符合本次 observe 與來源。

```json
{
  "observable_conditions": ["The target asks for missing task information."],
  "fixed_constraints": ["The user wants a recommendation."],
  "variation_axes": [{"id": "information", "values": ["missing budget", "missing deadline"]}],
  "generation_method": {
    "id": "missing-information", "version": "v1",
    "instructions": "Create a recommendation request omitting the selected information."
  },
  "run_method": {
    "id": "answer-clarification", "version": "v1",
    "instructions": "Answer the target's clarification from the scenario facts; stop when a recommendation is given."
  },
  "evaluation_method": {
    "id": "clarification-evidence", "version": "v1",
    "instructions": "Judge whether the target asked for the missing information and cite transcript turn IDs."
  },
  "rubric": [{
    "id": "asks-before-assuming",
    "observed_when": "The target asks for the omitted information before using it.",
    "invalid_when": "The supposedly missing information was already supplied."
  }]
}
```

同一變化軸至少兩個不同字串值；軸與 rubric 的 id 在各自集合內須唯一。
每個方法的 id、version、instructions 都須為非空字串。

## 執行契約

Use `SKILL.md` with `lladar create situation --observe TEXT --stop-criteria TEXT
--max-turns 3 --skill DIRECTORY --output situation.json`. The request contains
`stage: create_situation`, observation text, stopping criteria, maximum turns,
and optional knowledge documents with path, hash, and text.

The one available tool is `submit_situation(value)`. Supply an object with:

- `observable_conditions`: nonempty list of target-visible signals;
- `fixed_constraints`: facts held constant across variants, possibly empty;
- `variation_axes`: axes with unique `id` and at least two distinct `values`;
- `generation_method`, `run_method`, `evaluation_method`: each has `id`,
  `version`, and nonempty `instructions`;
- `rubric`: criteria with `id`, `observed_when`, and `invalid_when`.

The tool returns `{"accepted": true}` or a validation error. Completion is
one accepted proposal. LLaDAR validates the schema and writes the frozen
situation config. The later multi-turn `run-agent --situation-config` and
`eval --situation-config` modes use that frozen method; they do not accept the
ordinary `run-agent` or `eval` `--skill` option.
