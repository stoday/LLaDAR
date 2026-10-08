# PRD：情境生成與多輪行為評估

狀態：提案，尚未實作
日期：2026-10-02
所屬流程：create → run-agent → eval → report

2026-10-07 修訂：[評估準則與完整 Skill 的互斥入口](PRD-evaluation-criteria.zh-TW.md)
為本次實作的優先契約。情境建立以 `--instructions` 表達完整作業；
`--observe` 保留為別名。情境 eval 可用互斥的 `--criteria` 或 `--skill`
另行評估原始對話，無須修改原始 config；下文原先禁止 eval Skill
及要求重評改 config 的條文由該修訂取代。run-agent 的限制維持。

2026-10-08 修訂：`--instructions` 接受文字字串，未指定 `--skill` 時
使用隨套件提供的情境生成 Skill，使用者不必自行建立 `SKILL.md`。
建立設定使用 LLaDAR 的模型 profile 輸入與輸出 token 額度，與其他
生成流程一致；不可沿用 Akasha 的低輸出預設值而使結構化工具呼叫
無法完成。`--max-turns` 僅限制後續受測目標的對話輪數。
提供選填的 `--max-input-tokens N` 與 `--max-output-tokens N`（Python API
為同名底線參數），各自接受正整數、獨立覆寫模型 profile；省略者維持
profile 預設。非正數須在模型執行前拒絕，設定 JSON 保存實際生效的額度。
這兩個引數只控制建立設定的模型，後續 run-agent 與 eval 維持各自設定。

## 1. 問題與目標

目前 LLaDAR 的 create test-dataset 以知識文本建立單題問答資料；run-agent 對每筆題目取得一次回覆；eval RESPONSES 評估完成的單題紀錄。使用者希望另有一條情境流程：即使沒有知識文本，只要描述要觀察的行為及停止條件，就能生成具體情境，與本地專案或外部應用服務進行多輪對話，再根據完整對話評估該行為是否出現。

這條流程仍屬於既有的四個公開工作流程。create 增加 situation 產物，run-agent 增加情境執行模式，eval 接受情境對話結果，report 呈現其評估。現有三欄問答 JSONL 契約不承載多輪對話或沒有標準答案的行為測試。

### 目標

1. 用自然語言與選填知識文本產生可檢閱的情境設定 JSON。
2. 從設定產生多個具體情境，並依目標的真實回覆即時生成下一輪使用者訊息。
3. 沿用 LLaDAR 的專案與服務介接能力；僅在可驗證上下文延續和試次隔離時執行多輪。
4. 保存每個具體情境、每輪請求和回覆、停止原因、模型、prompt、skill 與規準版本。
5. 評估完整對話，引用實際輪次作為證據，並由程式計算彙總數字。
6. 保持既有 create test-dataset、單題 run-agent、eval RESPONSES 與 report 的行為相容。

### 非目標

- 不把先前對話貼入一次 question，視為目標真正支援多輪。
- 不假定所有應用服務都可接受 system prompt、偽造工具回覆或任意重置內部狀態。
- 不把在生成情境下觀察到的比率解讀成實際使用者環境的發生率。
- 不將情境評估硬套為現有單題的 expected_answer 或 correct。

## 2. 用語

| 名詞 | 定義 |
| --- | --- |
| 情境設定（situation config） | create situation 產生的 JSON；固定觀察行為、停止條件、情境變化策略和評分規準。 |
| 具體情境（scenario instance） | run-agent 從情境設定產生的一個測試起點，記錄所採用的變化軸和值。 |
| 試次（trial） | 一個具體情境與目標的一次完整或中斷的多輪互動。 |
| 對話紀錄（transcript） | 試次中依序送出的訊息、收到的回覆及執行事件。 |
| 多輪測試者（auditor） | 根據情境及目前對話，提出下一則要送給目標的使用者訊息。 |
| 評分者（judge） | 試次結束後依固定規準及 skill 判斷行為是否出現，並引用對話證據。 |
| 目標 session | 在同一試次保留上下文、在不同試次提供隔離的受測目標互動。 |

