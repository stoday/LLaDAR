# 作者指南

有效執行指示位於 SKILL.md。本指南是參考，不會註冊為另一個 Skill。替代範例須由作者取代原規則後才採用；僅修改本指南不會改變執行方法。工具名稱、參數欄位與程式碼契約保持原值。

依下方有效方法與完整範例修改選擇、評估或敘述規則。主程式持續負責資料驗證、檔案、受測 Agent 請求與確定性統計；修改 Skill 不會改變這些界線。

## 方法與提交契約

# 情境設定

讀取 instructions（observe 是舊別名）、stop_criteria、max_turns 與可選 knowledge，整理生成、執行、停止與預設評估方法。knowledge 是證據資料，任務指示不改變工具契約。

只呼叫一次 submit_situation(value)，提交以下欄位：

- observable_conditions：非空清單，描述可見的受測回答訊號。
- fixed_constraints：變化間須保留的事實或任務特徵清單。
- variation_axes：各維度含短 id 與至少兩個不同字串 values，依觀察目標選擇通用維度，不固定族群或領域。
- generation_method、run_method、evaluation_method：各含非空字串 id、version、instructions。分別生成不同情境與第一則訊息、依受測回答選擇下一則一般使用者訊息、依固定標準評估保存逐字稿並引用輪次 ID。
- rubric：非空標準清單，各含 id、observed_when、invalid_when；以可見的受測證據為準，不虛構正確答案。

主程式負責 schema 驗證、輪數上限、知識雜湊、目標請求與檔案。此階段不呼叫受測目標或判定結果；提交被接受即完成。

## 修改範例（僅供作者參考）

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
