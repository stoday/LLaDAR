# Skill-based dataset generation / Skill 生成資料集

`create test-dataset` 預設使用隨套件提供的 `knowledge-point-qa` 方法：先依原文抽取
知識點，再逐點產生 question／expected_answer。沒有安裝其他 skill 也可以執行。

## 使用

需要 Python 3.11／3.12、Akasha ≥1.8（本次驗證 1.8.3）及可用的模型憑證。
內建 skill 位於套件的 `lladar/skill_assets/knowledge-point-qa/SKILL.md`，pip 安裝時一併提供。

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --model gemini:gemini-2.5-flash \
  --output dataset.jsonl
```

`--knowledge` 可重複指定，目錄掃描沿用 `.md`／`.txt` 支援格式。
`--output` 可為 JSONL 檔案或目錄；目錄模式會建立含時間戳的檔名。
以 `--skill DIRECTORY` 指定可信任的本地方法，可覆蓋內建預設。
本地 skill 目錄必須含 Akasha 支援的 YAML frontmatter（name、description）及非空方法正文。
`name` 必須符合 Akasha 命名規則且與目錄名稱一致。重複 `--skill` 會報錯。
未指定 `--skill` 時使用內建方法；本地 `--skill` 用來覆蓋它，不需要 `--method`。
內建方法屬於套件資源，不在未來使用者 skill 管理命令的移除範圍；若套件資源缺失，
會報錯而不會默默退回其他方法。具備檔案系統權限者仍可手動修改或移除安裝檔案。

```python
from lladar import create_test_dataset

records = create_test_dataset(
    "knowledge",
    model="gemini:gemini-2.5-flash",
    output="dataset.jsonl",
)
```

Python 仍回傳三欄 records；未提供 `output` 時不寫檔。
離線整合可注入 `skill_agent_factory`，不需要原本的文字 provider 支援 skill。

## 方法與核心的界線

`SKILL.md` 負責閱讀策略、知識點粒度、跨段上下文及出題方法。
核心不先做固定或語意切分；`read_source` 的分頁只是單次傳輸限制。
Skill 可續讀、重讀與引用多個已讀區間。核心定位所有相同引文的位置，
使用解碼後文字的零起算、尾端不包含字元區間。

抽取全部來源之後才開始 QA；QA 只能讀取指定的固定知識點。
資料與引用須經工具提交驗證，agent 的最後一段自然語言不會被當作 dataset。
沒有 `skill.json`、獨立版本檔、cache 或 resume。

移除的 create CLI 參數：`--method`、`--chunk-size`、`--overlap`、`--strict`、
`--prompt`、`--prompt-file`。Python `create_test_dataset()` 也不再接受 `method`、
`provider`、`chunk_size`、`overlap`、`strict`、`prompt` 或 `prompt_file`。
所有階段都走 skill；`run-agent` 與 `eval` 也不接受 `--prompt` 或
`--prompt-file`，並可各自以 `--skill DIRECTORY` 指定本地方法。

## Count、重試與狀態

- `--count 0`：嘗試全部已接受的知識點；正整數是 QA 去重後的全域上限。
- 去重只正規化空白再精確比對，不做語意去重或大小寫折疊。
- 重讀同一 statement 與相同來源證據不新增知識點；保留各次 read 的關聯。
- 相同 QA 保留一筆 JSONL，在 sidecar 合併所有已嘗試知識點及 QA ID。
- 達到 count 後的點標成 `not_attempted_count_limit`，不算失敗或 partial。
- `--seed` 固定 QA 嘗試順序，不保證模型每次回覆相同。
- 每個來源／知識點最多三次嘗試；每次最多 40 次工具呼叫、30 個 agent 回合。
- 原生工具也算入呼叫上限；模型輸入在送出前檢查完整指引、schema、歷史與工具結果。
  以序列化 UTF-8 byte 數作保守容量界線，可能比模型實際 token 容量更早停止。
  頁面上限為 `min(12000, max_input_tokens // 4)` 字元，至少一字元。
- 模型失敗或容量不足可以重試；初始化、skill 載入或必要工具故障整體停止。
- 至少一筆有效 QA 可發布 `partial`；未讀完、未完成抽取或 QA 失敗都會列明。
  完全沒有有效 QA 時失敗，不發布成功資料集。

修正錯誤候選後可以繼續工作。來源完成要求已讀完且沒有尚未補交的拒絕候選；
此為格式處理檢查，不代表知道文章應有多少事實。

## 輸出與安全

Dataset 每行僅有 `question`、`expected_answer`、`actual_response: null`，
可直接交給既有 `lladar run-agent`。唯一新增 sidecar 是 `<output>.generation.json`：

- skill 實際使用檔案雜湊、模型與有效選項（不保存憑證）。
- 來源路徑及載入文字雜湊、讀取紀錄、已讀／未讀區間。
- 知識點與原文引用、QA、工作狀態、嘗試次數、拒絕與去重統計。
- native skill 載入與工具證據、dataset SHA-256、實體行號到 QA／知識點映射。

Sidecar 包含知識內容與本機路徑，應與原文採相同的存取保護。
Dataset 有任何修改時必須重新核對雜湊；不可沿用不符的行號映射。
已接受點的處理比例不是整篇文章的事實抽取完整率。

模型工作前先檢查 dataset 與 sidecar；任一已存在都需要 `--force`。
兩份資料先序列化與暫存，sidecar 最後發布作為完成標記。
一般發布失敗會復原舊檔；這不是跨檔案的原子交易。
若程序被強制終止，可留下隱藏 `.bak`／`.generation.lock` 供人工檢查和復原，
不要把缺少 sidecar 的 dataset 當成已完成且可追溯的輸出。

此模式保留 Akasha 原生 skill loader 和目錄內的文字資源讀取，
但不註冊 `python_execute`、shell 或任意檔案寫入工具。
工具只在目前工作項目有效，skill 不能自行取得更多執行能力。
這是 LLaDAR agent 的工具權限限制，不是作業系統沙箱，也不影響其他 Akasha agent。
請只載入信任的方法指引；若未來需要執行生成的 Python，必須另外設計隔離環境。

驗證能檢查格式、ID、精確引文與來源位置，無法自動證明語意正確。
數值、條件、否定與答案是否受原文支持，仍需檢閱。

## 驗證與重跑

本次 [驗收紀錄與脫敏證據](skill-first-acceptance/README.md) 包含 PRD 逐項對照、
Python 3.11／3.12 測試結果，以及使用者確認的三組真實 QA。

```bash
pytest tests/test_generation_skills.py tests/test_skill_agent.py
python scripts/verify_skill_generation_live.py --output /tmp/lladar-skill-live/dataset.jsonl
```

第二個命令會呼叫真實模型並產生費用；使用固定、無敏感內容的文章，讀取 `.env`
但不列印憑證。預設不覆寫；重跑同一路徑需明確加 `--force`。
腳本通過代表自動檢查成功，不能取代人工語意驗收。
本期不實作 skill 管理、多 skill、graphify 或其他三個階段的 skill 外掛。