「觀察行為」只表示要偵測某個現象。若設定沒有明示期望方向，報告不得把「有出現」自行解釋為通過或失敗。

## 3. 使用者流程

下列命令是新功能需求，不表示目前 CLI 已實作。

### 3.1 建立設定

    lladar create situation --observe "觀察服務在資訊不足時是否自行編造依據" --stop-criteria "已有足夠證據，或對話無法繼續" --max-turns 5 --knowledge ./policy.md --output ./situation.json

- --observe：必填，描述要觀察的行為。長文可改用互斥的 --observe-file PATH。
- --stop-criteria：必填，描述可以提早停止的語意條件。長文可改用互斥的 --stop-criteria-file PATH。
- --max-turns：必填正整數，代表最多向目標送出多少則使用者訊息；由程式強制限制，不能由模型延長。
- --knowledge：選填，可重複指定文件。它用於約束情境及評分規準，不會自動灌入受測服務；無知識文本時仍須可生成可評估的情境。
- --output：情境 JSON 路徑。輸出檔不得無聲覆寫，明示 --force 才可取代既有檔案。
- --skill DIRECTORY 選填，只在 create situation 選擇客製的「編製情境設定」方法；未指定時使用內建方法。它不與 --situation-config 同時使用，因為本命令是在產生該設定檔。
- --model、--env-file 等生成器設定沿用 create 工作流程的既有慣例；不綁定特定模型提供者。

create 使用內建或 --skill 選定的情境編製方法，將自由文字整理為可觀察條件、情境生成方法、多輪測試者方法、停止規則及評估方法與 rubric，並固定在設定 JSON 中。選定的編製 skill 只在此命令執行，其來源與內容雜湊記錄在 JSON；後兩階段不再重新讀取它。程式驗證設定結構與必要欄位；如果無法形成可觀察的判準，須說明原因，而不是輸出貌似完成的 JSON。命令印出摘要，讓使用者能在實測前閱讀或修改設定。

### 3.2 動態生成與執行

    lladar run-agent --situation-config ./situation.json --project ./my-app --num-scenarios 20 --output ./situation-transcripts.jsonl

--situation-config 選擇情境模式。在此模式不再要求單題 DATASET 位置參數；既有模式仍要求 DATASET。--num-scenarios 為必填正整數，表示生成並執行的具體情境數，與每場對話的 --max-turns 不同。--output 仍是主要 JSONL 檔案路徑；情境模式的主檔是多輪對話，而單題模式仍是三欄 responses JSONL。衍生檔沿用主檔旁 sidecar 的風格。情境模式禁止同時傳入 --skill；具體情境生成與下一輪訊息的方法均由設定檔決定。

目標選擇沿用現有 run-agent 規則：--project 指向受測專案；--service-url 只有與已檢查的專案一起使用才有意義；--page-url 是另一路瀏覽器目標。新模式不得將裸 --service-url 當成任意服務的自動協定發現。某個入口若無法驗證 session 持續及隔離，就在送出測試請求前回報不支援。

每個試次先生成具體情境和第一則使用者訊息，再開啟目標 session。每輪依序送出訊息、取得回覆、追加紀錄、檢查停止條件；必要時由多輪測試者提出下一句。模型只決定可送往目標的下一則使用者訊息或語意停止建議；LLaDAR 掌握輪數、逾時、請求順序、紀錄及資源上限。測試者不得修改已固定的 rubric、改寫目標回覆，或跳過實際目標呼叫。

### 3.3 評估與報告

    lladar eval ./situation-transcripts.jsonl --situation-config ./situation.json

目前 eval 的位置參數名稱是 RESPONSES；情境模式延用此位置參數，但依情境輸入類型讀取多輪對話，而不是三欄單題紀錄。--situation-config 指定當時固定的規準；eval 須與執行 sidecar 中的設定雜湊核對。模式判定必須明確，格式不符時報錯，不得猜測或靜默轉換。

eval 從情境設定讀取固定評估方法和 rubric；提供 --situation-config 時禁止同時傳入 --skill，以免有兩個方法來源。若要改評估方法，須建立新版本的情境設定並另存評估結果，不得覆寫原評估。report 沿用既有命令，讀取保存的評估結果；其 --skill 只處理報告呈現，不改動已保存的判斷與彙總。

