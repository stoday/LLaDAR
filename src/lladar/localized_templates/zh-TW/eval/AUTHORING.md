# 作者指南

## 一般使用者從這裡開始

開啟 `SKILL.md`，只改「自訂需求（修改這裡）」到「執行契約（進階）」之間的內容。範本已有可直接使用的預設需求；用平常說話的方式描述想要的結果、必要條件，必要時附上符合與不符合期待的例子。一般修改不需要寫 Python 或 JSON。

可整段替換的例子：「意思相同就算正確，但日期、金額與適用條件必須完整，不能補充沒有依據的資訊。」

如何說清楚差異：預期答案是「平日票價一百元」；「週一到週五一百元」符合，「每天一百元」不符合。

請替換原有需求，避免同時保留互相矛盾的規則。保留執行契約、工具名稱與欄位。下方進階例子只供參考，僅修改 AUTHORING.md 不會改變有效需求。

### 先做小量試跑

先用少量來源或小型資料集，使用新的輸出路徑，比對實際結果是否符合上面的正反例。執行階段請檢查 run sidecar 的選題與次數；評估與報告可重用已保存的回答或評估資料。建立範本不呼叫模型；實際執行階段可能呼叫模型與受測目標。自然語言需求是行為指引，不能取代主程式驗證或保證固定題數與覆蓋率。

## 進階參考


## 從這裡開始

修改 `SKILL.md` 的判定規則，決定如何處理等價說法、必要條件或額外評估維度。完成後執行：

```sh
lladar eval RESPONSES.jsonl --skill ./my-eval --output evaluation.json
```

比較保存的評估結果；彙總統計由主程式計算。

有效執行指示位於 SKILL.md。本指南是參考，不會註冊為另一個 Skill。替代範例須由作者取代原規則後才採用；僅修改本指南不會改變執行方法。工具名稱、參數欄位與程式碼契約保持原值。

依下方有效方法與完整範例修改選擇、評估或敘述規則。主程式持續負責資料驗證、檔案、受測 Agent 請求與確定性統計；修改 Skill 不會改變這些界線。

## 方法與提交契約

# 答案判定

stage=plan 時以 submit_plan 提交布林 correct 維度的計畫。stage=judgment 時使用凍結計畫，僅比較 expected_answer 與 actual_response，以 submit_judgment 提交 values 與非空 reason。受支持時 correct=true，不相符時 false；證據無法判定時使用 null 並解釋。不計算彙總統計。

確認提交 status；格式被拒絕時，使用 revise_plan／revise_judgment，傳入 item_id、新 request_id、目前 revision 與完整修正內容，最多修正三次。使用 get_plan／get_judgment 查詢不確定結果。已接受的計畫不得修改。使用指定 trial_id 與 plan_version，各項 request_id 有自己的範圍。actual_response=null 且有 response_read 時代表長回答，先用 read_trial_response(trial_id, start_char) 跟隨 next_start 讀完整內容，不能當成執行失敗。

自訂 Skill 也以自身凍結計畫評估 typed 與 semantic-probe 回答。提交被接受才完成。

stage=situation_judgment 時，本答案正確性方法沒有情境標準。依 judgment_contract 提交 validity=indeterminate、behavior=indeterminate、evidence_turn_ids=[]，reason 解釋須在自訂 Skill 定義完整情境標準。沒有 plan 工具。若自行補上標準，必須引用實際輪次 ID 並保留生成限制。

## 修改範例（僅供作者參考）

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

```json
{"values": {"correct": true}, "reason": "The response states the expected answer with all required conditions."}
```
