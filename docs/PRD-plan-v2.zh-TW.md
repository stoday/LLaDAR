# lladar plan v2：結構化測試規劃與可追蹤診斷

日期：2026-10-30。狀態：已於本機工作區實作，尚未提交；驗證範圍見文末。

本文件取代舊 plan 實作的設計方向。舊有 `src/lladar/plan.py` 可以完整移除並重做；不要求保留舊的 plan 對話、輸出 schema、命令生成方式或內部結構。既有 `create`、`run-agent`、`eval`、`report` 能力仍是 plan v2 可以規劃的工作流，但 plan 不應再把它們的 CLI 細節交給模型自由拼接。

## 1. 問題定義

目前 plan 同時承擔：

1. 理解使用者目標。
2. 決定應採用哪一種工作流。
3. 判斷需要向使用者取得哪些資料。
4. 解釋能力邊界與不可證明的結論。
5. 產生正確的 CLI argv。
6. 驗證參數、輸入、階段依賴和輸出產物。

當這些責任都放在 prompt 與模型輸出中，會產生四類問題：

- plan 亂問、重複問，或漏問真正必要的答案。
- 模型產生不存在、互斥或順序錯誤的參數。
- 使用者只看到「某某失敗」，不知道失敗在哪個階段、原因是什麼、如何修正。
- console 顯示、log 檔案、plan 產物與實際錯誤彼此不一致，事後無法追蹤。

## 2. 產品定位

`lladar plan` 是一個互動式需求釐清器與工作流規劃器。

它應該把自然語言需求轉成一份可驗證的 `PlanSpec`，再由確定性的程式驗證、渲染和交付。它不是：

- CLI 指令的自由文字生成器。
- 只靠 prompt 維護能力邊界的聊天機器人。
- 自動執行測試的總控器。
- 藉由文字回答推論隱藏業務狀態的幻覺偵測器。

核心承諾：使用者可以清楚知道這次要測什麼、需要補什麼、計畫能做什麼、不能證明什麼，以及出錯時下一步該怎麼做。

## 3. 目標

### 3.1 必須達成

- 以情境選擇 workflow profile，而非對所有使用者套用固定問卷。
- 每個 workflow profile 明確定義必要答案、條件式答案、禁止詢問事項和完成條件。
- plan agent 一次只問一個尚未取得且會影響決策的問題。
- 使用者已提供或可安全推導的資訊不得重複詢問。
- agent 只產生結構化 `PlanSpec`，不得直接產生正式 shell 命令作為真實交付。
- `PlanSpec` 由程式做確定性驗證，再生成 `plan.md`、`plan.json` 和命令腳本。
- 所有重要階段都能在 `--debug` 下即時顯示詳細診斷。
- 詳細診斷同步寫入 log 檔案，包含時間、階段、事件類型、錯誤、stack trace 和關聯識別碼。
- 發生錯誤時，console 與 log 都必須指出發生位置、原因、上下文、可採取的修正，以及產物位置。
- 不支援或缺乏證據的需求必須明確標記 `blocked`，不得產出貌似可執行的命令。

### 3.2 不包含

- plan 直接執行 `create`、`run-agent`、`eval` 或 `report`。
- plan 自動呼叫受測 Agent、API、網站或目標專案。
- plan 自動登入、取得或保存 credentials、cookies、tokens。
- plan 自動建立或修改使用者的 Skill。
- 為保留舊 plan 行為而增加相容層。
- 以模型自動修正 CLI argv 作為主要驗證策略。

## 4. 核心設計：三層責任

### 4.1 Workflow profile：定義「必須得到什麼」

每個可規劃的工作流有一份版本化 profile。Profile 是程式可讀的規格，不只是一段 prompt。

Profile 至少包含：

- `id` 與 schema 版本。
- 適用情境與觸發條件。
- 必要欄位。
- 條件式欄位及觸發條件。
- 可由對話推導的欄位。
- 禁止詢問或不可保存的資料。
- 可接受的 target 類型。
- 輸入與輸出產物。
- 相容性規則。
- 完成條件。
- blocked 條件與人話說明。

第一批 profile 至少涵蓋：

- 文件問答：`document_qa`
- 行為情境：`behavior_situation`
- 既有回答重評估：`evaluate_existing_responses`
- 評估結果報告：`report_existing_evaluation`

### 4.2 Plan agent skill：定義「應該怎麼問」

Plan agent 使用專用 Skill，但 Skill 不是能力真相，也不能取代 validator。Skill 負責：

