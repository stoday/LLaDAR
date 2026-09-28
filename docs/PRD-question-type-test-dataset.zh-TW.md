# PRD：來源約束的題型化問答測試集

## 狀態與決策請求

**狀態：已實作 MVP；目前以來源驗證與固定題型契約為範圍。**

本文件請求確認下列 MVP 範圍：LLaDAR 在既有自由回答資料集以外，可從確實具備可比較結構的來源內容生成單選、複選與排序題；正式 JSONL 仍嚴格維持三欄。題型在生成時決定並由 sidecar 保存，評測與報表只能讀取已驗證的題型契約，不能從 Agent 回答文字猜測題型。

程式、測試、CLI help 與雙語使用文件已隨 MVP 更新；真實 provider 驗收仍須由
具備有效 provider 設定的環境執行。

## 問題

現行 `create test-dataset` 會從來源知識點產生自由回答的 `question` 與 `expected_answer`。有些來源包含方案、條件、步驟或明示優先序，適合測試 Agent 是否能從有限選項中作出正確選擇；但很多來源只有一項獨立事實，例如「保存期限為 30 天」。

若系統對後者憑常識編造干擾選項，選擇題看似完整，實際卻不再是來源支持的測試。因此「有答案」不等於「可生成選擇題」。

## 目標

- 以單一、易懂的 CLI 偏好選擇題型；使用者不需指定選項數、干擾項、評分 prompt 或 skill 路徑。
- 保持正式 dataset 每列精確為 `question`、`expected_answer`、`actual_response`。
- 只在來源提供同一問題、同一關係、同一比較軸的完整候選集合時，生成選擇或排序題。
- 題型、選項、正確代號、來源證據與評測協定由 generation sidecar 保存並可驗證地傳遞到 run、eval 與 report。
- 選擇／排序題採核心可重現的格式與答案比對；自由回答仍使用既有評測 skill。
- 在 report 顯示按題型的可用數、執行數、格式合格數與答對數，不以推測的題型製造統計。

## 非目標

- 不改變 JSONL 三欄資料契約，也不在 `actual_response` 寫入分數、代號或題型。
- 不以模型常識或來源外資料製造「看似合理」的錯誤選項。
- 不從 `B`、`A,C` 或自然語言回答反推題型。
- 不在 MVP 支援排序同分、部分排序、可自訂選項數、可自訂計分公式，或使用者自訂題型 schema。
- 不改變現有自由回答的預設行為、`run-agent` 目標整合方式，或現有自訂 `--skill DIRECTORY` 的語意。
- 不宣稱選項格式測試能取代語意、事實完整性或人工檢閱。

## 使用者介面

新增 `create test-dataset` 的一個選項：

```text
--question-type {free,auto,single-choice,multiple-choice,ranking}
```

| 值 | 行為 |
| --- | --- |
| `free`（預設） | 維持目前自由回答產生方式與相容性。 |
| `auto` | 每個候選集合以固定優先序選擇最受來源支持的題型；無合格集合時產生自由回答。 |
| `single-choice` | 只輸出有且只有一個正確選項的合格單選題。 |
| `multiple-choice` | 只輸出有兩個以上正確選項、且可完整列舉的合格複選題。 |
| `ranking` | 只輸出有明示排序依據與唯一完整順序的合格排序題。 |

選用 `auto` 時，系統可以混合題型，並在 stderr 的完成摘要顯示各題型數量。選用特定題型時，系統持續掃描候選集合直到達到 `--count` 上限或來源耗盡；不合格候選不消耗 `--count`。來源耗盡而未達上限不是靜默降級，摘要必須顯示實際輸出數與依類別彙總的略過原因。

`auto` 的固定優先序為 `ranking`、`multiple-choice`、`single-choice`、`free`。只有候選符合上一節的完整可行性條件才可進入該題型；相同輸入、seed 與已驗證候選集合必須選到相同題型。此優先序是輸出形狀的規則，不表示排序題的品質天然高於其他題型。

`--question-type` 不使用 `--format` 名稱，避免與資料檔格式或已移除的歷史輸出格式選項混淆。

## 來源模型與生成規則

### 候選集合

核心在知識點抽取完成後，建立只供生成器使用的候選集合。每一集合必須明確保存：

- 問題的主詞、關係與必要條件；
- 相同關係下的候選答案；
- 每一候選答案的來源證據；
- 若為排序題，明示的比較軸與方向。

同一 `topic` 不足以構成集合。例如「費用」與「有效期限」即使位於同一方案段落，也不可混成同一組選項。候選必須對同一個問題有可比較的答案形狀與單位。

### 題型可行性

| 題型 | 最低可行條件 | 回覆協定 |
| --- | --- | --- |
| `free` | 一項可獨立回答且受來源支持的事實。 | 一般自然語言答案。 |
| `single-choice` | 2–5 個同位階選項；恰一個正確；每個選項都由來源支持。 | 一個選項 ID，例如 `B`。 |
| `multiple-choice` | 3–6 個同位階選項；至少兩個正確；正確集合可完整列舉。 | 以逗號遞增列出 ID，例如 `A,C`。 |
| `ranking` | 3–5 個同位階項目；來源明示比較軸與方向；所有項目可形成唯一、無同分的完整順序。 | 以 `>` 列出 ID，例如 `B>A>C`。 |

