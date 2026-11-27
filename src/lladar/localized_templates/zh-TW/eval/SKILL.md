---
name: eval
description: 比較 Agent 回答與預期答案，依自訂標準判定正確、錯誤或無法判定，並說明理由。
---

## 自訂需求（修改這裡）

一般只需改寫下方「目前採用的評估規則」，用平常說話的方式描述什麼算正確、什麼算錯誤。保留一組一致的規則；工具名稱、資料欄位與下方執行契約保持原樣。AUTHORING.md 提供小量試跑與進階修改方式。

### 目前採用的評估規則

意思相同就算正確，答案須保留必要條件、數字與單位。自行補充沒有證據支持的資訊算錯誤；證據不足以判定時標示無法判定並說明。

### 改寫範例（僅供參考，不自動套用）

以下是給使用者參考的替換文字，不是有效評估指示。要採用時，將選定內容改寫到「目前採用的評估規則」，並取代原有規則；未採用的例子不參與判定。

- **接受同義說法**：「不要求與預期答案逐字相同，只要意思相同，而且必要條件完整，就算正確。」例如預期答案是「平日票價一百元」，回答「週一到週五票價一百元」可接受。
- **重視條件完整性**：「日期、金額與適用條件都必須正確；漏掉會改變答案適用範圍的條件就算錯誤。」例如預期答案是「平日票價一百元」，回答「每天票價一百元」不符合。
- **限制額外補充**：「可以換句話說，但不能加入預期答案沒有依據支持的優惠或承諾。」例如預期答案只說票價，回答卻另稱「附贈免費接駁」就算錯誤。

## 執行契約（進階）

在下方工具與證據限制內執行上方自訂需求，保留工具名稱、欄位與提交規則。


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
