# PRD：語意知識圖譜與人口統計探針測試集

## 狀態與決策請求

**狀態：已實作 MVP；以目前支援的餐次熱量同位階事實為範圍。**

本文件定義 `lladar create test-dataset --knowledge ...` 的下一代預設生成
流程：從來源文本建立可追溯的知識圖譜，產生一般來源事實題、上位概念的
語意選擇觀察題，以及性別與年齡等人口統計反事實不變性題。使用者不需要
提供本體、圖譜檔、題目模板或人口統計設定。

本文件**取代方向**為目前 `docs/PRD-graph-test-dataset.zh-TW.md` 所描述的
選用 `--strategy graph` 產品介面；該舊文件保留作歷史設計參考，不得與本
文件的預設行為、sidecar 版本或評測語意混合實作。既有三欄 JSONL 契約仍
不變。

程式、聚焦測試、CLI help 與雙語使用文件已隨 MVP 更新；provider-backed
端對端驗收仍須由具備有效 provider 設定的環境執行。

## 問題

目前的流程會從來源中擷取可回答的知識點，然後將每個知識點寫成一題。
這能測試直接事實，卻無法回答下列問題：

- 一組同位階實例能否被模型歸到同一個上位概念？
- 當「正餐」可指早餐、午餐或晚餐，模型會把它映射到哪一個實例？
- 在來源沒有將規則連結至性別或年齡時，模型是否會因這些無關條件而改變
  回答？

若把這些問題硬塞進一個唯一 `expected_answer`，系統會把本來有意設計的
歧義錯當作答錯，也會把單次模型輸出誤稱為偏見。因此生成、分類、配對與
統計必須在同一條可追溯資料線上完成。

## 產品目標

- 使用者仍只需要提供 `--knowledge`；不必學習圖譜、本體、schema 或人口
  統計測試方法。
- 先將來源事實建成知識圖譜，再由圖譜規劃測試；LLM 只負責受控擷取、上位
  概念候選與題目措辭，不能自行補來源外知識。
- 明確區分 `source`（來源明示）、`inferred`（由來源實例歸納）與
  `synthetic_control`（產品建立的反事實控制條件）。
- 對上位概念題觀察模型選擇，而非假裝存在唯一正解。
- 對人口統計題測量同一來源規則在一次只變動一個條件時是否出現回覆類別
  差異；產出「偏差訊號」而非自動宣告模型有偏見。
- 正式 dataset 每列持續嚴格只有 `question`、`expected_answer`、
  `actual_response`；圖譜、探針意圖、候選實例與統計 join key 全部位於
  sidecar。
- 保持既有 `run-agent` 的題目輸入方式；在 eval 後以 Python 做可重現的分類
  彙總與配對統計。

## 非目標

- 不建立對外查詢的圖資料庫、GraphQL、GraphML 或圖形化編輯器。
- 不要求使用者手動畫圖、命名概念、設定年齡分箱、挑選性別值或撰寫 prompt。
- 不將推論出的上位概念陳述成來源原文已明示的事實。
- 不以人口統計變數推論個人特質、資格、風險、能力或應得待遇。
- 不把一次回答、低信心的語意映射，或少量樣本稱為模型偏見。
- 不改寫 `actual_response`、不把評分或族群標籤寫入正式 JSONL，也不移除現有
  自由回答、單選、複選、排序的資料列契約。
- 不把來源未提及的人口統計規則虛構為來源支持的醫療、營養或社會建議。

## 最簡使用者體驗

唯一必要命令不變：

```powershell
.\.venv\Scripts\lladar.exe create test-dataset `
  --knowledge .\knowledge `
  --output .\artifacts
```

系統預設完成下列工作：

1. 讀取所有支援的來源文本並抽取附逐字證據的事實。
2. 建立知識圖譜與可審核的上位概念候選。
3. 輸出來源事實基準題與可觀察概念映射的探針；只有使用者明確選擇時才加入
   受控人口統計成對探針。
4. 寫入三欄 JSONL、`<dataset>.generation.json` 與
   `<dataset>.graph.json`。
5. 在完成摘要顯示題數、概念群組數、可用探針數與被略過原因。

沒有額外的必填旗標。既有 `--count` 仍限制最終 dataset 記錄數；既有
`--question-type` 仍控制可有唯一答案之一般事實題的作答格式。概念映射與
人口統計探針在 MVP 一律使用自由回答，因為模型必須自行暴露它選擇的概念，
而非只從顯示選項中挑代號。

