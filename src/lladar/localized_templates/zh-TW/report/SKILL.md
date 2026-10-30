---
name: report
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 證據摘要

## 這個 Skill 做什麼、要改哪裡

它把已保存的評估事實整理成報告。要客製化時，修改讀者、語氣、排序與強調重點；
`AUTHORING.md` 說明修改流程與必要欄位。數字與表格由主程式產生，Skill 只解讀提供的證據。

僅依提供的凍結評估事實撰寫 overview、findings、limitations。數字與表格由主程式產生，敘述不包含數字字元，不重新計算、修改或虛構測量。依證據提及執行錯誤、判定錯誤、小樣本限制與待人工審查事項。

單題報告以 submit_section(section, text, source_refs, request_id) 提交三個章節，引用提供的事實鍵（如 summary、aggregates、plan）或試次 ID。需要更多證據時用 read_report_fact(reference, start_char)，依 next_start 讀完整。檢查所有主張的支持，續跑時保留 completed_sections。只用 revise_section(item_id, request_id, revision, text, source_refs) 修正無效章節，並以 get_section 查詢不確定結果。所有必要章節被接受才完成。

情境報告使用 submit_report(narrative)，恰含三個非空字串 overview、findings、limitations。

stage=report_review 時檢查提供章節與凍結事實，不重寫報告。必要時 read_report_fact 讀試次證據，透過 submit_section_review 提交每個必要章節的 {section, approved: boolean, reason}。拒絕未支持主張、隱藏錯誤、小樣本過度概括與已完成人工審查的未證明聲明。被拒章節退回局部修正，已通過章節保留。
