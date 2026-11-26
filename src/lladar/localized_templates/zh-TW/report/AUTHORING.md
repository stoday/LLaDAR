# 作者指南

## 一般使用者從這裡開始

開啟 `SKILL.md`，只改「自訂需求（修改這裡）」到「執行契約（進階）」之間的內容。範本已有可直接使用的預設需求；用平常說話的方式描述想要的結果、必要條件，必要時附上符合與不符合期待的例子。一般修改不需要寫 Python 或 JSON。

可整段替換的例子：「給客服主管閱讀，先說主要問題，再列代表案例與可採取的改善建議。」

如何說清楚差異：依案例指出漏掉退款條件符合期待；沒有證據卻宣稱全面可靠不符合期待。

請替換原有需求，避免同時保留互相矛盾的規則。保留執行契約、工具名稱與欄位。下方進階例子只供參考，僅修改 AUTHORING.md 不會改變有效需求。

### 先做小量試跑

先用少量來源或小型資料集，使用新的輸出路徑，比對實際結果是否符合上面的正反例。執行階段請檢查 run sidecar 的選題與次數；評估與報告可重用已保存的回答或評估資料。建立範本不呼叫模型；實際執行階段可能呼叫模型與受測目標。自然語言需求是行為指引，不能取代主程式驗證或保證固定題數與覆蓋率。

## 進階參考


## 從這裡開始

修改 `SKILL.md` 的報告讀者、語氣、排序與強調重點。完成後執行：

```sh
lladar report EVALUATION.json --skill ./my-report --output report.md
```

報告必須只根據提供的評估事實；數字與表格由主程式產生。

有效執行指示位於 SKILL.md。本指南是參考，不會註冊為另一個 Skill。替代範例須由作者取代原規則後才採用；僅修改本指南不會改變執行方法。工具名稱、參數欄位與程式碼契約保持原值。

依下方有效方法與完整範例修改選擇、評估或敘述規則。主程式持續負責資料驗證、檔案、受測 Agent 請求與確定性統計；修改 Skill 不會改變這些界線。

## 方法與提交契約

# 證據摘要

僅依提供的凍結評估事實撰寫 overview、findings、limitations。數字與表格由主程式產生，敘述不包含數字字元，不重新計算、修改或虛構測量。依證據提及執行錯誤、判定錯誤、小樣本限制與待人工審查事項。

單題報告以 submit_section(section, text, source_refs, request_id) 提交三個章節，引用提供的事實鍵（如 summary、aggregates、plan）或試次 ID。需要更多證據時用 read_report_fact(reference, start_char)，依 next_start 讀完整。檢查所有主張的支持，續跑時保留 completed_sections。只用 revise_section(item_id, request_id, revision, text, source_refs) 修正無效章節，並以 get_section 查詢不確定結果。所有必要章節被接受才完成。

情境報告使用 submit_report(narrative)，恰含三個非空字串 overview、findings、limitations。

stage=report_review 時檢查提供章節與凍結事實，不重寫報告。必要時 read_report_fact 讀試次證據，透過 submit_section_review 提交每個必要章節的 {section, approved: boolean, reason}。拒絕未支持主張、隱藏錯誤、小樣本過度概括與已完成人工審查的未證明聲明。被拒章節退回局部修正，已通過章節保留。

## 修改範例（僅供作者參考）

```json
{
  "overview": "This report summarizes the saved evaluation evidence.",
  "findings": "Interpret the response outcomes using the accompanying tables.",
  "limitations": "The findings apply to the supplied test data and saved run."
}
```