人口統計探針預設關閉。它採用受控維度，不接受自由 prompt，因為 pair 的兩側
必須能被確定地比較。使用者可選擇最適合情境的已定義維度：

```text
--demographic-probes             在終端顯示編號清單並選擇維度
--demographic-topics age,nationality  供非互動腳本以逗號指定維度
```

兩者不可同時使用；`--demographic-probes` 需要終端輸入。實際呼叫目標 Agent
仍由既有 `run-agent` 流程控制其執行次數。

## 例子：正餐概念的語意選擇

假設來源明示：

```text
早餐建議 400 大卡。
午餐建議 650 大卡。
晚餐建議 700 大卡。
```

圖譜保留三個來源事實，並可建立下列**推論**概念：

```text
早餐 ─┐
午餐 ─┼─ is_a → 正餐（origin=inferred）
晚餐 ─┘

早餐 → 建議熱量 → 400 kcal（origin=source）
午餐 → 建議熱量 → 650 kcal（origin=source）
晚餐 → 建議熱量 → 700 kcal（origin=source）
```

系統可生成：

- 基準題：`午餐建議攝取多少大卡？`，唯一來源答案是 `650 kcal`。
- 概念探針：`一份正餐建議攝取多少大卡？`，參考集合是早餐、午餐、晚餐的
  三個來源值，**沒有唯一正解**。

概念探針的 eval 結果不是 `correct` 或 `incorrect`，而是
`maps_to: breakfast`、`maps_to: lunch`、`maps_to: dinner`、`synthesized`、
`external_or_unsupported` 或 `unmapped`。報表可以據此說明「在 12 個可分類
回覆中，模型 7 次把正餐映射為晚餐」，但不得說來源規定正餐等於晚餐，也
不得僅憑此結論宣稱偏見。

如果來源沒有足以支持至少兩個同位階實例、共同關係和可區別回答值，系統不
建立概念探針；它只保留可成立的直接事實題並記錄略過原因。

## 領域模型與證據等級

### 節點

圖譜是生成器內部的 deep module；對外介面仍是 `--knowledge`。其內部節點
至少有：

| 節點類型 | 用途 | 允許的來源 |
| --- | --- | --- |
| `entity` | 可被辨識的項目，例如早餐、方案 A。 | `source` |
| `fact` | 原子化的主詞－關係－值敘述。 | `source` |
| `concept` | 包含兩個以上同位階實例的上位概念，例如正餐。 | `source` 或 `inferred` |
| `attribute` | 可比較的關係／單位，例如建議熱量、保存天數。 | `source` |
| `source_condition` | 文件明示的適用條件，例如「65 歲以上女性」。 | `source` |
| `demographic_control` | 為反事實測試建立的受控條件。 | `synthetic_control` |

每個來源節點與邊必須有至少一段逐字 quote、來源 ID、字元範圍與檔案摘要。
任何沒有來源引用的項目不能成為 `source`。

### 邊

MVP 支援最小而足夠的關係集合：

```text
subject --has_value--> value
subject --qualified_by--> source_condition
entity --is_a--> concept
concept --has_member--> entity
fact --measures--> attribute
probe --varies--> demographic_control
```

`is_a`／`has_member` 必須保存 `origin`。若來源只有早餐、午餐、晚餐等實例，
而沒有出現「正餐」一詞，概念可以被建立為 `inferred`，但其名稱、成員與
推論依據均須出現在 graph sidecar，且題目／報表不可暗示這是原文直接陳述。

### 上位概念的最低條件

一個 `inferred` concept 只有同時滿足下列條件才可建立：

- 至少兩個來源實例；
- 所有成員可連到同一個可比較關係和答案型別；
- 成員的來源 topic／限定條件不互相衝突；
- 推論模型回傳成員、概念名稱及理由，但核心只能接受既有 entity ID；
- 任一成員都不因建立概念而失去原本的來源事實或條件。

概念成員資格是非排他的；同一實例可屬於多個概念。核心以穩定順序與固定
seed 選取可用群組，避免語言模型的回傳順序決定結果。

## 人口統計測試政策

### 來源明示條件

若來源本身明示性別、年齡或其他適用條件，系統將原文條件保存為
`source_condition`，並可生成直接來源題。例如來源寫「65 歲以上女性的 X
建議值為 Y」，題目可測試該條件下的 Y。條件文字不可被改寫成來源未使用的
醫療、身份或族群主張。

### 反事實不變性控制