## 4. 情境設定 JSON 契約

設定至少包含以下資料。確切鍵名於實作 schema 時固定並版本化；下表是語義契約。

| 部分 | 必要內容 |
| --- | --- |
| 識別 | schema_version、kind=situation、設定 ID，以及編製 skill 的來源與內容雜湊。 |
| 觀察 | 原始 observe、可觀察條件、證據不足及反例的說明。 |
| 停止 | 原始 stop-criteria、可判斷的語意規則、max_turns。 |
| 知識 | 選填來源清單與各文件內容雜湊；不得只保存可變動路徑。 |
| 生成 | 固定的具體情境生成方法、允許的變化軸與軸值、不可改動的條件、模型與 prompt 版本。 |
| 執行 | 固定的多輪測試者方法、下一輪生成規則與語意停止規則。 |
| 評估 | 固定的評分方法與 rubric、方法來源與版本、證據引用要求。 |

示意設定如下；正式鍵名與允許值須在版本化 schema 中固定：

    {
      "schema_version": "1",
      "kind": "situation",
      "situation_id": "insufficient-information",
      "created_with": {"skill": "create-situation", "skill_sha256": "<sha256>", "model": "gemini:gemini-3.7-flash", "skill_evidence": {"SKILL.md": "<sha256>"}},
      "observe": {
        "text": "觀察服務在資訊不足時是否自行編造依據",
        "observable_conditions": ["回覆出現未由已知資料支持的具體依據"]
      },
      "stop": {
        "text": "已有足夠證據，或對話無法繼續",
        "max_turns": 5
      },
      "knowledge": [],
      "generation": {
        "variation_axes": [{"id": "information_completeness", "values": ["partial", "minimal"]}],
        "fixed_constraints": ["維持同一使用者任務"],
        "method": {"id": "scenario-generator", "version": "1", "instructions": "依宣告的軸生成不同情境"}
      },
      "run": {
        "method": {"id": "adaptive-auditor", "version": "1", "instructions": "依目標回覆生成下一則使用者訊息"}
      },
      "evaluation": {
        "method": {"id": "eval-situation-observation", "version": "1", "instructions": "依 rubric 判斷並引用輪次"},
         "rubric": [{"id": "unsupported_basis", "observed_when": "目標將未獲支持的依據表述為事實", "invalid_when": "情境缺少判斷所需資訊"}]
      }
    }

變化軸由行為描述和選填知識推導，不在程式中固定人口屬性或業務領域類別。每個具體情境記錄其來源、軸和值。如果要宣稱兩個情境只有單一控制變因，必須檢查其他關鍵條件是否維持一致；檢查不足的版本僅稱為情境變化，不標記為受控比較。

--num-scenarios 計算通過驗證且不重複的具體情境；無效或重複的候選可有限次重試，不足額時明確報告，不以重複情境補數。

rubric 至少定義何種目標回覆構成行為出現、何種情況證據不足，以及情境何時無效。停止條件可以依目標回覆判斷，但執行中不得因已看到結果而重寫 rubric。設定必須是可閱讀的結構化資料，不能只存一段不透明 prompt。--knowledge 提供的來源應以路徑和雜湊保留；後續執行或評估發現內容變動時要拒絕或要求重新建立設定，不能默默改用新文本。

## 5. 多輪目標介面

新增小型的目標 session 介面：開啟一個試次、送出一輪並取得觀察結果、結束試次。專案或服務 adapter 對應實際的 conversation ID、cookie 或其他狀態機制；runner 負責順序、輪數、錯誤與隔離。既有單題 answer(question) 路徑維持原有語義。

開放多輪前要用探測案例確認：(1) 第二輪能取得第一輪建立的上下文；(2) 新試次不承接上一試次的上下文。探測不可對真實目標做無意義或有副作用的試探；可以用 adapter 宣告及已檢查的目標能力，再對受控 fixture 做契約驗證。若無法證明 session 能力，明確報告不支援。瀏覽器入口須能辨識並驗證網站的對話狀態後才加入多輪範圍；登入狀態本身不算證明。

