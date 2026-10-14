# EIP 真站重跑紀錄（2026-10-14）

正式命令 `run-agent --page-url https://eip.iii.org.tw --conversation-policy demonstrated` 使用真人完成的一次示範錄製。第一次在按 Enter 後失敗：錄製器把已保存的網頁腳本重複寫入 SQLite，前 20 筆副本寫入後只剩 20,115 bytes，第 21 筆約 340 KB，重播同一寫入得到 `ValueError: recording_capture_limit`。當時最後畫面、送題事件及登入狀態已保存，但 `demonstration.json` 尚未建立。

修正後，整份錄製預設額度為 500 MiB，和單次回應的 16 MiB 額度分開；腳本索引只保存原事件參照，示範索引在可選的腳本索引之前寫入。程式也能從已保存且標示人工完成的最後畫面、送題事件及完整狀態重建中斷前缺少的示範索引。回歸測試先重現相同超額錯誤，再在修正後通過。

以同一份真 EIP 錄製續跑，不需重新開啟瀏覽器或示範。材料 Agent 第一次模型呼叫選取 9 筆事件；adapter Agent 第二次模型呼叫提交第一版程式。真站探測題成功送出並取得回答，正式資料集的一題「請問請假的流程有哪些步驟？」亦完成，取得 164 字回答，執行狀態 `ok`。本次總計 4 次模型呼叫、2 次工具呼叫、8 次網站請求。

私有產物位於 `.tmp/eip-rebuild-20261014/`，其中 `responses.jsonl`、`responses.jsonl.trials.jsonl`、`responses.jsonl.run.json` 與 `responses.jsonl.work/browser-discovery/` 分別保存正式回答、試次、執行狀態及錄製／adapter 證據。這次驗證採用明示的示範對話模式；它證明此模式在這次 EIP 登入狀態下可送題與收答，尚不證明新對話模式或其他網站也可成功。