若一個來源規則沒有 `source_condition` 連到性別或年齡，系統可建立成對的
`synthetic_control` 題目，例如只改變：

```text
性別控制：woman ↔ man
年齡控制：25 ↔ 65
```

每對題目只變動一個控制變數；問題主詞、來源條件、措辭骨架、候選圖譜事實
與順序都必須相同。這些值是產品為測試而加入的文字控制，並非來源聲稱的人
口統計事實。sidecar 必須標記：

```json
{
  "origin": "synthetic_control",
  "pair_id": "dp_000021",
  "varied_dimension": "age",
  "control_value": 25,
  "expected_relation": "invariant"
}
```

沒有可回答的來源規則、問題本身會涉及個人資格／風險／待遇、或無法在不暗示
刻板印象下插入控制條件時，不建立此類 probe。未指定人口統計維度時不建立
synthetic controls，但不移除來源原本明示的人口統計條件題。

### 解讀規則

- 回覆類別一致，表示在本測試的可分類範圍內未觀察到差異；不表示模型無偏見。
- 回覆類別不同，表示存在可重現的偏差訊號；不表示差異必然不合理或模型具有
  某種動機。
- 若來源確實使該人口統計條件相關，該 pair 不屬於不變性測試，必須改列為
  source-conditional 直接題。
- 報表必須同時顯示樣本數、未映射數、執行錯誤與每一維度的差異分布。

## 生成流程

```text
支援的來源檔
  → 已驗證 knowledge points
  → 原子事實與條件圖
  → 概念候選與驗證
  → 穩定的 test plans
  → LLM 只渲染題目文字
  → 三欄 JSONL + graph/generation sidecars
  → run-agent 回覆
  → 回覆分類
  → Python 配對與分布統計
```

### 1. 來源與事實

沿用現有 `knowledge-point-qa` 的受控閱讀、逐字 quote 驗證、來源覆蓋與重試
語意。圖譜建構只能消費已驗證 knowledge points，不能重新直接相信模型對原文
的自由摘要。

### 2. 圖譜建構

核心把知識點轉成原子事實、條件與 entity ID。模型若需要協助正規化或提出
concept，只能透過受限工具讀取已驗證點並提交既有 ID；核心驗證節點、邊、
關係、單位、來源證據與 evidence class 後才寫入 graph。

### 3. 測試規劃

規劃器在 LLM 題目措辭之前，以穩定順序產生以下 plan：

| Plan 類型 | 前提 | `expected_answer` 語意 |
| --- | --- | --- |
| `direct_fact` | 一個有完整來源支持的事實。 | 唯一來源答案。 |
| `concept_mapping` | 至少兩個同位階成員、共同關係、可區別值。 | 列出完整候選參考集合；非唯一答案。 |
| `source_conditional` | 來源明示人口統計或其他條件。 | 該條件下的唯一來源答案。 |
| `demographic_invariance` | 規則沒有相關來源人口統計條件，且可安全建立控制 pair。 | 同一候選參考集合；以 pair 類別比較。 |

`--count` 在 plan 去重與驗證後才套用。若必須截斷，pair 要麼完整保留，要麼
完整略過；不得只留下其中一側。

### 4. 題目措辭

LLM 接收不可變的 plan、來源語言與必要作答說明，只能產生題目文字。它不得：

- 改變 plan 的實體、數值、單位、條件、候選集合或 pair ID；
- 將 `inferred` concept 寫成「來源規定」；
- 將 synthetic control 寫成來源中的人口統計事實；
- 在題目中透露預期映射、分類、偏見標籤或評分規則。

核心檢查題目包含 plan 所需的主詞、關係與控制條件，且不含不支援的答案。無法
通過的措辭最多重試三次，仍失敗則略過 plan 並保存原因。

## 資料契約與產物

### 正式 JSONL：維持三欄

```json
{
  "question": "一份正餐建議攝取多少大卡？",
  "expected_answer": "早餐：400 kcal；午餐：650 kcal；晚餐：700 kcal",
  "actual_response": null
}
```

對 `concept_mapping` 與 `demographic_invariance`，`expected_answer` 是完整來源
候選參考集合，不是假裝唯一正確答案。目標 Agent 仍只收到 `question`；一般
dataset reader 仍可讀三欄資料。直接事實題及既有單選／複選／排序題維持其既有
canonical `expected_answer` 協定。

### generation sidecar v4

`<dataset>.jsonl.generation.json` 使用 `schema_version: 4`，至少保存：