- 用白話解釋欄位和術語。
- 根據 profile 選擇自然的提問方式。
- 避免固定問卷和不必要的問題。
- 根據使用者情境提供 2～4 個真正不同的選項。
- 在缺少觀測證據時先解釋限制，再詢問是否接受縮小目標。
- 保持對話中的前情提要。
- 避免要求 secrets、cookies、tokens。
- 在已足夠時停止提問，不為了完成所有階段而繼續問。

Skill 不得自行宣稱：

- 某個 CLI flag 存在。
- 某個 target 支援某種模式。
- 某個結果能證明隱藏狀態。
- 某個不存在的檔案或 Skill 已經準備好。

### 4.3 Deterministic validator/compiler：定義「是否真的足夠」

程式負責：

- 驗證 `PlanSpec` schema。
- 驗證 workflow profile 的必要欄位。
- 驗證 workflow、target、method、evaluation 的組合。
- 驗證既有輸入檔案是否存在及格式是否可辨識。
- 驗證階段依賴和產物依賴。
- 產生後續流程的 argv 或腳本。
- 產生一致的 Markdown、JSON、診斷和 log。

## 5. PlanSpec

plan agent 的正式輸出只允許是一個結構化候選計畫，不允許直接交付自由文字命令：

```json
{
  "schema_version": 2,
  "workflow": "document_qa",
  "status": "draft",
  "user_goal": "確認客服是否依退款文件回答",
  "observable_behavior": "回答是否符合來源文件中的退款規則",
  "inputs": {
    "knowledge": ["refund.md"],
    "dataset": null,
    "responses": null
  },
  "target": {
    "kind": "project",
    "path": "../my-agent"
  },
  "method": {
    "kind": "source_grounded_qa",
    "count": 10
  },
  "evaluation": {
    "kind": "expected_answer",
    "criteria": null
  },
  "stages": ["create", "run-agent", "eval", "report"],
  "assumptions": [],
  "limitations": [],
  "open_questions": [],
  "evidence": [],
  "diagnostics": []
}
```

### 5.1 PlanSpec 原則

- `status` 在模型產出時只能是 `draft`；由 validator 決定 `ready` 或 `blocked`。
- 不保存 secrets、cookies、tokens 或 `.env` 的值。
- 路徑、URL、使用者回答和模型決策必須分開保存。
- `stages` 是結構化階段，不是模型自由輸出的命令。
- 命令由 compiler 根據 `PlanSpec` 和當前 CLI 能力產生。
- plan.md、plan.json、命令腳本和診斷摘要必須來自同一份已驗證的 `PlanSpec`。

## 6. 對話狀態機

plan 不以「固定四階段問卷」實作，而以狀態機運作：

```text
需求理解
  ↓
選 workflow profile
  ↓
填必要欄位
  ↓
填條件式欄位
  ↓
能力與證據檢查
  ├─ 缺少必要資料 → 繼續詢問
  ├─ 能力不足 → blocked 計畫
  └─ 靜態檢查通過 → ready 計畫
```

每一輪模型回傳：

```json
{
  "kind": "question",
  "field": "target.kind",
  "context": "你已經提供退款文件，下一步需要知道要測哪個 Agent。",
  "message": "你要測試哪一種入口？",
  "options": [],
  "known": {},
  "assumptions": [],
  "open_questions": []
}
```

或回傳候選 `PlanSpec`。每次模型回應都必須先經 JSON schema 驗證和狀態機驗證；不合法時顯示具體錯誤並重新要求模型修正，超過上限就交付帶診斷的 failed 草稿，不可假裝完成。

## 7. CLI

基本介面：

```bash
lladar plan
lladar plan "我想測客服在資訊不足時會不會追問" --lang zh-TW
lladar plan --output runs/refund-plan --debug --log runs/refund-plan/plan.log
```

旗標：

- `--output DIRECTORY`：計畫包位置；預設 `runs/plan-YYYYMMDD-HHMMSS`。
- `--lang CODE`：使用者介面和產物語言。
- `--model MODEL`：規劃模型。
- `--env-file PATH`：模型環境檔路徑；不保存內容。
- `--debug`：即時輸出所有階段的詳細診斷，包括模型呼叫、schema 驗證、profile 選擇、欄位變更、validator、compiler、檔案寫入和例外。
- `--log PATH`：將相同的結構化事件寫入指定檔案；若省略則在 output 內建立 `plan.log`。
- `--max-input-tokens`、`--max-output-tokens`：模型資源上限。

`--debug` 和 `--log` 是正交設定：