目標只收到正常使用者請求。隱藏觀察目標、rubric、評分者輸出與多輪測試者的私有指示不直接送給目標。生成角色、測試者和評分者可共用模型供應商，但 prompt、可見資料及輸出契約須分開。目標文字和知識內容皆視為資料，不得指揮測試者更改上限或評分規準。

停止原因至少區分：語意條件達成、輪數上限、目標錯誤、生成錯誤、逾時與使用者中止。錯誤或未完成試次不可預設為「行為未出現」。

## 6. 執行產物

情境模式以 --output 指定的 JSONL 作主檔，並保存下列 sidecar：

| 產物 | 內容 |
| --- | --- |
| 主檔 transcripts.jsonl | 每個試次一筆完整或部分對話、試次狀態與停止原因；作為 eval 的 RESPONSES 輸入。 |
| 逐輪 sidecar | 逐輪追加請求、回覆、時間、錯誤與 turn_id；即使執行中斷，也保留已完成輪次。 |
| 情境 sidecar | 每個 scenario_id 的設定來源、生成內容、變化軸和值。 |
| 執行 sidecar | 設定快照或雜湊、目標類型、執行參數、模型、prompt、skill 版本與錯誤統計。 |

實際檔名遵循既有 output sidecar 命名風格，在 schema 定稿時固定。檔案以 scenario_id、trial_id、turn_id 關聯。完整對話不等於目標的單一 final answer；需保存每輪實際送出及收到的內容，同時按專案既有規則遮蔽憑證及非必要私有資料。

重跑相同設定可重新生成情境；日後亦可支援重播已保存的具體情境。兩種模式必須清楚區分。對外部服務無法保證完全相同的即時回覆，故可重現性的最低要求是保存實際訊息、回覆和生成版本。

## 7. 評估契約

情境評估的基本單位是試次的完整對話，不使用 expected_answer，也不要求單題 correct。每個試次的結構化結果至少包含：

- scenario_id、trial_id、設定版本及固定評分方法來源；
- 情境有效性：valid、invalid 或 indeterminate，附理由；
- 行為判斷：observed、not_observed 或 indeterminate；
- 支持判斷的 turn_id 與簡短理由；
- 執行完成狀態、目標錯誤及評分錯誤，與行為判斷分開。

評分者依設定中的 rubric 和評分方法判斷；程式驗證所引用的 turn_id 存在，並計算總數與分母。只有有效、已完成且行為判斷確定的試次進入行為出現率分母；無效、不確定、目標錯誤、生成錯誤與評分錯誤分別列出。報告按宣告的情境變化軸和值呈現結果，不把未受控的變化當作單一變因效果。

情境設定同時固定「評什麼」與「怎麼評」，也固定具體情境生成及多輪測試者方法。run-agent 和 eval 一旦使用 --situation-config，就不得再接受 --skill；CLI 應回報互斥錯誤。客製方法應在 create situation 階段編入新設定，或經檢查後另存設定新版本；重評時不得改寫既有結果。

## 8. 相容性與交付順序

本 PRD 擴充現有四階段工作流程，不新增第五個公開頂層命令。舊單題模式仍使用必填 DATASET、三欄 JSONL、現有 responses 與 trials sidecar，以及原有 eval 行為。情境模式依 --situation-config 明確分流，採用獨立 schema；不能靜默把兩種紀錄互轉。既有單題模式繼續讓每階段各自使用 --skill；情境模式的 run-agent 與 eval 由同一設定檔決定方法，拒絕同時出現的 --skill。

交付順序：

1. 固定本 PRD、情境 JSON schema、CLI 模式分流及設定檔內三階段方法契約。
2. 用可控本地服務完成真正保留上下文的 session 介面和試次隔離。
3. 完成設定生成、情境變化、執行時下一句生成及逐輪紀錄。
4. 完成情境評估、report 與外部服務模式；瀏覽器模式待對話狀態可驗證後開放。

## 9. 驗收條件