本 MVP 不保留舊 sidecar 的讀取相容路徑。版本不符、雜湊不符或欄位不完整時，
`run-agent` 不帶入題型／probe 契約，`eval` 也不推測其語意。

- dataset SHA-256、每列 fingerprint、生成模型與已載入 skill 摘要；
- plan ID、plan type、圖譜／來源節點 ID、evidence refs 與 origin classes；
- concept mapping 的成員、候選答案、回答分類規則與 `concept_origin`；
- demographic pair ID、兩側 record fingerprints、變動維度、控制值與
  `expected_relation`；
- 略過 plan、驗證失敗與計數；
- 既有題型化資料的 question type contract。

缺少 dataset hash、record fingerprint 或必要 plan mapping 的 sidecar 視為失效，
不得用於題型或 probe 統計。

### graph sidecar

`<dataset>.jsonl.graph.json` 是可審核的圖譜產物，至少含：

```json
{
  "schema_version": 1,
  "corpus_sha256": "…",
  "nodes": [{"id": "entity_lunch", "type": "entity", "origin": "source"}],
  "edges": [{"from": "entity_lunch", "relation": "is_a", "to": "concept_meal", "origin": "inferred"}],
  "evidence": [{"ref": "ev_001", "source_id": "source_001", "quote": "…"}]
}
```

此檔是產生與審核證據，不是使用者需要手改的設定。它可能包含來源引文與本機
路徑，必須與來源文本採相同的存取、分享與刪除保護。

## 執行、評測與報表

### Run

`run-agent` 讀到有效 v4 generation sidecar 時，將最小、驗證過的 probe
contract snapshot 寫入 `<responses>.run.json`。snapshot 只保留 record
fingerprint、plan type、候選 ID／canonical values、pair ID 和分類規則；不
複製整份來源文本或圖譜。資料集或 sidecar hash 不一致時不帶 probe contract，
但不阻止既有一般 run。

### 回覆分類

分類器先執行決定性比對：選項 ID、唯一數值、明確實體名稱與 canonical aliases。
其餘自然語言回覆才交由既有 evaluation skill 映射到受限分類集合：

```text
maps_to:<member_id>
synthesized
external_or_unsupported
unmapped
```

evaluation skill 必須提交分類與理由，不得創造 member ID 或改寫原始回答。
`actual_response` 永遠原樣保存。所有分類結果都保留分類方法
(`deterministic` 或 `skill`)、信心與理由；低信心或失敗分類保留為 `unmapped`。

### 確定性統計

回覆一旦被映射，所有計數、分母、pair join、差異率、試次分布和報表表格都由
Python 決定性計算。不得把聚合算術交給 evaluation 或 reporting Agent。

`direct_fact` 與既有 typed records 保留 `correct`／`incorrect` 指標。
`concept_mapping` 與 `demographic_invariance` 改用 `mapping_outcome` 與
`pair_outcome`，不得被併入答對率分母。

### 報表

report 新增以下區塊：

| 區塊 | 必含內容 |
| --- | --- |
| 來源基準題 | 題數、可評測數、正確率；不與 probe 混算。 |
| 概念映射 | 每個 concept 的成員分布、synthesized／external／unmapped 數、實際 trial 數。 |
| 人口統計不變性 | 每個維度的完整 pair 數、兩側可映射數、相同／不同／不可判定數。 |
| 限制 | concept 是否 inferred、樣本數、未映射率、試次數、來源與 synthetic control 的區別。 |

可接受的敘述是「在 18 個可映射回覆中，12 個映射至晚餐」或「年齡控制 pair
中有 3/10 類別不同」。不可接受的敘述是「模型偏見為 30%」或「模型認為某
族群應得到不同待遇」。

## 資料契約

- 輸出 JSONL、`read_records`、現有 Agent target adapter 的三欄契約不變。
- 缺少或無效 generation sidecar 的 dataset／responses 仍可作一般問答評測，
  但不具題型或 probe 統計語意。
- 現有 `--question-type` 的 free、single-choice、multiple-choice、ranking
  規則保留；圖譜 probe MVP 強制 free answer，不從回覆文字猜題型。
- `--skill DIRECTORY` 仍可替換生成方法；選定 skill 必須實作 v4 所需的受控
  graph submission 契約，否則 generation 在開始 model work 前失敗，不得退回
  舊流程。
