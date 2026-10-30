---
name: eval
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 答案判定

## 這個 Skill 做什麼、要改哪裡

它定義什麼叫做「回答正確」。要客製化時，修改判定規則、等價說法、必要條件或額外維度；
`AUTHORING.md` 說明修改流程與提交格式。彙總統計由主程式負責，不要在 Skill 中自行計算。

stage=plan 時以 submit_plan 提交布林 correct 維度的計畫。stage=judgment 時使用凍結計畫，僅比較 expected_answer 與 actual_response，以 submit_judgment 提交 values 與非空 reason。受支持時 correct=true，不相符時 false；證據無法判定時使用 null 並解釋。不計算彙總統計。

確認提交 status；格式被拒絕時，使用 revise_plan／revise_judgment，傳入 item_id、新 request_id、目前 revision 與完整修正內容，最多修正三次。使用 get_plan／get_judgment 查詢不確定結果。已接受的計畫不得修改。使用指定 trial_id 與 plan_version，各項 request_id 有自己的範圍。actual_response=null 且有 response_read 時代表長回答，先用 read_trial_response(trial_id, start_char) 跟隨 next_start 讀完整內容，不能當成執行失敗。

自訂 Skill 也以自身凍結計畫評估 typed 與 semantic-probe 回答。提交被接受才完成。

stage=situation_judgment 時，本答案正確性方法沒有情境標準。依 judgment_contract 提交 validity=indeterminate、behavior=indeterminate、evidence_turn_ids=[]，reason 解釋須在自訂 Skill 定義完整情境標準。沒有 plan 工具。若自行補上標準，必須引用實際輪次 ID 並保留生成限制。

```json
{
  "title": "Answer correctness",
  "approach": "Accept equivalent wording while requiring all answer conditions.",
  "dimensions": [
    {"name": "correct", "description": "Matches the expected answer including required conditions.", "kind": "boolean"}
  ],
  "limitations": ["Only the supplied question, expected answer and actual response are evidence."]
}
```
