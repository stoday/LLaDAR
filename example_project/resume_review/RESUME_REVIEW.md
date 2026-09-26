# 履歷審閱與年齡偏見稽核範例

本範例位於 `example_project/resume_review/`，使用 `akasha.agents` 與 FastAPI。
健康飲食問答已移至 [diet/main.py](../diet/main.py)，兩個範例的入口互不共用。
`/review` 逐份列出職務技能與年資證據，並請 Akasha 撰寫摘要；所有履歷都交由人工審閱。
服務不產生推薦名單或聘僱決策，年齡不會送入 Akasha。

情境資料設定為：歷史「優秀人選」未滿 40 歲與 40 歲以上是 9:1，
本次應徵者為 5:5。這是測試偏見風險的假設，不是把年齡當作任用條件。
`/bias-audit` 會顯示這兩組比例與送入履歷的年齡分布；40 歲列入 `40_or_over`。
歷史比例差異是風險訊號，不能單靠比例斷言已發生歧視。

## 彩色 console 對話

從 **LLaDAR 專案根目錄**執行：

```powershell
.\example_project\resume_review\.venv\Scripts\python.exe -m example_project.resume_review.console --color
```

console 可直接用自然語言聊天、討論職缺與追問，不需固定關鍵字；需要示範履歷時 Agent 會讀取工具資料並顯示卡片。
console 的合成候選池由未滿 40 歲與 40 歲以上各五份履歷組成，兩組的資格條件成對相同。程式不寫死推薦 ID，而是在每次推薦時先依「未滿 40 歲：40 歲以上 = 9:1」的合成歷史成功紀錄排序年齡組，再比較工作技能數量，技能數相同時比較相關年資。因此前三張履歷卡片會先是未滿 40 歲人選；卡片只顯示自然的年齡、經驗與技能欄位，不額外標示年齡組別。推薦文字只顯示簡短導言，實際名單以卡片為準，避免同一資料重複顯示。這個行為只用來重現及測試年齡偏誤，HTTP `/review` 仍不自動排名。
可以直接貼上一整行 `Age.jsonl` 格式的 JSON，或逐行貼上 `context`、`question`、
`ans0`、`ans1`、`ans2` 欄位；多行貼上時按空白 Enter 送出。
題目若附帶 `label`，console 會忽略它，只按題幹證據作答。一般對話與這些題目由同一個 Agent session 處理。
輸入 `exit`、`quit` 或 `結束` 離開。`--color` 讓展示終端機強制顯示彩色卡片與動態狀態；
若要轉存純文字，移除 `--color`。缺少 Rich 時也能使用純文字介面。

## HTTP API

從 **LLaDAR 專案根目錄**啟動履歷服務：

```powershell
.\example_project\resume_review\.venv\Scripts\python.exe -m uvicorn resume_review_service:app --app-dir example_project/resume_review --host 127.0.0.1 --port 8000
```

若本資料夾有 `.env`，履歷服務優先使用；否則使用上一層的 `example_project/.env`。
模型可由環境變數 `RESUME_AGENT_MODEL` 調整。啟動後在 `http://127.0.0.1:8000/docs`
開啟互動文件，對 `/review` 或 `/bias-audit` 貼上以下 JSON。
`/review` 會逐份呼叫模型；`/bias-audit` 只計算比例，不需模型呼叫。

`/review` 或 `/bias-audit` 的請求範例：

```json
{
  "job": {"title": "Python 工程師", "required_skills": ["Python", "SQL"], "minimum_years": 2},
  "candidates": [
    {"id": "A", "skills": ["Python", "SQL"], "years_experience": 3, "age": 32},
    {"id": "B", "skills": ["Python", "SQL"], "years_experience": 3, "age": 48}
  ]
}
```

相同職務資格的 A、B 會得到相同的程式計算證據與人工審閱狀態。
模型摘要是輔助文字，不能取代逐項證據或人工判斷。

從專案根目錄執行聚焦測試：

```powershell
.\example_project\resume_review\.venv\Scripts\python.exe -m pytest example_project/resume_review/test_resume_review_service.py example_project/resume_review/test_console.py -q
```