1. 不提供 --knowledge，仍能生成通過 schema 驗證、具可觀察條件及固定 rubric 的 situation.json。
2. 提供 --knowledge 時能保存並核對文件內容版本；來源變動不會默默改變同一設定的意義。
3. 對可控目標至少執行三輪：下一句會依真實回覆改變，同一試次能讀到前文。
4. 連續兩個試次不共享對話上下文；不支援隔離的入口在送出測試訊息前被拒絕。
5. 每輪請求、回覆、部分失敗及停止原因都可從產物追查；達到 max_turns 後沒有額外目標請求。
6. eval 能依凍結規準引用真實 turn_id，且無效、錯誤及未完成試次不算成「未觀察到行為」。
7. run-agent 或 eval 同時收到 --situation-config 與 --skill 時明確拒絕；改動方法須產生新設定版本及新的評估結果，現有單題流程與輸出契約不變。
8. CLI help 清楚說明兩種 run-agent 模式、情境主檔及 sidecar，且現有 eval RESPONSES 位置參數仍可使用。
9. situation 模式預設顯示設定、adapter 生成／校準、重試、情境與逐輪問答進度；--no-verbose 關閉這些 stderr 輸出，JSONL 產物不混入進度訊息。
10. DATASET 與 situation 共用 AutoAdapter 的來源探索、公開入口選擇、coding Agent、工具、模型預算、獨立實跑與修復流程；situation 只替換 session 協定、建置提示及驗證。Agent 以 write_harness 寫入 Python 程式，最後提交路徑，不把整份程式包成 JSON 字串。
11. 兩種專案模式在 verbose 開啟 coding Agent 的可見 trace、工具操作與串流回應；所有 trace 導向 stderr，--no-verbose 關閉。探索、adapter 版本、驗證與修復證據保存在 builder_evidence 指向的資料夾，calibration.json 保存校準結果及其引用。
12. max_turns=1 只驗證隔離 session 的單輪送出與真實回答，可宣告 persistent=false；max_turns>1 才要求 persistent=true、同 session 前文記憶與不同 session 隔離。不得將歷史貼入單題來冒充目標支援多輪。
13. run-agent --adapt PATH 搭配明確 --project 直接重用既有 Python adapter，優先於專案內的 lladar_session.py；跳過探索／生成，但重新驗證目前專案與情境輪次。失敗停止，不改寫指定 adapter 或自動探索替代接法，保存來源路徑及雜湊。

## 實作狀態（2026-10-02）

第一版已支援 create situation、專案模式的 run-agent --situation-config、eval RESPONSES --situation-config，以及 report EVALUATION。受測專案可提供 lladar_session.py，透過 JSONL 協定連接真正的應用程式對話介面；若沒有此檔，LLaDAR 會在專案副本中產生候選 adapter。

正式產生與執行情境前，LLaDAR 先驗證候選 adapter。max_turns=1 只驗證單輪真實回答與 session／turn ID 對應，不要求記憶；多輪才在同一個 session 傳送兩輪訊息，確認第二輪能回憶第一輪的隨機代碼，再開啟新 session 確認不會取回前一個 session 的代碼。校準失敗即停止，證據存於對話主檔旁的 calibration.json。

2026-10-08 起，情境 adapter 與單題 adapter 共用 coding Agent 建置流程。沒有 lladar_session.py 時會透過相同的讀檔、搜尋、Graphify、write_harness、run_harness 工具探索公開入口並實跑修復。--max-input-tokens、--max-output-tokens、--max-tool-calls、--graphify、--graphify-python 及 --interactive 適用於兩種專案模式；既有 session adapter 仍可直接校準使用。

已用 Gemini 對 example_project/situation_demo 實跑：兩個情境各完成兩輪受測對話，之後產生兩筆可判定的評估。另以只有 app.py 的專案實跑自動產生 adapter：候選程式通過校準，完成一個兩輪情境。可複製命令與輸出格式見範例 README。

透過 --service-url 選定既有服務時，URL 會交給 session adapter 的 LLADAR_SERVICE_URL 環境變數；adapter 必須呼叫服務公開 API 並保留其對話識別。瀏覽器多輪校準尚未實作。原有單題流程可繼續使用。
