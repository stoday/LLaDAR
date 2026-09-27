# PRD：語意知識圖譜與受控變體探針測試集

## 狀態與決策請求

**狀態：已實作餐次熱量 MVP；本文件定義下一階段的通用化重構。**

本文件定義 `lladar create test-dataset --knowledge ...` 的下一代預設生成
流程：從來源文本建立可追溯的知識圖譜，產生一般來源事實題、上位概念的
語意選擇觀察題，以及可選的受控變體不變性題。使用者不需要提供本體、
圖譜檔、題目模板或預設的條件分類法。

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
- 在來源沒有將規則連結至某條件時，模型是否會因只改動該條件的措辭而改變
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
- 對受控變體題測量同一來源規則在一次只變動一個已宣告條件維度時是否出現
  回覆類別差異；產出「回覆差異訊號」而非自動宣告模型有偏見。
- 正式 dataset 每列持續嚴格只有 `question`、`expected_answer`、
  `actual_response`；圖譜、探針意圖、候選實例與統計 join key 全部位於
  sidecar。
- 保持既有 `run-agent` 的題目輸入方式；在 eval 後以 Python 做可重現的分類
  彙總與配對統計。

## 非目標

- 不建立對外查詢的圖資料庫、GraphQL、GraphML 或圖形化編輯器。
- 不要求使用者手動畫圖、命名概念、預先設定領域或群體分類法，或撰寫
  prompt。
- 不將推論出的上位概念陳述成來源原文已明示的事實。
- 不以任何受控條件推論個人特質、資格、風險、能力或應得待遇。
- 不把一次回答、低信心的語意映射，或少量樣本稱為模型偏見。
- 不改寫 `actual_response`、不把評分或族群標籤寫入正式 JSONL，也不移除現有
  自由回答、單選、複選、排序的資料列契約。
- 不把來源未提及的受控條件虛構為來源支持的醫療、營養、社會或其他建議。

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
   受控變體成對探針。
4. 寫入三欄 JSONL、`<dataset>.generation.json` 與
   `<dataset>.graph.json`。
5. 在完成摘要顯示題數、概念群組數、可用探針數與被略過原因。

沒有額外的必填旗標。既有 `--count` 仍限制最終 dataset 記錄數；既有
`--question-type` 仍控制可有唯一答案之一般事實題的作答格式。概念映射與
受控變體探針在本版本一律使用自由回答，因為模型必須自行暴露它選擇的概念，
而非只從顯示選項中挑代號。

受控變體探針預設關閉。它採用已宣告的受控維度，不接受自由 prompt，因為
pair 的兩側必須能被確定地比較。互動選單在第一階段圖譜驗證完成後，列出本次
planner 已驗證可用的維度；非互動腳本只能指定同一 corpus fingerprint 的
既有 graph sidecar 中已列出的穩定 ID，否則失敗而非猜測：

```text
--controlled-variant-probes             在終端顯示編號清單並選擇維度
--controlled-variant-topics DIMENSION_ID[,DIMENSION_ID]  供非互動腳本指定維度
```

兩者不可同時使用；`--controlled-variant-probes` 需要終端輸入。這是一次破壞性
遷移：重構後移除 MVP 的 `--demographic-probes`、`--demographic-topics`、固定
維度清單及其 sidecar 語意，舊產物須重新生成。實際呼叫目標 Agent 仍由既有
`run-agent` 流程控制其執行次數。

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
| `source_condition` | 文件明示的適用條件，例如「65 歲以上」。 | `source` |
| `control_dimension` | 一個可獨立變動、具明確語意範圍與值域的測試條件維度。 | `synthetic_control` |
| `control_value` | 某受控維度的一個值；僅可透過其宣告的維度解讀。 | `synthetic_control` |

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
probe --varies--> control_dimension
control_value --value_of--> control_dimension
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

## 受控變體測試政策

### 來源明示條件

若來源本身明示適用條件，系統將原文條件保存為 `source_condition`，並可生成
直接來源題。例如來源寫「符合條件 C 的 X 建議值為 Y」，題目可測試該條件下
的 Y。條件文字不可被改寫成來源未使用的醫療、身份、群體或其他主張。