- `--debug` 控制 console 詳細程度。
- `--log` 控制檔案保存。
- 沒有 `--debug` 時仍必須寫入足夠的錯誤和生命週期資訊到 log。
- 啟用 `--debug` 時不得只顯示摘要而把細節丟失。

## 8. Debug 與 logging 需求

### 8.1 即時 console 事件

`--debug` 必須在每個重要步驟開始、成功、跳過、重試、失敗時即時呈現事件。至少包含：

- `run_id`、`plan_id`、時間戳、stage、event type。
- 模型名稱、輸入／輸出 token（若 provider 提供）。
- 模型請求開始、完成、耗時和 retry 次數。
- 傳給模型的 prompt 組成摘要；不得輸出 secrets。
- profile 選擇和選擇理由。
- 已取得的欄位、變更前後值的安全摘要。
- 每次提問和使用者回答的事件摘要。
- JSON schema 驗證結果。
- workflow validator 每一條錯誤及其欄位路徑。
- compiler 產生的 stage、輸入依賴、輸出產物和 argv 的安全表示。
- 每個檔案建立、寫入、跳過或失敗事件。
- 未捕捉例外的類型、message、stack trace 和關聯事件。

格式範例：

```text
[2026-10-30T14:22:11+08:00] [run=abc123] [stage=validate] ERROR
PLAN_TARGET_REQUIRED: target.kind is required for requested stage run-agent
field=target.kind
next_action=回答受測入口類型：project、api 或 page
trace_id=evt-018
```

禁止只輸出：

```text
plan failed
something went wrong
command generation failed
```

### 8.2 Log 檔案

log 至少包含：

- 純文字人類可讀訊息。
- JSONL 結構化事件，或等價的可程式解析格式。
- 完整錯誤 stack trace。
- 模型 request／response 的去敏化摘要；不得默默保存完整 secrets。
- 對話 transcript 的去敏化版本。
- PlanSpec 版本和每次 validator 結果。
- 寫入產物的絕對路徑與 hash。
- 最終狀態和失敗原因。

建議計畫包：

```text
refund-plan/
  plan.md
  plan.json
  plan-events.jsonl
  plan.log
  commands.ps1              # 僅 ready 計畫
  diagnostics.json          # blocked 或 failed 時必須存在
```

log 寫入失敗時，console 必須明確指出原始錯誤和 log 寫入失敗兩個問題，不可吞掉其中任何一個。

### 8.3 錯誤分類

每個錯誤必須有穩定錯誤碼、階段、欄位路徑、原因和修正建議。至少分為：

- `PLAN_INPUT_INVALID`：初始輸入或選項格式錯誤。
- `PLAN_MODEL_INVALID_RESPONSE`：模型回應不是合法計畫／問題物件。
- `PLAN_PROFILE_NOT_FOUND`：找不到適用 workflow profile。
- `PLAN_REQUIRED_FIELD_MISSING`：缺少必要欄位。
- `PLAN_UNSUPPORTED_COMBINATION`：欄位組合不被能力支援。
- `PLAN_EVIDENCE_INSUFFICIENT`：目標需要的證據不可觀測。
- `PLAN_EXISTING_INPUT_MISSING`：指定檔案不存在或格式無法辨識。
- `PLAN_COMPILATION_ERROR`：PlanSpec 無法轉成現有工作流。
- `PLAN_ARTIFACT_WRITE_ERROR`：產物或 log 無法寫入。
- `PLAN_UNEXPECTED_EXCEPTION`：未預期程式錯誤。

每個錯誤的資料結構至少包含：

```json
{
  "code": "PLAN_REQUIRED_FIELD_MISSING",
  "stage": "validate",
  "message": "run-agent stage requires target.kind and target.path",
  "field_path": ["target", "kind"],
  "cause": "The plan requests executing an Agent but no target entry was selected.",
  "next_action": "Choose project, api, or page and provide its corresponding path or URL.",
  "recoverable": true,
  "trace_id": "evt-018"
}
```

### 8.4 敏感資料規則

- debug 不得輸出環境變數值、API keys、cookies、tokens、Authorization header。
- URL 中的 credential query parameter 必須遮罩。
- 使用者輸入若可能含秘密，log 使用去敏化摘要。
- 不保存完整 `.env`。
- stack trace 可以保存程式路徑和行號，但不可包含已插值的秘密。

## 9. 產物與狀態

### 狀態

- `draft`：對話尚未完成，不宣稱可執行。
- `ready`：必要欄位、能力、輸入和靜態依賴檢查通過。
- `blocked`：缺少必要資料或能力，無法安全規劃要求的工作流。
- `failed`：plan 程式、模型、validator 或產物寫入發生未完成的錯誤；必須有 diagnostics 和 log。

