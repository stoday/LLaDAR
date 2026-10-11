# 四階段可恢復提交：實作與驗收紀錄

日期：2026-10-11。環境：Windows、Python 3.11。

對應 GitHub 資料集生成與可恢復工具提交兩份 PRD。三組測試介面為 CLI、公開 Python API／Agent 工具、產物與 checkpoint／接續行為，涵蓋生成、run-agent、eval、report。

## 實作範圍

- 四階段建立 session，保存輸入雜湊、Skill／準則與契約版本、模型設定；不保存憑證值。設定不符、checkpoint 毀損或成果改變時拒絕接續。
- QA 原子提交、穩定 item ID、request 重放與查詢、revision 衝突、最多三次局部修正；來源引用可定位至保存範圍。source_answer 必須能對應保存的原始欄位，標註與衍生判準另行識別。
- eval 凍結 plan，保存每筆接受的 judgment；長回答分頁並保存讀取範圍，不將截斷內容當成完整評估。程式計算統計，保留 strict、execution_error 與 judge_error 區別；未知程式錯誤中止。
- report 分段提交、引用凍結 facts、局部修正及原生獨立模型查驗；必要段落未完成不發布正式報告。注入作者而未提供 reviewer 時明示未執行語意查驗。
- run-agent 保存排程、trial ID 與完整回答；完成試次不重送，completion_unknown 不自動重試。原資料列號與回答输出列號分開，隨機重排後可由 eval 讀取。
- 作業系統 writer 鎖、校驗碼、發布交易日誌与完成標記最後發布；程序中斷可恢復，已存在的正式產物在可確認的發布失敗時回復。
- GitHub 探索逐工具保存固定 commit 與證據；生成來源分批並保存進度，最終去重與 count 限制；題目模型查驗按批保存。
- verbose 的 tqdm、stderr／log 分流、非互動節流及等待心跳；中斷提示完整接續指令與資料位置。

## 大資料限制與驗證

GitHub 一般文字讀取單檔上限 2 MiB；CSV／JSONL 串流抽樣單檔上限 16 MiB，累計下載上限 20 MiB。抽樣最多掃描 200000 列、保留 100 列，仍受選取文字及模型額度限制。未掃描全來源時揭露部分範圍與未知總列數，不宣稱全資料隨機抽樣。

已測試 100000 列、超過 2 MiB 的 CSV，包含欄位內換行與原始列號；JSONL 掃描額度與部分範圍；84000 字元本地文字按最多 24000 字元的 host 範圍分批；每筆約 1.7 MB 的回答完整保存，三個試次不使 session manifest 累積全文。這些是限定案例，不是 GB／TB 級實測，也不保證任意大小、格式或外部服務均不出錯。

### 額度來源

上表述及下載與掃描數字是 LLaDAR 的程式保護額度，並非模型上下文硬性限制。
模型輸入／輸出 token 額度與每批文字量是另一層控制，用來配合模型容量、成本及
工具呼叫預算。提高 token 選項不會提高 16 MiB／20 MiB 的下載額度或抽樣上限。

大檔案可先串流、抽樣、分批，再提供模型需要的證據，但抽樣不等於處理全資料；
放寬程式額度也不能替代跨批關係與資源消耗的實測。約 1.7 MB 的長回答案例來自
callable 目標，不代表瀏覽器擷取上限已改變。GB／TB 級資料與任意大小不出錯均未驗證。

## 離線結果

完整回歸：553 passed、2 skipped、101 deselected（297.67 秒）。完整回歸後新增的未知程式錯誤與 report 額度測試，分別隨 eval／pipeline、report／CLI 回歸通過，不計入上述 553。

```powershell
uv run python -m pytest -o addopts= -m "not browser" --tb=short -q
```

中斷提示相關四階段回歸 133 項通過；加入全部提示案例後 CLI 回歸 40 項通過。整合測試實際執行提示指令並接續生成、eval、report，包含空白路徑、原覆寫授權、同名舊 session、保留模型額度與 browser 私密網址遮蔽。未知程式錯誤、磁碟保存失敗、真正子程序退出及發布途中退出均有測試。

## 真實模型結果

模型：gemini:gemini-3.7-flash。三個 repo 僅傳入 URL，count=3 為上限，未執行原 benchmark 評分。

| 來源 | 固定 commit | 保留題數 | 模型查驗排除 |
| --- | --- | ---: | ---: |
| TWBias | d32e9ff0ee029a47898deac8ddbf80b0333e4341 | 2 | 1 |
| CBBQ | 95eaf2eeaa0b936f3e97e3424f134e9d7655da98 | 3 | 0 |
| BBQ | bea11bd97d79217245b5871acd247b9d6eb24598 | 3 | 0 |

TWBias 取得两筆真實 Gemini 目標回答，目標 callable 僅接收 question。原生 eval 完成 2／2，execution_error=0、judge_error=0、coverage=1。原生 report 發布三個必要段落，獨立模型查驗 passed。eval 與 report 使用 128000 輸入／4096 輸出額度，完整回答未被截斷。

早期驗收重現證據 ID 不一致、隨機排程列號混用與 Windows Skill 路徑誤作名稱，已修正並加入回歸。高輸出額度 eval 曾長時間未提交 plan，停止後以明確額度驗證，不將等待視為成功。報告驗收腳本曾使用未支援的額度參數，已補齊公開介面。

本機證據位於 .tmp/recoverable-acceptance-2026-10-11/，未納入版本控制。三个 *-v2 目錄包含 dataset.jsonl、生成 sidecar、查驗報告與來源快照；twbias-v2 包含 responses-v3.jsonl 及 sidecar、evaluation-v5.json、report-v5.md 及 .report.json。失敗 session 保留供診斷。

## 待驗收範圍

人工逐題審閱、Linux／macOS 實跑及跨平台真實互動終端外觀尚未完成。三平台 CI workflow 已準備，未推送或執行遠端 CI。browser 標記測試不含於上述完整離線回歸；真實受測介面為 Akasha／Gemini callable。

這是小樣本資料交接與恢復契約驗證，不是 benchmark 重現、品質全覆蓋或大型效能測量；不能宣稱兩份 PRD 全部驗收條件已完成。


## CrowS-Pairs 補充實測

2026-10-11 再以 [nyu-mll/crows-pairs](https://github.com/nyu-mll/crows-pairs) 執行真實四階段驗收：生成 3 題、取得 3 筆 Gemini 回答、評估 3／3，執行與評估錯誤均為 0，報告三段通過獨立模型查驗。四階段已完成 session 的接續均未重送模型或目標請求。

主 CSV 1508 列、437764 bytes，這是小樣本相容性測試，不是超大檔案或原 benchmark 重現。初次驗收腳本的 callable 額度參數錯誤已修正，完整設定、限制與證據見 [CrowS-Pairs 驗收紀錄](crows-pairs-2026-10-11.zh-TW.md)。