### 反事實不變性控制

若一個來源規則沒有連到某個候選控制維度的 `source_condition`，系統可建立
成對的 `synthetic_control` 題目。每個控制維度必須先由第二階段 planner 提出，
再經核心驗證；它至少須宣告：

- 穩定 `dimension_id`、人類可讀標籤、語意範圍與候選值；
- 值是否互斥、可否與哪些其他維度共存，以及未知時採取的保守行為；
- 維度值是來源條件或測試加入的 `synthetic_control`；
- 可安全套用的 plan 與題目骨架，或明確的略過原因。

系統不得因標籤名稱相近、自然語言常被並列，便推定兩個條件屬於同一維度、
互斥、可彼此替代或可共同出現在同一題。維度關係未明確宣告時，planner 不得
產生可能同時改動多個語意條件的 pair。

每對題目只變動一個已宣告的控制維度；問題主詞、來源條件、措辭骨架、候選
圖譜事實與順序都必須相同。這些值是產品為測試而加入的文字控制，並非來源
聲稱的事實。sidecar 必須標記：

```json
{
  "origin": "synthetic_control",
  "pair_id": "cv_000021",
  "varied_dimension": "dimension_id",
  "control_value": "value_a",
  "dimension_semantic_scope": "declared scope",
  "answer_contract": "invariant"
}
```

沒有可回答的來源規則、問題本身會涉及個人資格／風險／待遇、無法在不暗示
刻板印象或來源外主張下插入控制條件、或維度語意／相容性未能驗證時，不建立
此類 probe。未指定受控維度時不建立 synthetic controls，但不移除來源原本
明示的條件題。

### 解讀規則

- 回覆類別一致，表示在本測試的可分類範圍內未觀察到差異；不表示模型無偏見。
- 回覆類別不同，表示存在可重現的偏差訊號；不表示差異必然不合理或模型具有
  某種動機。
- 若來源確實使該控制維度相關，該 pair 不屬於不變性測試，必須改列為
  source-conditional 直接題。
- 報表必須同時顯示樣本數、未映射數、執行錯誤與每一維度的差異分布；它不得
  將單一維度的差異外推成其他未測維度的結論。

## 生成流程

```text
支援的來源檔
  → LlamaIndex／LLM 提出的通用圖譜候選
  → 核心驗證的原子事實與條件圖
  → 第二階段 LLM 提出的概念／受控變體 plans 與自然題目
  → 核心驗證、materialize 與穩定排序
  → 三欄 JSONL + graph/generation sidecars
  → run-agent 回覆
  → 回覆分類
  → Python 配對與分布統計
```

### 1. 第一階段：來源與通用圖譜

LlamaIndex 負責受控讀取來源，第一階段 LLM 從全文提出通用圖譜候選：原子
事實、實體、關係、條件、概念候選與逐字 evidence refs。它不得依賴餐次、
人口統計或任何其他特定領域的內建規則。任何資料夾、檔案、遠端內容或模型
回覆都只視為資料，不是指令。

核心沿用既有的受控閱讀、逐字 quote 驗證、來源覆蓋與重試語意。每個接受的
圖譜項目至少有來源 ID、source-relative location 與一段可在來源中重現的
quote；不能驗證的候選不得進入已驗證圖譜或下一階段。

### 2. 核心圖譜驗證

核心將第一階段候選正規化為原子事實、條件與穩定 entity ID。模型若需要協助
正規化或提出 concept，只能透過受限工具讀取已驗證項目並提交既有 ID；核心
驗證節點、邊、關係、單位、來源證據與 evidence class 後才寫入 graph。

### 3. 第二階段：語意測試規劃

第二階段 LLM 只能讀取已驗證圖譜，提出下列 plan 候選及其自然語言題目。它
負責判斷哪些關係適合形成概念題或受控變體題，並可提出控制維度候選；它不得
宣稱來源外規則，也不得讓未宣告、未驗證的控制維度進入 plan。核心驗證候選後
才指派穩定 ID、去重、排序與
materialize；因此 Python 不決定領域關係或題型，只負責證據、完整性、雜湊與
確定性統計。

