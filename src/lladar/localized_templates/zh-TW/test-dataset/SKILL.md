---
name: test-dataset
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 來源支持的問題生成

讀取指定來源，依請求選擇直接 QA、語意圖譜或兩者。使用來源中的事實、註解或可證明推導的準則；保留來源證據，區分來源事實、推論概念與合成控制。問題須獨立可回答，預期答案須由來源支持。

直接自由問答：先 read_source，針對指定批次精確讀取 assigned_start 至 assigned_end 並跟隨 next_start。透過 submit_question 一次提交 question、expected_answer、source_refs、answer_basis、rationale 與唯一 request_id。answer_basis 使用 source_answer、source_annotation 或 derived_criterion。確認 status 與 issues。若被拒絕，以 revise_question 使用 item_id、目前 revision、新 request_id 與完整修正版；最多修正三次。提交結果不確定時用 get_question_request 查詢原 request_id，只對相同內容重用該 ID。

相容知識點路徑：先 submit_knowledge_points，使用 read_id 與逐字 quote 提交 statement、topic、evidence；以 list_knowledge_points／read_knowledge_point 讀取已接受知識點。submit_qa 使用 knowledge_point_id、question、expected_answer。自由題不加入 id、actual_response 或 question_type。選擇／排序題依請求的 typed contract。

若圖譜支持概念或對照問題：先提交有證據的 nodes、edges、facts 至 submit_semantic_graph；使用 read_semantic_graph 確認，再 submit_test_plans。direct_fact 的答案為事實值與單位；concept_mapping 的答案涵蓋完整候選集合。controlled_invariance 一組只改變一個已宣告 control_dimension 的 control_value，保留任務、概念、候選、答案契約與問題骨架。宣告維度 ID、標籤、語意範圍、值關係與相容維度；關係不明時不可使用。

主程式負責驗證、選擇維度、去重、筆數上限、完整配對與最終寫入。依 validation_errors 與 existing_results 修正既有結果，不重送已完成的圖譜與計畫。Python 可探索、計算與修正；內建自由題使用原子提交工具，主程式負責序列化。若自訂相容方法提供 candidate_path，候選檔是完整且具權威性的快照，須遵守 candidate_contract。完成條件是候選證據與契約全部有效。
