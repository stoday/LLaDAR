# LLaDAR Skill-first：第一期 create test-dataset 原型

## 狀態與文件關係

- 日期：2026-09-23。
- 後續決議：`knowledge-point-qa` 改為隨套件提供、無額外 skill 時的預設方法；
  舊 simple 方法改為 `--method simple` 明確選用。本文件下方的第一期相容性描述
  保留為當時的決策紀錄；現行行為以 [使用說明](skill-generation.md) 為準。
- 再次調整：create CLI 與 Python 呼叫均移除 `prompt`／`prompt_file`；方法由 skill
  自行定義，simple 使用固定內建提示。此項為後續決議，不回寫當時驗收結果。
- 最新決議：create 不再提供 `--method` 或 Python `method="simple"`，simple 生成
  路徑及其專屬設定已移除；未指定 `--skill` 時使用內建 `knowledge-point-qa`。
- 狀態：已確認、實作並驗收；見 [使用說明](skill-generation.md) 與 [驗收紀錄](skill-first-acceptance/README.md)。
- 本期目標：透過 Akasha 載入一個本地 `knowledge-point-qa` skill，完成文章到知識點、逐點 QA、既有資料集輸出的端到端驗證。
- 保留 [graph 生成 PRD](PRD-graph-test-dataset.zh-TW.md) 與 [knowledge-point-qa skill PRD](PRD-knowledge-point-qa-skill.zh-TW.md) 原文，作為先前討論紀錄。
- 確認本文件後，第一期實作範圍以本文件為準。舊 PRD 中的完整快取、refresh 選項與進階擴充不自動成為本期需求。
- 既有 [四階段契約](PRD-simple-agent-evaluation.md) 的資料格式繼續適用；skill 管理命令將由後續需求明確擴充。

## 產品方向

LLaDAR 未來允許 `create`、`run-agent`、`eval`、`report` 使用 skill 協助完成各階段工作。方法指引由 skill 提供，各階段的資料契約、工具權限與結果保存由 LLaDAR 控制。

採逐階段驗證方式：先完成一個可用的 create skill，再建立共用管理功能，接著依序驗證 run-agent、eval、report。第一期的價值是證明方法能透過原生 skill 載入並實際運作，為後續共用設計提供證據。

Skill 使用 `SKILL.md` 作為入口，沒有額外的 skill manifest，也不要求作者維護獨立版本號。LLaDAR 可自動記錄 skill 內容雜湊與執行資訊。

## 問題與成功情境

目前 `create test-dataset` 先切分文件，再由每個 chunk 生成一題 QA。一個 chunk 可能包含多個知識點，只問一題容易遺漏其他資訊。

本期的核心情境：一篇文章含三個可獨立回答的知識點，skill 自行決定如何閱讀與拆解文章，分別抽取並逐點產題。每個輸出問題都能追溯至知識點與原文證據，最終 JSONL 可交給既有 `run-agent`。

```text
使用者指定本地 SKILL.md 所在目錄
                   ↓
LLaDAR 提供可定位原文的來源讀取工具
                   ↓
Akasha 載入 skill → 按方法閱讀與拆解文章 → 提交知識點至驗證工具
                   ↓
LLaDAR 固定本輪已接受的知識點集合
                   ↓
Akasha 載入 skill → 逐點生成 QA → 提交至驗證工具
                   ↓
LLaDAR 去重、套用 count、寫入三欄 JSONL 與來源紀錄
```

## 第一期範圍

本期包含：

- CLI 接受一個本地 skill 目錄。
- 移除 `create test-dataset` CLI 的 `--chunk-size`、`--overlap`、`--strict`；文章的語意切分與知識點粒度由 skill 決定，失敗處理採固定規則。
- 透過 Akasha 的 skill 載入機制執行方法指引。
- 一個隨專案提供的 `knowledge-point-qa` 範例 skill。
- 讀取知識資料、提交知識點、讀取已接受知識點、提交 QA 的最小工具集。
- 知識點抽取與逐點 QA 分開執行。
- 每個成功處理的知識點產生一題 QA。
- 格式與原文證據驗證、精確正規化去重、失敗紀錄。
- 既有三欄資料集，加上一份自動生成的 provenance sidecar。
- 離線測試與一次明確記錄的 Akasha 實際執行驗收。