### ready 計畫

- 顯示目標、方法、限制、階段順序和未執行命令。
- 產生 `plan.md`、`plan.json`、`plan-events.jsonl`、`plan.log` 和命令腳本。
- 明確標示「尚未呼叫受測 Agent」。

### blocked 計畫

- 保存目前已取得的資訊。
- 列出每一個 blocking error code、原因和下一步。
- 不產生宣稱可完整執行的命令腳本。

### failed 計畫

- 即使模型或程式例外，也盡可能保存 `diagnostics.json`、`plan-events.jsonl` 和 `plan.log`。
- console 必須輸出錯誤碼、階段、trace id、log 路徑和修正方向。
- 不把程式例外誤標成 `blocked`，也不把使用者資料不足誤標成程式 `failed`。

## 10. 實作要求

### 10.1 舊程式碼處理

- 可移除舊 `plan.py` 的對話流程、prompt、命令修復迴圈、舊輸出格式和舊渲染器。
- 不得以「保留舊函式，外面再包一層」作為主要方案。
- 新實作應將以下責任拆成可測試的 module：
  - profile registry
  - conversation state machine
  - PlanSpec schema/parser
  - deterministic validator
  - workflow compiler
  - event logger
  - artifact writer
  - console renderer
- 對外 `run_plan(...)` 介面應保持小而穩定；複雜度放在 implementation 內部。

### 10.2 能力來源

- CLI 能力、workflow profile 和 validator 不得各自維護互相矛盾的規則。
- compiler 必須呼叫共用的無副作用驗證函式，而不是模擬執行 CLI 才知道是否有效。
- 文件、prompt 和 help 可以由 profile／schema 產生或引用，不要手動複製一份完整能力清單。

### 10.3 模型不可信邊界

- 模型輸出一律視為不可信輸入。
- 任何欄位都必須經 schema 驗證、型別驗證和能力驗證。
- 模型不能決定最終 status。
- 模型不能直接寫入任意路徑或任意命令。
- 模型不能要求 plan 執行外部副作用。

## 11. 驗收條件

### 對話與規劃

- 模糊需求會先釐清可觀察行為，不直接生成完整命令。
- 文件問答只詢問文件、目標和評估所需欄位；不要求不相關的多輪設定。
- 多輪情境才詢問停止條件和最大回合數。
- 已有 responses 時，能跳過 create 和 run-agent，從 evaluation profile 開始。
- 缺少隱藏狀態證據時，產出 `PLAN_EVIDENCE_INSUFFICIENT`，不得宣稱可驗證業務完成。
- 使用者改變目標時，舊欄位和舊階段會重新驗證，不會混入已撤回目標。
- 每次只問一個真正影響計畫的問題。
- profile 必要欄位不足時，validator 能指出精確 field path。

### 結構與安全

- 模型輸出不存在的 flag 時，`PLAN_COMPILATION_ERROR` 能指出具體 flag、stage 和替代方向。
- 互斥選項、缺檔、錯誤格式和不支援 target 都有各自錯誤碼。
- `plan.md`、`plan.json` 和命令腳本的目標、路徑、狀態一致。
- blocked 和 failed 都不產生誤導性的 ready 命令。
- 含中文、空白、引號和 shell 特殊字元的路徑不會改變語意。
- debug 和 log 都不洩漏 secrets。

### Debug 與錯誤

- `--debug` 能看到模型呼叫、提問、回答、profile、欄位變更、schema、validator、compiler 和檔案寫入等步驟。
- 每個開始／成功／跳過／重試／失敗事件都有 timestamp、stage、event type 和 trace id。
- 未預期例外顯示 exception type、message、stack trace、發生階段和 log 路徑。
- 模型回傳空字串、非法 JSON、錯誤 schema、重複問題和超過回合上限，都能區分處理。
- console 顯示的錯誤和 log 保存的錯誤具有相同 error code 與 trace id。
- log 寫入失敗不會被吞掉。
- 非 debug 模式仍保留可供事後診斷的完整 log。

## 12. 測試策略

### 單元與 contract tests

- 每個 workflow profile 的必要欄位、條件式欄位和 blocked 條件。
- PlanSpec schema parser 和未知欄位處理。
- validator 的每個錯誤碼和 field path。
- compiler 對合法 PlanSpec 產生穩定 argv。
- artifact writer 在 ready、blocked、failed 狀態的產物差異。
- logger 的事件順序、trace id、去敏化和 log write failure。