只有一個可回答值時，必須產生 `free` 題，不得為湊足選項而編造錯答。`auto` 的回退是 `free`；明確要求 `single-choice`、`multiple-choice` 或 `ranking` 時，該候選應略過並保留原因。

每題問題文字必須本身包含選項、作答指示與排序方向。例如單選明示「請只回覆一個選項代號」，複選明示「請列出所有符合代號，以逗號分隔」，排序明示「依 X 由高至低排序，以 `>` 分隔」。這使 runner 仍只需傳送 `question` 給目標 Agent。

### 品質閘門

在輸出前，核心必須拒絕下列候選：

- 問題缺少主詞、必要條件、比較軸或排序方向；
- 選項混合不同實體、關係、單位或答案類型；
- 任一選項或正確答案無來源證據；
- 單選有零個或多個正確選項；
- 複選無法證明「所有且只有」正確選項；
- 排序缺少完整項目、方向不明、存在同分，或順序須由模型主觀推論；
- 選項文字與 evidence 不一致，或同一選項重複。

題型渲染只能改寫問題措辭與配置穩定的選項 ID；不得改變來源事實、補足缺失條件或創造反向規則。

## 資料契約與可追溯性

### 正式 JSONL：不變

```json
{
  "question": "…",
  "expected_answer": "B",
  "actual_response": null
}
```

選擇與排序題的 `expected_answer` 是回覆協定要求的 canonical 字串，不是提供給 Agent 的隱藏答案。完整選項均在 `question` 中。現行三欄驗證器、外部 reader 與不關心題型的工作流因而仍可讀取資料集。

### generation sidecar：擴充但不暴露至 JSONL

`<dataset>.jsonl.generation.json` 是題型的權威來源。現行實作只接受與產生 v4；
沒有 v4 題型映射者視為題型未知，而非被推定為選擇題。

sidecar 的 `dataset` 必須保存完整 JSONL 的 SHA-256 與每筆正式列的映射。每筆映射新增：

```json
{
  "line": 7,
  "qa_ids": ["qa_000007"],
  "record_fingerprint": "sha256(canonical UTF-8 JSON [question, expected_answer])",
  "question_type": "single_choice",
  "answer_protocol": "one_option_id",
  "options": [
    {"id": "A", "text": "…", "evidence_refs": ["kp_000012"]},
    {"id": "B", "text": "…", "evidence_refs": ["kp_000013"]}
  ],
  "correct_option_ids": ["B"]
}
```

自由回答資料列也保存 `question_type: "free"` 與 `answer_protocol: "natural_language"`。排序項目另保存 `ranking_axis` 與 `direction`。這些欄位是核心寫入的執行證據，不是讓使用者手改的設定；sidecar 可能包含來源內容與本機路徑，必須採與知識來源相同的存取保護。

### lineage：生成到評測

1. `run-agent` 載入輸入 dataset 時，尋找同名 generation sidecar，核對 dataset SHA-256 與每筆 fingerprint。
2. 核對成功時，runner 將最小、已驗證的題型契約快照寫進 `<responses>.run.json`：dataset hash、generation sidecar hash、每個 fingerprint 的題型與作答協定。原始來源、選項原文與正確答案不需複製到 run sidecar。
3. `eval` 從 responses 的 run sidecar 讀取該快照，再以題目／預期答案 fingerprint 連結 trial。它不得依回覆內容推測題型。
4. 資料集或 sidecar 雜湊不符時，runner 不附帶題型契約；eval 保留一般評測行為，但把題型統計標為 `unavailable`。不能使用失效行號或路徑作猜測。
5. 使用者直接提供手寫 responses、或不存在對應 run sidecar 時，也允許既有通用 eval；僅題型統計不可得。

此設計不依賴 responses 的列順序。`record_fingerprint` 是跨 generation、run、trial 與 eval 的 join key；行號僅作可讀的來源定位。

## 評測與報表

### 評測

- `free` 題沿用目前 evaluation skill 的語意判斷。
- 具備已驗證題型契約的單選、複選與排序題，由核心依 `answer_protocol` 做決定性正規化與比對。
- MVP 僅接受協定指定的 canonical 代號格式，允許移除首尾空白、大小寫正規化與全形英數／標點轉半形；任何附加自然語言、未知代號、重複代號、漏選、多選或錯誤分隔符都是 `invalid_response_format`。
- 有效格式但答案不符為 `incorrect`；格式有效且答案完全符合為 `correct`。排序不提供部分分數。
- `actual_response` 必須原樣保留；正規化結果只存在 evaluation item，不回寫 responses JSONL。

這些題型的 `correct` 由核心計算，不交給評測 Agent 猜選項。eval 的 plan 與摘要必須明確標示哪些 items 為 deterministic、哪些為 skill judgment。