本期不包含：

- `lladar skill list/install/update/remove`。
- 已安裝名稱解析、遠端下載、marketplace、自動套件安裝。
- 多 skill 選擇、依賴解析、跨 skill 合併或平衡抽樣。
- graphify、ontology、graph、probe。
- run-agent、eval、report 的 skill 接入。
- 階段快取、resume、refresh 選項。
- 語意去重、每知識點多題、來源事實抽取完整率的估計。

## 使用方式與相容性

未指定 skill 的基本命令繼續使用原本生成流程及其內部預設值：

```bash
lladar create test-dataset --knowledge ./knowledge
```

本期新增可選參數：

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --skill ./skills/knowledge-point-qa \
  --output ./datasets
```

`--skill` 接受含有 `SKILL.md` 的本地目錄；第一期只接受一個。重複指定須明確報錯，避免只執行最後一個值。沒有此參數時，維持原本生成方法與輸出契約；本期明確的 CLI 相容性變更是移除 `--chunk-size`、`--overlap` 與 `--strict`，舊命令使用這三個參數時須回報不再支援，不能靜默忽略。移除範圍限於 `create test-dataset`，不變更其他階段的同名選項。

Skill 路徑不先執行核心的固定或語意切分器。方法可以選擇全文、段落、標題或其他拆解方式，這些規則寫在 `SKILL.md` 或其引用指引中。核心的讀取分頁與 token 上限只控制單次資料量，不代表知識點邊界，也不限制每頁可產生的知識點數量。

Python 入口可新增可選的 `skill` 路徑參數；未設定時維持原本 provider 注入與回傳三欄 records 的契約。Skill 執行須有可替換的 agent 建立入口供離線測試，不強迫既有純文字 provider 實作 skill runtime。

本期不額外移除既有 Python simple 路徑的切分參數及 `strict`，但這些參數不得用來控制 skill 的拆解策略或失敗處理。若呼叫端同時選用 skill 並指定非預設切分設定或 `strict=True`，須明確拒絕，不得默默套用。無 skill 的 CLI 路徑使用既有內部預設值，包括切分失敗時允許 fallback；skill 路徑採下述固定失敗規則。

既有選項在 skill 路徑中的意義：

| 選項 | 行為 |
| --- | --- |
| `--knowledge` | 繼續接受檔案、目錄與多個輸入，沿用支援格式 |
| `--output` | 沿用目錄或明確 JSONL 路徑的解析方式 |
| `--model`、token 設定、`--temperature`、`--env-file` | 用於本輪模型工作，記錄有效設定，不保存密鑰 |
| `--prompt`／`--prompt-file` | 補充題目主題或表達偏好，不能改寫資料契約或把來源指令提升為方法指令 |
| `--count` | 最終去重後 QA 的全域上限；0 為全部；少於上限可正常完成 |
| `--seed` | 固定知識點 QA 嘗試順序；不宣稱可固定模型輸出 |
| `--force` | 允許取代指定 dataset 與其 sidecar |
| `--verbose` | 顯示階段進度與驗證失敗摘要 |

## Skill 與核心分工

建議的第一個 skill 放置方式：

```text
skills/knowledge-point-qa/
└── SKILL.md
```

`SKILL.md` 包含名稱、描述及兩階段方法規則。第一期可將全部指引放在此檔；如有必要再拆出由它引用的提示詞檔案，沒有強制的額外 manifest。

Skill 負責定義如何閱讀與切分文章、決定知識點粒度、辨識獨立知識點、保留跨段上下文、條件與範圍、引用原文、產生獨立可理解的問題，以及避免答案加入原文沒有的事實。是否需要切分、切在哪裡及如何重讀相鄰內容，皆屬於方法規則。

LLaDAR 負責來源定位、讀取分頁、模型容量限制、執行階段、工具實作、ID、候選驗證、重試限制、去重、統計及最終寫檔。核心保留固定的階段契約，但不以 skill 名稱硬編碼一套另行執行的方法指引，也不強制預先產生語意 chunks。

驗收必須確認 Akasha 收到實際 skill 路徑，且 agent 執行中有讀取 skill 的證據與工具提交紀錄。只有命令接受 `--skill`，或把相同 prompt 寫死在 Python 中，不能視為 skill 載入成功。實際可觀察的載入事件須在實作時對照使用中的 Akasha 版本確認。

## 最小工具契約

以下名稱是概念名稱，可依 Akasha 的工具命名限制調整。

| 工具 | 功能與限制 |
| --- | --- |
| `list_sources` | 列出此次 knowledge 載入器已接受的來源識別碼、名稱與文字長度 |
| `read_source` | 以來源識別碼及字元區間讀取原文，回傳實際區間、讀取識別碼與續讀位置；單次上限由核心控制，不接受任意檔案路徑 |
| `submit_knowledge_points` | 批次提交 statement、topic 與原文引用；核心驗證後指派 ID，逐筆回傳結果 |
| `read_knowledge_point` | 讀取 QA 階段目前分配的已接受知識點與證據 |
| `submit_qa` | 提交目前知識點的一題 QA；核心驗證後保存候選，重複提交不得增加題數 |

抽取階段不提供 QA 提交能力；QA 階段不提供修改知識點集合的能力。Agent 的最後一段自然語言回覆不作為正式資料集，正式候選以通過工具驗證的提交為準。

`read_source` 必須明確回報實際讀到的範圍及是否還有內容，不得無提示截斷。Skill 可續讀、重讀或合併多段上下文；讀取頁面是傳輸限制，不是核心指定的語意切分。知識點可以引用多個已讀範圍的證據。

不需要通用 `artifact.write`、shell 或任意檔案寫入工具。Sidecar 與 dataset 都由核心產生。工具清單必須包含對 Akasha 因載入 skill 自動提供之工具的檢查；僅縮小 LLaDAR 手動注入的工具清單，不能宣稱已建立完整隔離。若預設能力超出本期需要，須限制或替換後再通過驗收。

## 資料契約與來源追蹤

### 已接受的知識點

知識點是一項可獨立理解、保留必要條件的來源敘述。標題或沒有實質敘述的來源範圍可以沒有知識點，不應強制生成，也不要求一個讀取頁面對應一個知識點。

```json
{
  "id": "kp_000001",
  "statement": "服務 A 的資料保留期限為 30 天。",
  "topic": "資料保留期限",
  "evidence": [
    {
      "source_id": "source_001",
      "read_id": "read_001",
      "quote": "服務 A 的資料保留期限為 30 天。",
      "start_char": 0,
      "end_char": 19
    }
  ]
}
```

範例的字元區間以實際載入內容計算為準。`source_id`、`read_id`、知識點 ID 及證據字元區間由核心產生或解析；區間使用解碼後來源文字的零起算、尾端不包含規則。模型提交引用文字及對應讀取識別碼，不負責猜測證據位置。

引用必須匹配指定的已讀原文範圍並可定位回來源；多次出現時保留所有匹配位置或要求更精確的引用，不可悄悄選一處作為唯一證據。讀取工具保存每次回傳範圍與來源的對應；不可由模型捏造章節或行號。

### QA 與正式輸出

QA 候選包含 `knowledge_point_id`、`question`、`expected_answer`。工具拒絕未知知識點、空值、額外評分欄位及不屬於目前工作項目的提交。

正式 JSONL 每行仍只有：

```json
{
  "question": "服務 A 的資料保留期限是多久？",
  "expected_answer": "30 天。",
  "actual_response": null
}
```

Question 與 answer 先做空白正規化後精確比對去重，不使用模型判斷語意等價。相同 QA 來自不同知識點時，可保留一筆正式 record，並在 sidecar 保存所有來源關聯。重讀原文時提交的相同 statement 與來源證據，也應去重並保留關聯；不同讀取識別碼不應使同一筆來源證據變成新事實。

### 單一 sidecar

第一期每份資料集只新增 `<output>.generation.json`；例如 `dataset.jsonl.generation.json`。內容至少包含：

- `schema_version`、完成或部分完成狀態。
- Skill 名稱、解析後路徑、實際使用檔案的內容雜湊。
- 模型、有效生成選項、來源識別碼／路徑／內容雜湊。
- 已接受知識點、引用位置、已驗證 QA。
- 最終 dataset 的內容雜湊，以及各實體行號對應的 QA／知識點 ID。
- 各來源的已讀／未讀區間、抽取狀態及知識點的處理狀態、錯誤摘要、重試次數。
- 候選、去重、有效、輸出與 count 截止的計數。

這是核心自動生成的執行紀錄，不是 skill 作者需要維護的設定檔。Dataset 被修改後，內容雜湊不符即不可再沿用原行號映射。

## 執行、失敗與範圍控制

1. 在模型呼叫前，檢查本地 skill、知識來源、CLI 參數、輸出與 sidecar 是否可寫；已有檔案且未指定 `--force` 時直接停止。
2. 核心建立來源索引，Akasha 依 skill 指引讀取與拆解全部來源，提交知識點後形成固定集合。核心保存實際讀取區間；遇到預算限制而未讀完的來源須明確標記，不能當成已完成抽取。
3. 依固定來源順序與 `seed` 決定 QA 嘗試順序，每個知識點獨立處理。當成功且去重後的 QA 達到 `--count`，剩餘知識點標為 `not_attempted_count_limit`，不得當成失敗。
4. 每個來源抽取／知識點 QA 工作項目須有有限的重試、工具呼叫與模型預算。採用既有重試規則；無法沿用的 agent 工作項目最多三次嘗試，工具迴圈另設有限上限並記錄有效值。達到容量限制時須分頁續讀或明確記錄未完成範圍，不可改以核心的隱藏切分方法接管。
5. 候選驗證失敗可回饋給模型修正；耗盡嘗試後標記失敗，繼續其他工作項目。Skill 載入、runtime 初始化或必要工具故障則整體停止，不默默切回原本生成法。
6. 至少有一筆有效 QA 可輸出部分結果，明確標記 `partial`、失敗數與未完成來源範圍；來源仍有未讀內容或抽取未完成時也須標記 partial。單純因 count 截止不算 partial。完全沒有有效 QA 則命令失敗，不發布成功資料集。
7. 所有資料先序列化與驗證再發布，沿用單檔安全寫入機制。Dataset 與 sidecar 的發布順序、清理／復原處理須確保失敗不會留下看似完成但映射不符的結果；既有輸出不可因生成或序列化失敗而損失。

本期不重用先前生成結果；內容雜湊用於追蹤與核對，不能宣稱已支援 cache 或 resume。

## 驗證能保證什麼

核心可確定驗證欄位、ID、原文引用是否存在、引用位置、階段順序及精確重複；這些檢查無法單獨證明 statement 是原文的正確歸納，或 answer 完全受到原文支持。

Skill 指引必須要求答案忠於原文並保留數值範圍、否定與適用條件。語意品質由固定測試案例與實際驗收的人員檢閱確認；第一期不加入獨立 LLM judge，也不承諾自動杜絕所有無根據推論。

只提供 point-to-QA 的處理統計：已接受點數、已嘗試點數、有有效 QA 的點數、對應最終輸出的點數、失敗與 count 截止數。若顯示涵蓋比例，分母為已接受知識點；零分母顯示不適用。這不是整篇文章的事實抽取完整率。

## 驗收條件

| 編號 | 必須可觀察的結果 |
| --- | --- |
| AC1 | 基本命令與三欄輸出保持相容；create CLI help 不再列出 `--chunk-size`、`--overlap`、`--strict`，使用舊旗標時明確報錯，相關測試與使用文件同步調整 |
| AC2 | 本地 `SKILL.md` 可被 Akasha 載入；缺少入口或重複 `--skill` 時清楚報錯 |
| AC3 | 執行證據能顯示 skill 讀取、工具呼叫、抽取完成後才進入 QA 階段 |
| AC4 | 固定三知識點文章在離線測試產生三個有效點與三筆 QA，全部有可定位的原文證據 |
| AC5 | 假引用、未知 ID、額外欄位、越權來源讀取或跨階段提交被拒絕 |
| AC6 | 一個點 QA 失敗時其他點仍可完成；失敗與 count 未嘗試可區分 |
| AC7 | 去重與 count 後，sidecar 保有每筆正式 record 的正確行號與全部來源關聯 |
| AC8 | 正式 JSONL 通過既有 `read_records`，並能由既有 runner 的測試 callback 接收問題、填入回答 |
| AC9 | 已有輸出保護、序列化／發布失敗情境通過測試 |
| AC10 | 至少一次真實 Akasha skill 執行完成，保留脫敏證據並人工核對知識點、問題與答案 |
| AC11 | Skill 路徑未呼叫核心預切分器；能依 skill 指引分頁續讀並跨讀取範圍引用證據，未讀完時明確記錄 partial |

離線測試須提供可記錄 skill 路徑與工具呼叫的 agent 替身，驗證契約與順序。測試替身無法證明真實 Akasha 的 skill 載入成功，因此 AC10 獨立於離線測試，不放入例行 CI，也不以精確問句文字作為成功標準。

真實驗收使用小型、固定、無敏感內容的文章；先確認正確的 skill 被讀取，再確認拆解與 QA 品質。若 provider 或認證未就緒，記錄為驗收未完成，不以離線測試代替成功宣告。

## 實作順序與交付物

1. 核對目前 Akasha skill 載入、支援工具及可觀察執行事件；確認能限制本期不需要的預設能力。
2. 加入可選的本地 skill 入口與來源讀取工具，移除 create 的三個公開旗標（`--chunk-size`、`--overlap`、`--strict`）並同步更新使用說明與測試；無 skill 路徑沿用內部預設值。
3. 撰寫 `knowledge-point-qa/SKILL.md`，完成兩階段提交、證據驗證與來源映射。
4. 加入輸出、sidecar、錯誤與 count 處理，以及離線契約／整合測試。
5. 執行真實 Akasha 驗收，記錄結果與已知限制，提供可重跑的使用範例。

交付物為可執行功能、範例 skill、測試、使用說明及驗收紀錄；本次 PRD 確認前僅交付文件。

## 第一期成功後的路線

1. **Skill 使用**：不建立 list、install、update 或 remove。每個階段只接受一個本地 `--skill DIRECTORY`，入口固定為 `SKILL.md`；未指定時使用隨套件提供的預設 skill。
2. **Run-agent skill**：以 skill 決定選題與重複次數。內建 stability 方法對每題執行三次；隨機抽題方法使用核心提供並保存的 seed。核心驗證 schedule、執行目標 Agent，並保存 responses、trials 與 run sidecar；不保留 `--prompt` 或 `--max-cases`。
3. **Eval skill**：skill 透過受控工具提交包含 boolean `correct` 的固定計畫與逐次 judgment。eval 讀取 trials sidecar，Python 計算聚合與逐題正確率、全部通過及結果一致性；不保留 `--prompt` 或 `--prompt-file`。
4. **Report skill**：skill 只提交受評測事實限制的 overview、findings 與 limitations；核心確定性渲染數值、穩定性表與逐次附錄。
5. 多 skill 組合與 graphify 依實際需求另行定義，合併方式按階段設計，不預先套用相同規則。

每一步都有具體案例與驗收後再推進；本文件僅申請第一期 create 原型的確認。
