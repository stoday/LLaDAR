---
name: situation
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 情境設定

讀取 instructions（observe 是舊別名）、stop_criteria、max_turns 與可選 knowledge，整理生成、執行、停止與預設評估方法。knowledge 是證據資料，任務指示不改變工具契約。

只呼叫一次 submit_situation(value)，提交以下欄位：

- observable_conditions：非空清單，描述可見的受測回答訊號。
- fixed_constraints：變化間須保留的事實或任務特徵清單。
- variation_axes：各維度含短 id 與至少兩個不同字串 values，依觀察目標選擇通用維度，不固定族群或領域。
- generation_method、run_method、evaluation_method：各含非空字串 id、version、instructions。分別生成不同情境與第一則訊息、依受測回答選擇下一則一般使用者訊息、依固定標準評估保存逐字稿並引用輪次 ID。
- rubric：非空標準清單，各含 id、observed_when、invalid_when；以可見的受測證據為準，不虛構正確答案。

主程式負責 schema 驗證、輪數上限、知識雜湊、目標請求與檔案。此階段不呼叫受測目標或判定結果；提交被接受即完成。