| Plan 類型 | 前提 | `expected_answer` 語意 |
| --- | --- | --- |
| `direct_fact` | 一個有完整來源支持的事實。 | 唯一來源答案。 |
| `concept_mapping` | 至少兩個同位階成員、共同關係、可區別值。 | 列出完整候選參考集合；非唯一答案。 |
| `source_conditional` | 來源明示的適用條件。 | 該條件下的唯一來源答案。 |
| `controlled_invariance` | 規則沒有相關來源條件，且可安全建立已宣告維度的控制 pair。 | 同一候選參考集合；以 pair 類別比較。 |

每個受控變體 plan 至少含 `source_concept`、`source_support`、
`answer_contract`、`varied_dimension`、`control_value`、`pair_id` 及
`question_template`。`answer_contract: invariant` 表示各變體共用相同的來源
支持答案集合；它不是對真實世界中的條件關係作出主張。

`--count` 在 plan 去重與驗證後才套用。若必須截斷，pair 要麼完整保留，要麼
完整略過；不得只留下其中一側。

### 4. plan 與題目驗證

核心驗證每個候選 plan 的實體、數值、單位、條件、候選集合與 evidence refs
都只引用已驗證圖譜，且每個 pair 僅變動一個已宣告維度。它也驗證自然題目
符合 plan，並不得：

- 改變 plan 的實體、數值、單位、條件、候選集合或 pair ID；
- 將 `inferred` concept 寫成「來源規定」；
- 將 synthetic control 寫成來源中的事實；
- 在題目中透露預期映射、分類、偏見標籤或評分規則。

無法通過的候選最多要求第二階段 LLM 修正三次，仍失敗則略過 plan 並保存原因。

## 資料契約與產物

### 正式 JSONL：維持三欄

```json
{
  "question": "一份正餐建議攝取多少大卡？",
  "expected_answer": "早餐：400 kcal；午餐：650 kcal；晚餐：700 kcal",
  "actual_response": null
}
```

對 `concept_mapping` 與 `controlled_invariance`，`expected_answer` 是完整來源
候選參考集合，不是假裝唯一正確答案。目標 Agent 仍只收到 `question`；一般
dataset reader 仍可讀三欄資料。直接事實題及既有單選／複選／排序題維持其既有
canonical `expected_answer` 協定。

### generation sidecar v5

`<dataset>.jsonl.generation.json` 使用 `schema_version: 5`，至少保存：

本次重構不保留舊 sidecar 的讀取相容路徑。版本不符、雜湊不符或欄位不完整時，
`run-agent` 不帶入題型／probe 契約，`eval` 也不推測其語意。

- dataset SHA-256、每列 fingerprint、生成模型與已載入 skill 摘要；
- plan ID、plan type、圖譜／來源節點 ID、evidence refs 與 origin classes；
- concept mapping 的成員、候選答案、回答分類規則與 `concept_origin`；
- controlled pair ID、兩側 record fingerprints、變動維度、控制值、維度語意
  範圍／相容性聲明與 `answer_contract`；
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

`run-agent` 讀到有效 v5 generation sidecar 時，將最小、驗證過的 probe
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
`concept_mapping` 與 `controlled_invariance` 改用 `mapping_outcome` 與
`pair_outcome`，不得被併入答對率分母。

### 報表

report 新增以下區塊：

| 區塊 | 必含內容 |
| --- | --- |
| 來源基準題 | 題數、可評測數、正確率；不與 probe 混算。 |
| 概念映射 | 每個 concept 的成員分布、synthesized／external／unmapped 數、實際 trial 數。 |
| 受控變體不變性 | 每個維度的完整 pair 數、兩側可映射數、相同／不同／不可判定數。 |
| 限制 | concept 是否 inferred、樣本數、未映射率、試次數、來源與 synthetic control 的區別。 |

可接受的敘述是「在 18 個可映射回覆中，12 個映射至晚餐」或「維度 D 的控制
pair 中有 3/10 類別不同」。不可接受的敘述是「模型偏見為 30%」或「模型認為
某群體應得到不同待遇」。