- 這是預設生成邏輯的重大改動：release note、CLI help 與雙語文件必須明示
  direct facts、inferred concepts、synthetic controls 的差異與額外 probe trial
  成本。

## 品質與安全閘門

- 每條 `source` 邊均須能回溯到來源 quote；每條 `inferred` 邊均須保存成員、
  理由與支援成員的 evidence refs。
- 沒有至少兩個有效同位階成員時，不產生 concept mapping。
- 成員的關係、答案型別、單位或必要條件不一致時，不產生 concept mapping。
- synthetic control 必須標示 origin，且只變一個人口統計維度。
- 任何 source-conditional 規則不得被當成 invariance pair；反之亦然。
- pair 截斷、去重與發布必須具原子性；不得產生孤立的一側。
- 來源、skill、dataset 和 sidecar 的 hash 必須在 generation → run → eval
  lineage 中核對；失效資料只可走一般評測。
- 模型和來源文本均視為不受信任資料；來源不能改寫 tool scope、schema、
  evidence policy 或輸出路徑。

## 驗收條件

| ID | 可觀察結果 |
| --- | --- |
| AC1 | 不帶新增旗標的 `--knowledge` 指令生成有效三欄 JSONL、v4 generation sidecar 與 graph sidecar。 |
| AC2 | 每個 `source` 節點／邊都有可定位的逐字來源 evidence；無 evidence 的模型輸出被拒絕。 |
| AC3 | 一組有共同關係且至少兩個不同值的同位階實例可產生 `concept_mapping`；單一事實、混合單位或條件衝突的集合被略過。 |
| AC4 | inferred concept 在 graph、generation sidecar 與 report 均被標示為 inferred，且不被呈現為來源明示定義。 |
| AC5 | concept probe 的評測結果為候選映射／synthesized／external／unmapped，不進入答對率。 |
| AC6 | 來源明示人口統計條件時，生成 source-conditional 題；沒有該條件時，符合安全門檻者可生成只變一維的 synthetic_control pair。 |
| AC7 | 預設不建立 synthetic pairs；使用者可透過互動清單或 `--demographic-topics` 選定受控維度，且不刪除來源原本的條件事實。 |
| AC8 | `--count` 截斷時不產生不完整 pair；重跑相同輸入、seed 與 fixtures 有穩定 plan／pair 選取。 |
| AC9 | 有效 lineage 時，run snapshot 與 eval 可連結 plan／pair；hash 不符時不猜測 graph metadata 且既有 eval 不失敗。 |
| AC10 | 分類後的所有分布與 pair 統計由 Python 決定性彙總，報表分開顯示 direct correctness 和 probe outcomes。 |
| AC11 | 離線測試至少覆蓋早餐／午餐／晚餐上位概念、混合單位略過、來源明示年齡條件、不相關年齡控制 pair、孤立 pair 防護、sidecar 篡改與低信心 unmapped。 |
| AC12 | CLI help、英文與繁體中文文件包含一個可複製的最簡命令、成本說明、限制與不將偏差訊號誤稱偏見的示例。 |

## 建議交付順序

1. 建立 graph domain module、source／inferred／synthetic evidence classes、固定離線
   fixtures 及 graph sidecar schema；先以測試鎖住 evidence 與概念最低條件。
2. 將既有已驗證 knowledge points 轉成原子圖譜，加入受控 concept submission、
   穩定 planner 與三欄 flattening。
3. 實作 `direct_fact`、`concept_mapping` plan 與回覆分類；先完成 report 的概念
   分布，不加入人口統計控制。
4. 加入 source-conditional 與 synthetic demographic pair planner、pair 原子性和
   不變性報表。
5. 擴充 generation → run → eval lineage，更新 CLI help、雙語文件，並以小型
   無敏感內容 fixture 進行 provider-backed acceptance；離線 fake provider 測試不
   可被描述為真實模型偏好證據。

## 核准後的執行與解說範例

功能完成後，最簡使用方式為：

```powershell
.\.venv\Scripts\lladar.exe create test-dataset `
  --knowledge .\example_project\DIET-v1.md `
  --output .\artifacts
```

如果來源能形成「早餐／午餐／晚餐」這類同位階集合，dataset 會同時有可驗證的
直接題與一題概念映射 probe。執行目標 Agent 後，report 會將「回答是否正確」
和「模型把上位概念映射到哪個實例」分開呈現。若同一來源規則與年齡、性別沒有
關聯，人口統計 pair 的差異只會列為需要檢視的偏差訊號，不會被自動定義為偏見。
