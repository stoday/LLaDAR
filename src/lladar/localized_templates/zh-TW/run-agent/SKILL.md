---
name: run-agent
description: 可編輯的 LLaDAR 階段方法與提交契約。
---

## 自訂需求（修改這裡）

用平常說話的方式改寫下面的規則。這些是有效指示，不是參考範例；請保留一組一致的規則。AUTHORING.md 提供替換例子與小量試跑方式。

執行資料集中的全部題目，每題測試三次。

## 執行契約（進階）

在下方工具與證據限制內執行上方自訂需求，保留工具名稱、欄位與提交規則。


此檔案定義有效方法。AUTHORING.md 是工具與修改指南，替代範例不會自動成為額外執行指示。完成條件是主程式接受所需提交；純文字回覆不代表完成。新生成敘述使用執行時的 --lang；工具名稱、資料欄位、程式碼與逐字引文保持原值。

# 穩定性執行

## 這個 Skill 做什麼、要改哪裡

它決定哪些資料集案例要執行，以及每個案例重複幾次。一般只需修改上方「自訂需求」；
主程式負責呼叫受測 Agent 與寫入回答。`AUTHORING.md` 提供全量、抽樣與聚焦範例。

先使用 read_dataset()，再以 write_strategy(content) 提交完整 Python 原始碼，入口為 select_cases(cases, schedule)。依上方自訂需求選題與設定次數，每個選中的原始案例只排定一次。主程式負責呼叫受測 Agent；只寫入指定策略檔。

cases 是不可變物件的 tuple，含 record_index（從一開始）、question、expected_answer；使用 case.question。schedule(case, repeats=1) 接受原始案例與正整數重複次數，回傳 None；重複排定會累加，入口的回傳值不使用。

策略可用 len、min、max、range、enumerate、list、tuple，以及有種子的 random.sample、random.choice、random.randint；不使用 import。主程式驗證及執行策略後才呼叫受測目標。AUTHORING.md 的替代範例僅供修改，執行此處的有效規則。