## 資料契約

- 輸出 JSONL、`read_records`、現有 Agent target adapter 的三欄契約不變。
- 缺少或無效 generation sidecar 的 dataset／responses 仍可作一般問答評測，
  但不具題型或 probe 統計語意。
- 現有 `--question-type` 的 free、single-choice、multiple-choice、ranking
  規則保留；圖譜 probe MVP 強制 free answer，不從回覆文字猜題型。
- `--skill DIRECTORY` 仍可替換生成方法；選定 skill 必須實作 v5 所需的受控
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
- synthetic control 必須標示 origin，且只變一個已宣告、語意及相容性均已驗證
  的控制維度。
- 任何 source-conditional 規則不得被當成 invariance pair；反之亦然。
- pair 截斷、去重與發布必須具原子性；不得產生孤立的一側。
- 來源、skill、dataset 和 sidecar 的 hash 必須在 generation → run → eval
  lineage 中核對；失效資料只可走一般評測。
- 模型和來源文本均視為不受信任資料；來源不能改寫 tool scope、schema、
  evidence policy 或輸出路徑。

## 驗收條件

| ID | 可觀察結果 |
| --- | --- |
| AC1 | 不帶新增旗標的 `--knowledge` 指令生成有效三欄 JSONL、v5 generation sidecar 與 graph sidecar。 |
| AC2 | 每個 `source` 節點／邊都有可定位的逐字來源 evidence；無 evidence 的模型輸出被拒絕。 |
| AC3 | 一組有共同關係且至少兩個不同值的同位階實例可產生 `concept_mapping`；單一事實、混合單位或條件衝突的集合被略過。 |
| AC4 | inferred concept 在 graph、generation sidecar 與 report 均被標示為 inferred，且不被呈現為來源明示定義。 |
| AC5 | concept probe 的評測結果為候選映射／synthesized／external／unmapped，不進入答對率。 |
| AC6 | 來源明示適用條件時，生成 source-conditional 題；沒有該條件時，符合安全門檻者可生成只變一個已宣告維度的 synthetic_control pair。 |
| AC7 | 預設不建立 synthetic pairs；使用者可透過互動清單或 `--controlled-variant-topics` 選定已驗證維度，且不刪除來源原本的條件事實。 |
| AC8 | `--count` 截斷時不產生不完整 pair；重跑相同輸入、seed 與 fixtures 有穩定 plan／pair 選取。 |
| AC9 | 有效 lineage 時，run snapshot 與 eval 可連結 plan／pair；hash 不符時不猜測 graph metadata 且既有 eval 不失敗。 |
| AC10 | 分類後的所有分布與 pair 統計由 Python 決定性彙總，報表分開顯示 direct correctness 和 probe outcomes。 |
| AC11 | 離線測試至少覆蓋通用同位階概念、混合單位略過、來源明示條件、無來源關聯的單維控制 pair、未知相容性略過、孤立 pair 防護、sidecar 篡改與低信心 unmapped。 |
| AC12 | CLI help、英文與繁體中文文件包含一個可複製的最簡命令、成本說明、限制與不將偏差訊號誤稱偏見的示例。 |

## 建議交付順序

1. 以測試鎖住通用圖譜候選、evidence 驗證、維度宣告及 graph sidecar v5 schema。
2. 導入 LlamaIndex 受控讀取與第一階段通用圖譜候選；拒絕不具逐字證據的項目。
3. 導入第二階段 plan／自然題目生成與核心驗證；完成 `direct_fact`、
   `concept_mapping`、`source_conditional` 及 `controlled_invariance` 的三欄 flattening。
4. 完成單維 pair 原子性、未知相容性略過、回覆分類與不變性報表。
5. 擴充 generation → run → eval lineage，移除 MVP 固定維度／舊旗標，更新 CLI help、雙語文件，並以小型
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
和「模型把上位概念映射到哪個實例」分開呈現。若同一來源規則與某已宣告控制
維度沒有關聯，受控 pair 的差異只會列為需要檢視的回覆差異訊號，不會被自動
定義為偏見。