### Golden conversation tests

至少建立：

1. 文件＋專案的文件問答。
2. 沒有文件的行為情境。
3. 已有 responses 的重評估。
4. 隱藏狀態但缺少證據。
5. 只有網站入口。
6. 使用者中途改變目標。
7. 模型產生非法欄位或不存在 flag。
8. 模型 timeout、非法 JSON、空回應和連續重試。
9. log 路徑不可寫或磁碟錯誤。
10. debug 模式的完整事件輸出。

測試不得只確認最後文字；必須確認狀態、錯誤碼、產物、事件和 log 內容。

## 13. 實作順序

1. 定義 PlanSpec、錯誤資料結構和事件 schema。
2. 建立 workflow profile registry 和 deterministic validator。
3. 建立 compiler，從 PlanSpec 產生既有工作流的結構化步驟。
4. 建立統一 event logger、console renderer 和 artifact writer。
5. 移除舊 plan 對話與 argv repair loop。
6. 建立新的 plan agent Skill，讓它依 profile 填欄位並自然提問。
7. 接上對話 state machine 和模型 adapter。
8. 加入 golden conversation、錯誤、debug、log 測試。
9. 更新 README、中文文件、CLI help 和相關 PRD。

第一個 vertical slice 只要求完成：

```text
文件 → document_qa PlanSpec → project target → eval → report
```

先讓一條路徑穩定、可診斷，再加入網站、多輪和 adapter 等較複雜 profile。

## 14. 成功指標

- 使用者能從 plan.md 說出測試目標、必要輸入和不能證明的結論。
- ready 計畫不包含 validator 未通過的命令。
- blocked 計畫能指出可修正的具體缺口。
- 任何 failed run 都能從 console 或 log 找到 stage、error code、trace id 和 stack trace。
- debug log 足以重建 plan 的決策順序，而不需重新呼叫模型。
- 模型提問數降低不是唯一目標；更重要的是必要問題不漏問、已知問題不重問。
- 新增 workflow 時，只需要新增 profile、validator 規則、compiler adapter 和測試，不需修改一個巨大的 plan prompt。

## 15. 與舊 PRD 的關係

`docs/PRD-plan.zh-TW.md` 保留作為歷史背景；本文件是 plan 重做的實作依據。若兩份文件衝突，以本文件為準，尤其是：

- 不保留舊 plan 實作作為相容性限制。
- 不讓模型直接產生正式 CLI argv。
- 必須使用 workflow profile、PlanSpec 和 deterministic validator。
- `--debug` 必須覆蓋完整生命週期。
- 錯誤和 log 必須可追蹤、可保留、可對應。

## 16. 本機實作與驗證紀錄（2026-10-30）

- 已移除舊的自由 argv 生成及修復流程，改由專用 plan Skill 提交 schema v2 草稿。
- 已實作四個 workflow profile、對話狀態檢查、輸入／依賴驗證、確定性命令編譯、
  去敏化事件與文字 log，以及 ready／blocked／failed 的一致產物。
- 模型呼叫使用無工具介面；plan 不呼叫受測目標，也不執行後續工作流。
- CLI 與 compiler 共用無副作用的參數組合檢查；既有回答與情境 transcript、
  情境設定、API 文件及評估結果會先做本機格式與必要身分檢查。
- 真實 `gemini:gemini-3.8-flash` 已通過文件問答完整規劃與只建立情境的規劃；
  兩者皆未執行受測目標。額外從空白需求開始的真實對話遇到 Vertex 429，
  該次結果保留為 failed，不能算作通過。
- 可執行 `python scripts/verify_plan_live.py --case document`、`--case situation`
  或 `--case conversation` 重跑真實模型驗收。這會呼叫模型；測試用文件與占位專案
  保存於計畫旁的 `-inputs` 目錄，產物中的命令僅供檢閱。
- 自動化測試涵蓋四種 profile、狀態與產物、重複／無關提問、目標變更、非法 schema、
  模型失敗、log 寫入失敗、去敏化、token usage 與 CLI。這些 fixture 測試不代表
  任意自然語言對話或所有供應者都已完成真實驗收。
- 最終 planner／CLI 定向測試 70 項通過；擴大的相關回歸曾有 137 項通過。
  完整回歸跑得到 1130 項通過、17 項跳過、3 項失敗；其中 CLI help 缺少預設行為
  說明已修正並獨立重測通過。另兩項是既有翻譯表鍵不一致，以及回答擷取的 429
  錯誤分類；獨立重測仍失敗，相關檔案未在本次修改，因此未宣稱全套測試通過。