### 報表

既有總計與逐 trial 證據仍保留。若題型契約可用，report 新增「依題型」表：

| 題型 | scheduled | execution error | evaluated | invalid response format | correct | correct rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |

`unknown` 不可與 `free` 合併。缺少或失效 lineage 時，report 顯示「題型統計不可得」，但不得使整份一般 report 失敗。

## 可觀察的完成摘要

以 `--question-type auto --count 20` 為例，stderr 完成摘要至少包含：

```text
[QUESTION TYPE] requested=auto
  free: 11
  single-choice: 6
  multiple-choice: 2
  ranking: 1
[SKIPPED] no_complete_candidate_set=9, unsupported_ranking_tie=2
```

數字是格式示意，不是此 PRD 對任何知識文件的保證。stdout 的 JSONL 行保持純資料，不混入進度或統計。

## 相容性與遷移

- 不帶 `--question-type` 的命令等同今日的 `free` 模式。
- 現有三欄 dataset、responses、trial sidecar、evaluation JSON 與 report 都保持可讀。
- 舊 generation sidecar 或沒有 sidecar 的資料，不取得題型統計；不回填或猜測題型。
- `--skill DIRECTORY` 仍選擇完整資料集生成方法。內建題型策略由 `--question-type` 選擇，不要求使用者了解或傳遞 skill 路徑。
- 此功能只新增題型選擇，並不重新引入已移除的資料 `--format` 選項。

## 驗收條件

| ID | 可觀察結果 |
| --- | --- |
| AC1 | `--question-type free` 與未指定時產生的資料列契約、既有自由回答流程與輸出相容。 |
| AC2 | `auto` 僅在來源存在合格候選集合時產生題型題；單一獨立事實產生自由回答。 |
| AC3 | 單選、複選與排序各有固定離線 fixture，覆蓋有效案例及缺少同位階選項、未知選項、關係混合、同分排序與不明排序方向。 |
| AC4 | 每個題型題的所有選項、正確 ID、比較軸與方向都有可定位來源 evidence。 |
| AC5 | 正式 JSONL 的每列仍恰有三欄，且通過既有 `read_records`。 |
| AC6 | generation sidecar v4 具有 dataset hash、record fingerprint、題型、協定、選項／證據映射；dataset 被改寫後 lineage 被拒絕。 |
| AC7 | runner 在 dataset 與 sidecar 均有效時建立最小題型契約快照；缺少或失效 sidecar 時不猜測題型。 |
| AC8 | eval 對有效題型契約做決定性格式／答案比對，並原樣保存 Agent 回覆。 |
| AC9 | eval 對無 lineage 的 responses 維持一般評測，report 顯示題型統計不可得而非失敗。 |
| AC10 | report 正確分開 `free`、各指定題型與 `unknown`，且總數可回溯至 trial items。 |
| AC11 | CLI help、英文與繁體中文使用文件、離線 unit／integration 測試同步更新；真實 provider 驗收另行標示，不以 fake provider 代替。 |

## 建議實作順序

1. 先定義題型候選集合、可行性驗證器與固定 fixture；以測試先鎖住「單一事實不可硬做選擇題」。
2. 讓內建生成 skill 在受控的唯讀候選集合上渲染題目，核心驗證後輸出既有三欄資料與 generation sidecar v4。
3. 實作 runner 的 dataset／sidecar 核對與最小 lineage snapshot。
4. 實作 eval 的 deterministic protocol judge 與 report 題型表。
5. 更新 CLI help、雙語文件與使用案例；最後以小型、無敏感來源進行真實 provider 驗收，將結果與離線測試清楚區分。

## 執行範例

下列命令可直接執行：

```powershell
.\.venv\Scripts\lladar.exe create test-dataset `
  --knowledge .\example_project\DIET-v1.md `
  --question-type auto `
  --count 12 `
  --output .\artifacts\diet-question-types
```

解讀：`auto` 會對來源中的完整候選集合優先產生單選、複選或排序題；只有單一來源事實的部分仍產生自由回答。`--count 12` 限制最終去重後的可用題數，而不是候選嘗試數。輸出目錄會有時間戳 JSONL 及其 `.generation.json` sidecar；後者保存題型與來源追蹤，前者仍是可直接交給 `run-agent` 的三欄資料。

## 核准後的題目解說範例

假設來源明示「方案 A、B、C 的資料保存期限依序為 30、60、90 天」，一筆單選題可為：

```json
{
  "question": "哪一個方案的資料保存期限為 60 天？\nA. 方案 A\nB. 方案 B\nC. 方案 C\n請只回覆一個選項代號。",
  "expected_answer": "B",
  "actual_response": null
}
```

它能成為單選題，是因為三個方案回答的是同一關係（資料保存期限）、相同單位（天），且來源支持所有選項。若來源只寫「方案 B 的保存期限為 60 天」而沒有可比較的 A、C 資訊，這筆題目必須改成「方案 B 的資料保存期限是多久？」的自由回答，而不是自行補 A、C。
