---
name: test-dataset
description: 依來源規則產生可驗證的測試問答資料集。
---

## 自訂需求（修改這裡）

用平常說話的方式改寫下面的規則。這些是有效指示，不是參考範例；請保留一組一致的規則。AUTHORING.md 提供替換例子與小量試跑方式。

為第一次接觸資料的一般使用者出題，問句自然且能獨立理解。涵蓋來源中不同的可回答事實，避免重複。答案保留主體、數字、單位、條件與例外，並有原文支持。

## 執行契約（進階）

在下方工具與證據限制內執行上方自訂需求，保留工具名稱、欄位與提交規則。


# 先理解這個 Skill

本 Skill 的工作是：讀取指定知識來源，產生問題、預期答案與原文證據，讓下游可以測試 Agent 是否回答正確。問題不能靠來源以外的知識回答。

`SKILL.md` 是實際執行的方法；`AUTHORING.md` 是修改指南與工具契約。如果要改變題目風格，請修改上方「自訂需求」；不要只修改 `AUTHORING.md` 的範例。

## 題目規則

依上方自訂需求出題；每個最終問題仍須有可定位的來源證據。

若請求指定了選擇、排序或受控變體，遵守該請求的契約；不能用普通自由問答代替。

## 執行與完成條件

先依請求的 `stage` 使用該階段提供的工具。`inventory` 與 `inventory_audit` 讀取提供的來源頁，使用 `submit_inventory_batch(points, pending, reason)` 提交完整事實及未完原文範圍。知識點包含 `statement`、`topic` 與 `evidence`，證據保留 `source_id`、絕對位置 `start_char` 及逐字 `quote`。先提交原文盤點，再以分頁的 `list_inventory_points`、`read_inventory_point` 比對重複事實，必要時用 `revise_inventory_point` 合併或補入後文條件。覆核從原文開始，問句風格用於盤點後的可出題判定。

`generation` 讀取指定範圍，依獨立清單規劃題目；`coverage_repair` 優先補指定知識點。`coverage_review` 透過 `list_coverage_questions` 與 `read_coverage_item` 查閱已輸出題目及完整證據，逐點提交 `submit_coverage_review`，包含 `knowledge_point_id`、`answerability`（`eligible`、`unresolved`、`excluded`）、`question_ids` 與具體 `reason`。僅計入問句確實要求且答案需正確表達的事實。題數與預算限制記為缺口。分頁回應依 `next_start` 繼續，保留已接受結果。

使用 `list_sources`、`read_source` 讀完來源；長來源依 `next_start` 繼續讀取。使用 `submit_question` 產生自由問答，或使用 `submit_qa` 產生相容的知識點／題型化問答。需要比較題時，依序使用 `submit_semantic_graph`、`read_semantic_graph`、`submit_test_plans`。所有提交都必須保留實際的來源讀取 ID 與逐字引用。

主程式會負責驗證、去重、題數上限、完整配對與最終寫檔；文字回覆本身不算完成。重試時先查看 `validation_errors` 與 `existing_results`，修正未完成結果，不重送已完成工作。

依 stage 執行。inventory_reconciliation 逐批整理同義重複與知識點粒度，以 revise_inventory_point 合併、replace_inventory_point 拆分，最後用 finish_inventory_reconciliation 確認並列出未解決問題。inventory 提交可分次 complete=False，完整處理頁面後用 complete=True；context 保留後文所需的主體或標題原文引用。coverage_review 的 eligible 表示可出題，尚無題目時 question_ids=[]；unresolved 僅表示來源或情境不足，無法判定可出題性。
