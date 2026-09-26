# Browser capture：開發驗證紀錄

驗證日期：2026-09-24。依據為當時的宣告式解析 PRD；
[目前 PRD](PRD-browser-captured-api-evaluation.zh-TW.md) 的生成解析器修訂已於 2026-09-25 獲確認。
以下既有測試數字與手動驗收均不是生成解析器的完成證據；新開發進度另記於文末。

這是階段性證據與待辦，不是整份 PRD 的完成宣告。真實網站、合成瀏覽器測試、
單元測試與 CI 執行結果必須分開報告。

## 已取得的證據

- 手動驗收：使用者在專用瀏覽器送出指定校正標記，另行確認 `YES` 後，
  一次獨立驗證和一題測試成功；本機 run sidecar 為 `verified`，completed=1、failed=0。
  這次真實驗收發生在下面的串流修正之前，不能當成修正後再次實測的證據。
  不在本文件複製站點身分、完整 endpoint、回應內容或登入資料。
- 非瀏覽器完整回歸：`212 passed, 2 skipped, 41 deselected`。
  兩個 skip 是 Windows 不適用的 POSIX signal-delivery 測試。
- 最新解析器 corpus：`41 passed`。完整非瀏覽器回歸也包含 4 個安全 CLI 錯誤指引案例：
  缺少 Chromium、拒絕確認、無法解析與逾時，不回顯合成機密並保留對應操作指引。
- 真實 Chromium 的「校正已關閉、verification／dataset 仍保持連線」回歸：
  修正前 `TimeoutError`，修正後與既有 open-stream 測試合計 `2 passed`。
- 瀏覽器完整回歸最新為 `41 passed, 214 deselected`，包含正常登入／問答 UI 的
  JSON、SSE、NDJSON 三種協定，以及同意、拒絕、獨立驗證失敗的 9 種組合。
  本輪首次完整回歸為 `9 failed, 32 passed`：8 個初始 `Page.goto` 超過 5 秒，
  尚未進入校正／解析；1 個是 binary blocker 分類回歸。後者已獨立重現並修正。
  單獨重跑完整 browser suite 為 41 passed，沒有放寬 timeout；這不是導航波動根因已查明的證據。
  請求 timeout 測試已排除 browser/server cleanup 時間，1.8 秒斷言不變；
  manual-navigation 測試增加啟動餘裕後仍保留 `stream_closed is False` 的核心斷言。
- `uv build` 與 wheel/sdist 的 `twine check` 通過；兩個套件內都有 browser 模組與
  兩個 packaged `strategy.py`，沒有 `.lladar` runtime、browser profile 或 `.env`。
  最新產物在 `.lladar/build-verification/workflow-evidence-final-02`；sdist 包含正常 UI 與
  decoder 測試，wheel 內三個 browser 模組與兩個 packaged strategy bytes 均與本機原始碼一致。
- `git diff --check` 未回報空白錯誤；有既有的 Windows 換行提示。

本輪完整回歸命令：

```powershell
uv run python -m pytest -o addopts= -m 'not browser' -q --basetemp .lladar/test-repros/workflow-evidence-unit-final-02
uv run python -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/workflow-evidence-browser-serial-02
```

上述 `--basetemp` 是本次專用目錄；後續重跑請使用新的專用目錄，避免 pytest 清除舊證據。
另以 CI 使用的 `python -m pytest -m browser --collect-only -q` 核對選取範圍：
正常 UI 檔 18 個案例、driver/transport 檔 23 個案例，不受預設 non-browser marker 覆蓋。

## 本輪 TDD 修正

| 行為 | 修正前的紅燈 | 現在的驗證 |
| --- | --- | --- |
| SSE 只有 progress 文字 | 被學成最終答案，未拋出錯誤 | 缺少終止答案時拒絕建立計畫 |
| JSON 語法完整但 HTTP body 尚未結束 | `response_is_complete` 提早回傳 true | 等 body 完成 |
| SSE 終止 frame 被網路切開 | 尚未收到訊息分隔符即回傳 true | 對標準 SSE 與 data-line 變體逐字截斷測試 |
| 校正與驗證恰巧回答相同內容 | 唯一 answer 欄位被錯誤排除 | 不要求已辨識的答案欄位每次文字都不同 |
| 校正已關閉、後續串流保持開啟 | verification 沒帶暫定完成規則而逾時 | 仍推導暫定計畫；經獨立驗證後才信任 |
| NDJSON progress 與答案使用同一欄位 | 只選最後一筆的 answer，錯把進度當答案 | 學習並核對終止 record discriminator |
| NDJSON 最終答案後面仍有 metadata | 學不到答案，或誤選最後一筆訊息 | 依終止標記選唯一 record，不依位置 |
| SSE tool/debug/progress 中有 final_text | 被明確欄位名稱提升成最終答案 | 先排除非答案事件，再選答案欄位 |
| SSE 的 JSON envelope 標示 tool 或 role=tool | 回傳工具的 final_text 而非真正答案 | 事件名稱與 payload envelope 都納入判斷 |
| NDJSON 工具執行完成 status=completed | 被當成助手回答完成 | 非答案 envelope 不可建立終止答案計畫 |
| 瀏覽器將無效 UTF-8 默默轉成替代字元 | JSON／SSE／NDJSON 三個案例均 completed=1 | 嚴格解碼後 completed=0，actual_response=null，eval 記為 execution_error |
| 已知不支援的 binary 協定仍送出 verification | UTF-8 修正後落入一般 transport error | 明確的 unsupported protocol blocker，不再嘗試第二次請求 |

新 NDJSON 宣告式欄位是 `record_selector` 與 `record_value`，保存已觀察到的終止標記，
不是網站名稱或程式碼。現在支援的標記來自 `type`／`event`／`status` 中可辨識的
done/final/complete/completed；未知語意仍會阻擋，不宣稱能推得任意協定。
NDJSON 維持等 HTTP body 關閉；重複終止記錄、缺少答案、損壞 JSON 與進度-only 的
學習及重播均有拒絕測試，逐字 prefix 測試也確認不會在 body 完成前宣告完成。
正常 UI 的 NDJSON fixture 同時有進度與尾端 metadata；SSE fixture 的工具訊息刻意帶
`final_text`。9 個 UI 案例皆驗證合成秘密不進入 responses/run/trials。

標準 SSE 的事件分隔依據是 [WHATWG event stream interpretation](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)。
Data-line 變體是另外支援的封裝，不等同標準 SSE 多行 data 的語意。

## 完成前還需要的證據／工作

### 本輪已補齊

1. [正常 UI fixture](../tests/test_browser_workflow.py) 先顯示登入按鈕，成功登入才有合成
   session cookie 與問答表單；測試透過輸入框和 Send 按鈕送出 marker，覆蓋 JSON 與 SSE。
   舊 polling fixture 保留作為 transport corpus，兩者的證據範圍不同。
2. 正常 UI 測試以外部目標的 request log 驗證：確認前只有一次手動校正；
   拒絕時零自動請求；同意且驗證通過時一次驗證加一題資料集；驗證失敗時不送資料集。
3. Session expiry 的真實 Chromium 測試：校正／驗證後使 session 過期，頁面回到登入，
   重新登入後同一題只重試一次；持續 401 時 actual_response=null，run/trials 記錄失敗。
4. Browser 模式的 responses、run sidecar、trials sidecar 碰撞均驗證保持原檔不變，
   且 browser／model factory 尚未啟動。
5. 進度訊息含帶 token 的完整 page URL、trials/run 保存原始 transport exception，
   以及 CLI 回顯 native/runtime browser error 的三條洩漏路徑均先由測試重現再修正。
   現在只保留 page origin、錯誤類型與固定安全說明；瀏覽器仍收到原本 URL 以正常登入。
6. URL-encoded form 的 marker 擷取先重現 `matching_requests=0`，再修正瀏覽器 JS 與
   Python matcher；正常 UI 案例另驗證 `&`、`+`、引號與中文題目完整重送。
7. `run-agent --help` 增加 Chromium 安裝、校正操作、YES 關卡、401/403 重新登入、
   產物與 service-url 差異，公開 CLI 測試不依賴 argparse 的自動換行。
8. 正常登入 UI 的持久 cookie 設有有效期限；關閉第一個 browser context 後，第二次
   由產品選擇相同 origin 的 profile，直接看到問答表單，無需再次登入；兩次校正與重播成功。
   這不宣稱已過期或僅在瀏覽器 session 有效的 cookie 必定能跨次重用。
9. 正常 UI 的 JSON／SSE／NDJSON 成功產物直接交給真實 `evaluate`，只有外部模型以
   固定 fixture 取代，三欄資料與 trials 保持相容，來源 responses 不被修改。
10. 三種協定的損壞 JSON、無效 UTF-8 共 6 個 browser 案例檢查失敗 responses、run、
    trials、eval 與 console：答案為 null，不評為答錯，合成回應秘密不進入產物。

### 仍需完成

1. 已有 dedicated browser CI job 定義，收集全部 `browser` marker，包含新 UI 檔；
   尚無此工作樹的遠端 CI 執行證據。發布時需以實際發布 commit 重跑 CI，
   不使用未提交工作樹的本機測試代替。
2. 修正後尚未再次取得真實內部網站的手動驗收證據。重新送出 verification／dataset
   必須另取得有界授權；不得把上述本機 fixture 成功等同真實網站再次驗收。

導航波動列為已知驗證限制：未能在「profile reuse → malformed JSON」最小順序或
單獨完整 browser 回歸重現，尚未確認根因。本輪未增加重試、放寬逾時或移除失敗斷言。

本機程式／測試的上述缺口已補齊；完整目標仍不能標為完成。外站重測需要使用者再次
操作登入／校正並同意明確的 verification 與 dataset 數量；遠端 CI 需要確認要提交的
feature 檔案與推送方式，不能把目前整個混合工作樹直接提交。兩者都不是自動續跑的授權。

## 逐項需求對照

以下對照固定指向 2026-09-24 宣告式版本，不適用於 2026-09-25 修訂後的同序條目。
S 為該版本 PRD 的 User Stories 編號；I、T、O、F 分別為 Implementation Decisions、
Testing Decisions、Out of Scope、Further Notes 的條列順序。這是證據索引，不把
「已有程式或測試名稱」直接視為需求完成。

| PRD 項目 | 本機權威證據與判定 |
| --- | --- |
| S1、S4、S18；I1、I2、I7、I8；T2 | `runner.py` 入口驗證、`RequestTemplate`、CLI 互斥／URL／碰撞測試、GET 與表單特殊字元的 browser replay、bare-API 頁面拒絕測試。已實作，不從 bare URL 猜 payload。 |
| S2、S3；I6 | 正常 UI fixture 的 Sign in、Question、Send；`PlaywrightBrowserDriver` 預設 visible；登入與 MFA 留給使用者。實際外站曾手動操作，修正後待再驗收。 |
| S8、S9、S10；I5、I22；T3 | origin-keyed 專用 profile、`.gitignore`、fresh-profile cleanup 測試與真實 Chromium profile reuse。正常登入 UI 也已驗證兩次獨立 context、第二次無登入操作而可送題。 |
| S11 | 正常 UI 的 session-expiry/relogin 案例含成功及持續 401，驗證只重試一次；失敗 actual_response=null。 |
| S5、S6、S12；I9–I15；T5–T7 | `response_decoder.py` 的 registry 與 learn/decode interface、41 個 parser corpus 案例、fetch/XHR/GraphQL/NDJSON/SSE/text 與 opaque/binary browser corpus；本輪新操作經 9 種正常 UI 組合與 redaction 驗證。未加入框架／站點分支。 |
| I17 | 瀏覽器解析仍只有宣告式計畫，不呼叫 coding agent 或執行生成 parser；Python 擴充是另待確認的提案。 |
| S7、S17；I16、I18；T8 | UI fixture 的 target request log 證明確認前只有手動校正、拒絕零自動請求、驗證失敗零 dataset；成功才執行一次 verification 加排程。 |
| S13、S14；I19、I20、I21；T1、T4、T9、T11 | 共用 runner/schedule/records，獨立 request identity 的 replay map；正常 UI-to-eval 的三種協定成功案例通過，6 種 malformed dataset 案例確認 null 與 execution_error，非瀏覽器完整回歸通過。完整 browser 的導航波動另列，不以聚焦成功代替。 |
| S15；T10 | CLI startup/prepare/native error、私密 URL、trial error 及正常 UI responses/run/trials 的合成秘密掃描通過；contract 只保存 fingerprint，profile 未打包。 |
| S16；I3、I4；T12 | `pyproject.toml` 核心 Playwright、CLI 的明確 Chromium 安裝指引、兩個 packaged strategy 無 provider 測試、build/twine/封裝檢查通過；`release.yml` 專用 browser job 已配置，但這份工作樹沒有遠端 CI 結果。 |
| I23、I24 | README 中英版、run-agent 網站指南、公開 CLI help 測試及本文件區分 replay/answer quality、原始串流/最終答案產物、service-url/page-url。 |
| T13、T14；F5 | 既有私密手動 run evidence 是修正前的一次 acceptance；本文件不複製 URL、endpoint、auth 或答案。修正後 acceptance 未完成。 |
| O1、O2、O6、O8；F1 | driver 只啟動專用本機 profile，不讀一般瀏覽器 credential store；正常站點操作由人完成。fixture 的自動表單操作是測試端行為，不是產品 DOM 自動化功能。 |
| O3、O4、O5、O7、O9；F2–F4 | 無 bare endpoint 猜測、OpenAPI/cURL/API-key 新模式、通用 binary 解碼或非問答批次功能；opaque corpus 拒絕，沒有答案品質或全網站正確性宣稱。 |
| F6 | 發布 issue 與 ready-for-agent 標籤是有 tracker 存取後的條件式交付；本輪沒有發布 issue、commit、push 或觸發遠端工作。 |

## 2026-09-25：已修訂 PRD，待確認開發

使用者提出讓 coding agent 撰寫並執行 Python 解析程式，以適應未知格式。
依使用者「先修訂 PRD」的要求，草案已改為「內建解析優先 → 經同意生成 Python →
隔離離線試跑 → 凍結候選／獨立驗證 → 整批重用」。依後續指示收斂為：僅能根據測試
錄製內容撰寫及執行回應解析，其他行為禁止，並由工具強制限制；不開放通用 Python 工具。
保留必要的資料最小化、有限預算、cache 失效與 GP1–GP9 驗收。主流程 PRD 同步引用與職責。

本輪沒有新增或啟用動態程式碼執行，也沒有安裝 runtime、傳輸模型證據或重跑外站。
GP1–GP9 全部待實作／驗證；既有 41 個 parser corpus 及 browser 測試通過不能代替它們。
待使用者確認設計後，先驗證隔離 runtime 的可行性，再依 TDD 開發；不能將既有專案
adapter 的使用者權限 Python 執行當作安全沙箱，也不能將解析通過等同答案內容正確。

## 2026-09-25：使用者確認後的第一個開發切片

以下是安裝授權前的歷史狀態；後續進度見下一節。

- 使用者已確認新版 PRD 並要求建立新目標、開始開發；PRD 與主流程文件已同步核准狀態。
- 測試 Seam 沿用核准 PRD：回應解析 Interface、受限解析工具，以及 browser/run-agent
  的授權和產物契約。先做 GP6 的「未同意不得執行」切片，不把單一切片視為整項驗收完成。
- Red：`tests/test_parser_runtime.py` 因缺少 `lladar.parser_runtime` 而無法收集。
  Green：新增專用解析入口的同意關卡後為 `1 passed`；合成寫檔指令未執行，原始材料未輸出。
- 既有 `tests/test_response_decoder.py` 與 `tests/test_browser_target.py` 基準：`52 passed`。
  這是本機非瀏覽器測試，不是實際 Chromium、provider、外站或 CI 驗收。
- 目前 `parse_recorded_response` 是 fail-closed 的入口：未同意回報 `execution_not_approved`，
  已同意仍回報 `runtime_unavailable`。尚未接入 browser runner，也尚不能執行生成 parser。
- 已確認專案沒有 Monty／Wasmtime；PyPI 有 `pydantic-monty==0.0.23` 的 Windows 依賴套件。
  Monty 僅是待實測候選，已詢問是否允許在專案 `.venv` 安裝；未安裝、未變更套件依賴。
  官方隔離說明不代替 GP4 的實際攻擊與資源上限測試。
- 尚未呼叫生成 provider 或重跑真實網站；使用者表示可合作瀏覽器輸入，需要時再通知。

## 2026-09-25：獲准安裝後的本機解析基礎驗證

- 使用者同意只在專案 `.venv` 安裝 `pydantic-monty==0.0.23` 及必要依賴，並加入
  `pyproject.toml`。已完成；新增的 client、runtime 也都是 `0.0.23`，`uv.lock` 已同步，
  `uv lock --check` 通過。本次未進行全域安裝。
- 專用解析入口現在可以執行 `parse(response)`，但未授權仍先擋下，缺少 runtime、版本
  不符或缺少該環境的 binary 都不退回 host Python。每次使用新的 Monty worker/session，
  不提供 mount、外部函式或具能力的 host 物件，OS callback 只拒絕操作。
- 這是 Monty 的語言層級隔離，不是宣稱已建置 OS/container 沙箱。Windows 實測涵蓋
  讀檔、寫檔、環境變數、socket、子程序、攔截拒絕例外後假裝成功，以及無窮迴圈、
  大量配置與輸出。這些通過不等於已證明所有隔離攻擊都被涵蓋。
- 已設定每次執行 1 秒、64 MiB、遞迴深度 100、最多 16 次 suspension；worker request
  timeout 為 3 秒。原始碼上限 64 KiB、序列化輸入／結果各 1 MiB、收集後丟棄的 print
  上限 8 KiB。語言執行限額不是整個主程序的 OS 記憶體限制。
- `parser_recording` 只產生結構性證據：欄位與 event 名稱改成代號，字串／數值替換為
  合成資料，原始名稱對照表留在本機，不帶入前一筆答案。JSON、巢狀 JSON 字串、
  NDJSON、標準 SSE 的順序與型別保留；超量、過深與未完成 SSE frame 會擋下。
  此模組尚未接到 provider，不能把本機去識別測試稱為模型資料傳輸驗收。
- TDD 曾抓到 SSE JSON scalar 字串失去引號、空字串被替換，以及尾端未完成 frame
  被捨棄的問題；補上限制後，`tests/test_parser_recording.py` 與
  `tests/test_parser_runtime.py` 合計 `33 passed`。
- 在上述最後兩個 SSE 測試加入前，整體非瀏覽器回歸為 `243 passed, 2 skipped,
  41 deselected`。兩項 skip 是平台限定測試；warning 來自既有 Akasha 相依套件。
- 加入兩項測試後完整重跑為 `244 passed, 1 failed, 2 skipped, 41 deselected`。
  失敗位於既有 `test_skill_controls_paged_reading_and_unread_source_is_partial[True]`
  的檔案發布：`os.replace` 回報 `WinError 5`。未修改該功能；使用新暫存目錄單獨重跑
  兩個參數案例為 `2 passed`。原因尚未確定，不能據此宣稱最新完整回歸全綠或已修復。
- GP4／GP5／GP6 目前只有基礎切片證據。尚待受限生成工具、有限修正／試跑、候選凍結、
  獨立擷取驗證、cache／漂移、browser runner／CLI 授權與產物整合；GP1–GP9 均不宣告完成。
  此輪沒有呼叫真實 provider、外站或遠端 CI，也未要求使用者操作瀏覽器。

本輪局部重跑命令（再次執行請替換成新的 `--basetemp` 目錄）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_parser_recording.py tests/test_parser_runtime.py -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-evidence-framing-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-foundation-nonbrowser-final-20260925
.venv/Scripts/python.exe -m pytest 'tests/test_generation_skills.py::test_skill_controls_paged_reading_and_unread_source_is_partial' -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-foundation-publish-recheck-20260925
```

## 2026-09-25：離線生成、凍結與獨立核對切片

本節是上述本機基礎的後續進度，不是 GP1–GP9 完整驗收。

- 新增專用 `RecordedParserTool`：只在 Monty 中試跑合成錄製證據，最多 3 次，失敗也計入
  預算。只回傳安全狀態／原因碼與候選 hash，不回傳原始程式、實際答案或 runtime exception。
  權限越界、資源超限或隔離故障直接封鎖；凍結候選後禁止再修改／試跑。
- 新增內部生成流程：模型目的地、證據傳輸、程式執行各自確認後才初始化 provider。
  固定最多 3 次模型提案；傳入 8192 output-token 上限與剩餘 timeout，總期限 30 秒，
  在模型呼叫前後及離線試跑後檢查期限。**目前只有 fake provider**；尚無 production
  provider adapter，因此尚未證明真實 SDK 的 request timeout、取消與 token 限額生效。
- 合成 JSON fixture 已串接「假 provider 提出錯誤程式 → 安全回饋 → 修正 → 實際 Monty
  試跑 → 凍結 → agent 未見回應／可信本機完整回答核對 → 新題解碼」。另有混合 NDJSON
  與未知 replacement／terminal event 的 open SSE fixture，使用相同工具、驗證與解碼
  Interface。三種均不依賴 `final_text`，且測試先確認基準內建計畫不能解析對應材料。
  NDJSON／SSE 目前使用固定提案，不得稱為真實模型生成或 browser replay 驗收。
- 輸出須有抽取及完成依據。宿主獨立重讀來源位置，核對完整字串／字串片段列表的組合，
  再核對可信本機參考的完整文字、完成狀態與 request identity；不是只接受非空文字。
  參考不來自 `expected_answer`，測試中的錯誤目標回答也原樣保留。
- 校準內容重新標記不算獨立樣本；候選只有一次獨立驗證機會，失敗後不能改參考答案
  重驗同一候選。已驗證 parser 不接受重複 request identity；每題仍檢查來源依據，
  抽取／完成規則不得漂移，失敗後阻止後續解碼。request identity 由可信宿主提供，
  browser driver 的實際關聯與「解碼封鎖 → 停止派送」尚待整合測試，不以局部測試代替。
- TDD 抓到並修正：硬寫答案仍被當作候選、驗證後才偽造答案、將 progress 改稱完成、
  捨棄終止事件後的截斷 frame、重驗同一 holdout、重用驗證 request、缺少本機參考時
  洩出普通例外、provider 原始例外，以及越權後仍繼續模型修正／試跑期限未含最後一輪。
- 最新局部結果為 `58 passed`；非瀏覽器回歸 `270 passed, 2 skipped, 41 deselected`。
  先前既有發布測試的 `WinError 5` 此次未重現，未修改該功能，也未認定根因已修復。
  `compileall` 與 tracked diff whitespace 檢查通過（仍有工作樹既有 CRLF 提示）。

下一階段仍需完成：內建／生成 Adapter 的 browser runner 與 CLI 授權整合、當前
session 的本機核對互動、串流完成探測、來源／schema 漂移與複雜組合證據、私密 cache
與竄改／失效、production provider adapter 及其實際預算限制、browser fixture replay、
經各自授權的真實 provider／內部 SSE 網站驗收，以及發布 commit 的 CI。
目前只實作可核對的 JSON／NDJSON／標準 SSE 路徑與完成依據；這不是任意格式皆可解析的宣告。
沒有傳送真實錄製資料給 provider，沒有外站請求，也未要求使用者操作瀏覽器。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_parser_generation.py tests/test_generated_parser.py tests/test_parser_recording.py tests/test_parser_runtime.py -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-adaptation-focused-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-adaptation-nonbrowser-20260925
```

上述目錄已有此輪測試產物，再跑時請另選新的 `--basetemp` 目錄。

## 2026-09-25：優先安排的真網站有界重測

使用者即將離開電腦，要求優先進行真網站開發／測試，並另行同意：在原內部網站手動
校準後，最多自動送出 1 次驗證問題及 1 題印表機問題，不向生成模型傳輸資料。

- 使用 LLaDAR 的可見專用 Chromium、既有 `PlaywrightBrowserDriver` 與正式內建解析器。
  使用者在正常網站 UI 送出本輪唯一校準 marker，等串流完整回答後通知繼續。
- 本次實際執行 **2 次自動問答請求**：先獨立驗證，成功後才送出 1 題。兩者均完成，
  沒有重試、重新登入重送或其他自動題目。第 2 題成功抽取 769 字元的最終文字。
- 結果為 `parser_kind=builtin`、`decoder=sse-json`、`completed=1`。
  這只證明當次授權問答 workflow 的擷取／內建解析成功，**不判定印表機數量正確**，
  也不代表生成解析器、所有網站或未知封裝皆通過。
- 這次採用忽略目錄中的人工驗收 helper，直接使用正式 driver／decoder；不是新生成
  路徑的完整公開 `run-agent`／`eval` 驗收。沒有呼叫模型、評分、發布或遠端 CI。
- 原始請求／回應與抽取文字只留在存活 helper 的本機記憶體；寫入私密忽略目錄的三份
  錄製樣本只有去識別結構，原始 Cookie、header、payload、回答和私密欄位名未另存成檔。
  專用 browser profile 仍依既有政策留在本機。沒有把這些結構樣本交給生成 provider。
- helper 完成後停在只接受本機離線核對／關閉指令的階段，不會自行再次請求網站。
  後續額外真網站請求或模型證據傳輸仍須另外取得相應同意。

在等待網站回應期間完成的本機開發切片：`BrowserTarget` 可接收已明確授權的生成設定，
內建提案不能處理時，先離線凍結候選、再送驗證，且每題重用相同解析程式；去識別
descriptor 記入 target evidence。假 driver／fake provider 加上實際 Monty 的局部回歸
為 `37 passed`。這個切片目前只接通完成的 JSON 回應；生成串流完成探測、CLI／真人
本機核對互動、停止後續派送、cache 與 production provider 等整合尚待補齊，不能拿
本節真網站內建路徑的成功替代。

## 2026-09-25：生成串流完成探測與失敗停止派送

本節是後續本機切片，沒有追加任何真網站請求或 provider 傳輸。

- 共同完成探測介面可接收凍結／已驗證的生成 parser。依固定的完成依據讀取 SSE
  終止事件與完整 frame，不會每收到一個片段就重新執行生成 Python；完整抽取仍須
  經 Monty、來源核對與固定規則驗證。探測也受 1 MiB 輸入上限約束，超限安全封鎖。
- 手動校準時，使用者確認頁面完整回答後，可保留未知終止事件的完整 SSE 錄製材料，
  即使連線尚未關閉；這只允許繼續解析適配，不等同判定 parser 已驗證。
- 新增真正本機 Chromium 整合案例：正常登入、UI 校準、fake provider 固定提案、實際
  Monty 試跑、凍結、獨立回答核對、公開 `run_agent` 路徑的單次取樣與三欄輸出。
  fixture 使用未知 replacement／terminal event、巢狀 JSON 字串與片段列表，不含
  `final_text`；伺服器送完終止事件仍保持連線，驗證沒有依賴斷線才能完成。
- 同一案例加入第二題格式損壞：保留第一題成功回答、失敗與未送出題目維持 null，
  外部 fixture request log 證明第三題沒有派送，run sidecar 的 parser／target 狀態
  更新為 blocked，而非沿用 prepare 時的 verified。測試不使用 expected_answer
  作為抽取依據，故意不同的預期答案也不會改寫目標的實際回答。
- TDD 先重現未知 open SSE 校準逾時、失敗後仍送下一題、完成探測未限制輸入大小，
  再逐一補上。局部完成探測／decoder corpus 為 `61 passed`；browser target／生成
  parser／新瀏覽器案例的前一輪局部回歸為 `32 passed`。
- 全部非瀏覽器回歸為 `274 passed, 2 skipped, 43 deselected`，未啟動真實 provider。
- 全部瀏覽器回歸為 `42 passed, 1 failed, 276 deselected`。唯一失敗是既有
  `test_playwright_replay_stops_at_the_configured_timeout` 在初次 `Page.goto` 的
  1000 ms 導航期限內未完成，尚未進入 replay。未更動其期限或產品行為；隨後單獨
  重測該案例及兩個新增串流案例為 `3 passed`。這不代表已找出或修復啟動逾時根因，
  也不把第一次完整回歸改記為全綠。
- 變更檔案的 `compileall` 與 tracked diff whitespace 檢查通過；Git 仍提示既有
  LF／CRLF 轉換。沒有 commit、push、發布或遠端 CI。

此處使用 fake provider，不代表真實模型已會生成上述程式；本機正常登入由 fixture
UI 自動操作，也不是在真網站代替使用者登入。CLI 生成授權／真人核對 UX、production
provider 的硬性預算、私密 cache、其他未知封裝 browser corpus 與完整 GP1–GP9
仍待開發／驗收。先前真網站成功仍僅適用於內建 `sse-json` 路徑。

本輪命令（重跑時另選新的 `--basetemp` 目錄）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_generated_parser.py tests/test_response_decoder.py -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-completion-budget-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-stream-stop-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-stream-stop-browser-20260925
.venv/Scripts/python.exe -m pytest tests/test_playwright_driver.py::test_playwright_replay_stops_at_the_configured_timeout tests/test_browser_workflow.py::test_generated_parser_handles_unknown_open_sse_through_normal_browser_ui -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-stream-stop-browser-recheck-20260925
```

## 2026-09-25：60 分鐘等待、provider 傳輸與本機核對

依使用者新增要求，CLI／`run_agent`／專案 adapter 的目標等待預設值由 120 秒調為
3600 秒；`--timeout` 仍接受自訂秒數。parser 生成流程總期限由 30 秒調為 3600 秒，
最多 3 次模型提案共用剩餘預算。Monty 的 1 秒運算、64 MiB 記憶體、輸入輸出及
權限限制沒有放寬；不是讓不可信解析程式執行一小時。既有執行中的程序不會因此重啟。

- TDD 從公開 CLI／runner 證明新預設值傳到 target，也保留自訂 17 秒的覆寫行為。
  模擬時鐘證明 30 分鐘的模型回應不再被原本 30 秒上限拒絕，60 分鐘到期仍封鎖。
  未實際等待一小時，也未對真網站重送題目。
- 新增 Gemini provider 傳輸 Adapter，採固定 Google API 目的地；不繼承代理或其他
  cloud endpoint，不跟隨轉址、不自動重試、不提供模型工具。測試專用的 endpoint
  僅可指向明確的 loopback HTTP fixture。這不是已完成的 CLI 生成授權介面。
- 可信 HTTP worker 用子程序執行單次有界請求，逾時終止並關閉連線。這個 worker
  **不執行生成程式，也不是沙箱**；生成程式仍只交給 Monty。模型 prompt 上限
  64 KiB、HTTP 回應上限 256 KiB、每次要求最多 8192 output tokens。
  中止本機連線不宣稱遠端服務一定取消計算或計費；真實服務端行為尚未驗收。
- 本機 HTTP fixture 驗證無轉址／重試、429／500、安全錯誤、工具型／截斷／過大回應、
  逐小塊持續送資料也不能逃過總期限，以及「HTTP 提案 → 離線修正 → 實際 Monty
  試跑 → 凍結 → 獨立核對 → 新回答重用」。亦確認 API key 不進 prompt、原始私密
  值不送 provider、HTTP 回傳的惡意來源未能寫入宿主檔案。這是本機假服務，
  **不是經授權的真實 Gemini 生成驗收**。
- 生成路徑可在本機 `about:blank` 核對頁顯示新驗證題的錄製回應及抽取全文。
  頁面以文字呈現並禁止腳本／外連；不把舊校準網頁當成新驗證題的證據。
  操作者必須比較完整來源與答案，再明確輸入 MATCH；預覽本身不算驗證通過。
  內容不寫入 console、模型 prompt 或一般產物。本機核對頁的拒絕／注入測試
  尚待擴充，不能宣稱完整 GP5 驗收。
- 非互動且缺少可信參考來源時，改在初始化 provider 及送驗證題之前封鎖，
  避免已知無法驗證卻仍消耗模型／網站請求。本輪 parser generation／provider／
  runtime 局部結果 `47 passed`；新的本機核對／串流案例與既有逾時案例 `4 passed`。
- 全部非瀏覽器回歸 `295 passed, 2 skipped, 44 deselected`；CLI help 與預設／覆寫
  期限的最後重測 `5 passed`。本輪沒有重跑全部 44 個瀏覽器案例，不以 4 個重點
  案例代替完整 browser corpus。

目前仍待完成：公開 CLI 生成授權／built-in-only 政策、其他未知封裝 browser corpus、
私密 cache 與竄改／版本失效、複雜抽取證據、完整隱私／安全 corpus，以及各自授權的
真實 provider、生成路徑外站驗收與發布 CI。沒有安裝其他套件、呼叫真實模型、追加
外站題目或提交／推送。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_parser_generation.py tests/test_parser_provider.py tests/test_parser_runtime.py -o addopts= -q --tb=short --basetemp .lladar/test-repros/parser-hour-timeout-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/agent-hour-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest tests/test_browser_workflow.py::test_generated_parser_handles_unknown_open_sse_through_normal_browser_ui tests/test_playwright_driver.py::test_playwright_replay_stops_at_the_configured_timeout -o addopts= -q --tb=short --basetemp .lladar/test-repros/agent-hour-browser-20260925
```

命令中的測試產物目錄已使用，再跑請另選新的 `--basetemp`。

## 2026-09-25：公開 CLI 同意流程與預設模型協助適配

使用者後續明確選擇「模型協助適配」，本節及新版 PRD 取代先前「內建優先、未知格式
才啟用生成」的預設。這是實作政策變更，不是傳輸真實網站資料的授權。

- `run-agent --page-url` 預設 `--parser-policy model-assisted`：成功錄製後，不以內建
  規則命中／失敗作為門檻，熟悉或未知封裝都在另外同意後生成解析器。套件排程仍在
  本機執行，不會為排程啟動模型。`--parser-policy builtin-only` 保留無 parser 模型路徑。
- 網站 `YES`、模型證據 `TRANSFER`、Monty 執行 `EXECUTE` 與新驗證題的本機核對
  `MATCH` 各自有獨立用途。兩個 `--allow-parser-*` 參數只核准本 run 對應範圍，
  不能略過獨立驗證。非互動且無可信參考仍在模型及驗證請求前封鎖。
- production provider 固定使用 Gemini API；CLI 顯示目的地與所選模型。只有合成結構
  證據進提案，原始資料與 expected_answer 不進入模型。初始化只發生在相應同意之後。
  本輪所有 provider 都是測試替身，未傳任何真網站材料，也未呼叫真實 Gemini。
- TDD 先證明原流程對熟悉 JSON 不生成而失敗，再改成熟悉與未知格式都生成；兩道
  同意任一拒絕時零模型／零自動網站請求；只用內建政策不初始化 provider。
  目標答錯仍原樣寫入三欄，不使用 expected_answer 修正。安全 blocker 原因寫入 sidecar。
- 純文字增加合成證據及全文抽取 witness，保留換行與空白；JSON／NDJSON／純文字
  必須完成 body 擷取才允許送模型。SSE 仍以完整 frame 與獨立完成證據驗證，不必等
  keep-alive 連線關閉。生成程式、runtime 權限及 60 分鐘模型等待設定沒有擴權。
- 本機真 Chromium 的 6 個生成案例通過：未知 replacement SSE 的重用／漂移停止，
  以及公開 CLI 對未知 SSE、熟悉 JSON／SSE／NDJSON 的各自同意與本機核對。
  最終核對由 fixture 操作者對獨立已知答案比對；fake proposal 不等同真模型生成能力。
- 切換預設後全非瀏覽器回歸 `309 passed, 2 skipped, 47 deselected`。其後新增未完成
  body 擷取拒絕的 3 個案例，相關 generation／recording／parser／CLI 測試為
  `58 passed`。最後這個小變更沒有再重跑全部非瀏覽器集合。
- 切換預設前的完整瀏覽器回歸為 `43 passed, 1 failed`；失敗仍是既有
  `test_playwright_replay_stops_at_the_configured_timeout` 在初始 `Page.goto` 的
  1000 ms 限制內未完成，尚未測到 replay。本輪稍後單獨重測為 `1 passed`，
  未修改該測試或宣稱根因已修復。切換預設後只重跑上述 6 個生成案例，不把它說成
  全部 47 個 browser corpus 通過。
- PRD、README 與網站英／繁中 CLI 參考及操作指南已同步。實際安裝的
  `lladar run-agent --help` 及變更 Python 檔案 compileall 通過。沒有 commit／push／CI。

尚待開發／驗收：其他未知封裝 browser corpus、較複雜的抽取 witness、私密 cache 與
竄改／版本失效、本機核對的拒絕／注入測試、完整隔離／隱私 corpus，以及分別取得
授權後的真實 provider 與外站生成路徑。GP1–GP9 未宣告完成，先前真網站成功仍只
證明內建 SSE 路徑；沒有追加網站請求或要求使用者重新登入。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-model-default-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py::test_generated_parser_handles_recordings_through_normal_browser_ui -q --tb=short --basetemp .lladar/test-repros/parser-model-default-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_generation.py tests/test_parser_recording.py tests/test_generated_parser.py tests/test_parser_policy.py -q --tb=short --basetemp .lladar/test-repros/parser-model-final-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_playwright_driver.py::test_playwright_replay_stops_at_the_configured_timeout -q --tb=short --basetemp .lladar/test-repros/parser-model-timeout-recheck-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。

## 2026-09-25：私密解析器快取與未知 JSON／NDJSON 瀏覽器案例

本節是前述待辦的增量進度，不回溯改寫舊測試結果，也不宣告 GP1–GP9 全部完成。

- 公開 CLI 的模型協助流程預設啟用 `.lladar/parser-cache`；同網站 origin、runtime
  policy 與錄製結構指紋才有機會命中。只保存驗證後、通過保存檢查的原始碼、版本、
  hash 與抽取 witness；不保存錄製、欄位原名映射或舊驗證結果。三欄輸出不變，
  sidecar 只記 `stored`、`not_stored`、`hit_reverified` 或 `disabled` 等狀態。
- 快取命中只省略模型提案／TRANSFER；仍須本次 EXECUTE 同意、合成校正材料的
  Monty 試跑及新的獨立驗證。不以過去的 verified 狀態放行，也不逐題生成。
  `--no-parser-cache` 停用讀寫；`--clear-parser-cache` 在正常執行前只移除該站的
  regular parser entries，不清除登入 profile、其他網站或無關檔案。兩個旗標均只
  適用 `--page-url`，不是獨立清理指令。
- Windows 私密檔案實際使用 user-scoped DPAPI，未使用 LOCAL_MACHINE；讀回仍檢查
  payload schema、source hash、runtime policy 與 witness。非 Windows 分支使用
  owner-only 目錄／檔案與 HMAC integrity key，**未在本輪作非 Windows 實測，也不是
  宣稱該分支有加密**。這些措施不防護已被攻陷的當前使用者或管理員。
- 每站最多 16 項、單項最多 128 KiB；不自動淘汰舊項目。超限、損壞、不同站點／結構、
  hardlink 檔案與缺少執行同意都有本機測試。命中規則保守，frame 數或結構差異可能
  造成 miss，不能宣稱所有網站或每次重跑都免模型提案。
- TDD 發現原先只比對完整字串常數會漏掉「私人文字嵌於長字串或註解」；4 個失敗案例
  後補上 bounded source/comment 與解碼後 str/bytes literal 檢查。原始私人值僅留在
  recording 的本機 RAM、不送模型／不保存；過於昂貴或無法檢查就不寫快取。
  這不是通用反混淆器，數字、動態重組及完整敏感常數 corpus 仍待擴充，不宣稱 GP5 完成。
- 快取／recording／generation／generated parser 的局部回歸 `59 passed`。
  先前公開 CLI 快取／政策／browser target／pipeline 集合 `68 passed`；之後容量／
  hardlink／help 集合 `29 passed`。真 Chromium 跨兩次瀏覽器執行的 CLI 快取案例
  `1 passed`：只一次提案，但兩次均使用不同新驗證題、本機核對及執行同意。
- 新增未知巢狀 JSON 與混合 NDJSON 的正常登入／表單 UI：先確認同一 fixture 的
  基準內建計畫失敗，再由 fixed provider 提交 unsupported 候選、依離線回饋修正，
  實際 Monty 試跑、凍結、透過本機 review 核對獨立回應後跑資料集。兩個新案例
  `2 passed`，沒有 `final_text` 或站點特例；重新組合分段答案且保留錯誤目標回答。
  第一次測試中的 provider feedback assertion 誤寫 rejected，依現有工具契約改成
  unsupported；不是修正 production parser 或放寬驗證。
- 變更後完整非瀏覽器回歸：`331 passed, 2 skipped, 50 deselected`（68.20 秒）。
  完整瀏覽器回歸：`49 passed, 1 failed, 333 deselected`（196.87 秒）。失敗為
  `test_bare_api_url_is_rejected_as_a_noninteractive_page` 在初始導航 `/bare-api`
  等待 DOMContentLoaded 時超過 5000 ms，還沒到預期的「需要互動 HTML 頁面」拒絕。
  單獨重跑一次，再連續各開新程序／profile 重跑五次，合計 6 次均通過。
  未穩定重現整套的間歇性失敗，未改 production 導航或延長 timeout，也不宣稱根因
  已修好／整套全綠。先前 1000 ms replay 測試的初始導航失敗本輪未發生，亦不等於
  已證明其根因消失。新增未知封裝與快取案例皆包含於這次 49 個成功案例。
- 實際 `.venv/Scripts/lladar.exe run-agent --help`、變更 Python 模組 compileall 與
  `git diff --check` 通過；README、英／繁中 CLI 參考及操作指南同步快取行為。
  沒有新安裝、實際模型傳輸、外站請求、commit、push 或遠端 CI。

下一步仍包含：整套瀏覽器導航逾時的穩定重現、runtime 更新的快取失效專項證據、更完整的敏感常數／檔案系統安全
corpus、複雜抽取 witness、本機核對拒絕／注入，以及另外授權的真實 provider、
外站生成路徑與發布 commit CI。網站登入 profile 與 parser cache 是獨立狀態；
本輪未清除任何使用者登入狀態。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py tests/test_parser_recording.py tests/test_parser_generation.py tests/test_generated_parser.py -q --tb=short --basetemp .lladar/test-repros/parser-cache-literal-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k 'generated_parser_handles and (generated-json or generated-ndjson)' -q --tb=short --basetemp .lladar/test-repros/parser-unknown-envelope-browser-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-cache-nonbrowser-regression-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-cache-browser-regression-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。

## 2026-09-25：本機核對拒絕／HTML 注入與快取版本門檻

- 真 Chromium 的本機核對頁新增成功／拒絕兩種測試。錄製文字含合成秘密、script、
  img、iframe、meta refresh 與冒充核對指示的 HTML，仍只以完整文字呈現；無注入
  節點、對應外傳請求、dialog 或原頁面異動。接受／拒絕後 review tab 都關閉。
  另經公開 CLI 確認：網站用的 YES 不是 MATCH；拒絕本機核對時只有校正與驗證題，
  零 dataset 請求、無回答檔、無已驗證快取，sidecar 保留 `reference_declined`。
  三個新增 browser 案例 `3 passed`，原實作已符合，不為製造 red 而改動功能。
- 快取新增三個 runtime distribution 版本變動案例：以暫存 `.dist-info/METADATA`
  分別模擬 monty、client、runtime 的不相容版本；已保存的 candidate 不能執行，
  恢復相容 metadata 後仍只是 unverified，需新的獨立驗證。`3 passed`。
  沒有安裝新版／修改既有套件；這證明版本門檻，不是不同 Monty binary 的相容性驗收。
- 私人數字改寫為底線分隔、十六進位、負值或科學記號時，原先 cache guard 會漏查。
  4 個案例先失敗，再補上 recording 數字值與 AST 常數的正規化比對，相關 cache／
  recording／generation／generated parser／CLI policy 集合 `84 passed`。
  AST 檢查不執行程式，亦不取代 Monty；仍不宣稱能辨識任意動態混淆的秘密。
- 隱私檢查加嚴後，舊快取也需失效。使用測試自建檔案與獨立的 .NET DPAPI writer，
  先證明有效保護的舊政策 payload 仍被載入而失敗，再將 cache format 升為 v2，
  同時納入檔名指紋與 payload 檢查。舊檔案不自動遷移／刪除，不沿用舊 verified。
  此版本是保存／隱私政策版本，不是放寬或更換 Monty runtime。
  同一外部 artifact 邊界另驗證有效保護不能繞過 runtime policy、source hash、
  cache identity 與整數版本型別檢查，4 個案例通過。
- 快取 v2 前全非瀏覽器集合：`338 passed, 2 skipped, 53 deselected`（62.92 秒）。
  完整 browser 集合：`52 passed, 1 failed, 340 deselected`（165.24 秒）；新增核對
  拒絕／注入案例均通過。本次失敗是既有
  `test_playwright_replay_stops_at_the_configured_timeout` 在 `/chat` 初始導航的
  1000 ms 期限內未完成，尚未測到 replay；不是前一輪裸 API 的 5000 ms 失敗。
  未延長 timeout、改 production 導航或略過測試，間歇性導航問題仍保留待查。
- 快取 v2 後，最後相關 cache／recording／generation／generated parser／CLI policy
  集合 `89 passed`（12.58 秒），Chromium 快取重開／本機核對拒絕／HTML 注入
  集合 `4 passed`（14.68 秒）。未在這個最後小變更後重跑完整集合。
  變更模組 compileall、README 的 diff check 通過；README 英／繁中補充政策失效。
- 全程僅合成 fixture、固定 provider 與既有 Monty；未新增外站／模型請求、安裝、
  使用者 profile 清除、commit 或 push。

仍須補齊：敏感常數動態重組與檔案系統 corpus、複雜抽取 witness、瀏覽器導航間歇性
失敗，以及分別授權的真 provider／外站／CI。另從 `FrozenParser.verify` 確認目前
以 body hash 排除校正回應；仍需區分「重用舊錄製」與「不同新請求恰好得到完全相同
文字」，避免只憑內容相同而阻擋真正獨立的新觀察。GP1–GP9 仍未全部驗收。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k 'local_review_renders or (generated_parser_handles and YES)' -q --tb=short --basetemp .lladar/test-repros/parser-review-boundary-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py -k changed_runtime -q --tb=short --basetemp .lladar/test-repros/parser-cache-runtime-gate-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py tests/test_parser_recording.py tests/test_parser_generation.py tests/test_generated_parser.py tests/test_parser_policy.py -q --tb=short --basetemp .lladar/test-repros/parser-cache-numeric-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-review-numeric-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-review-numeric-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py tests/test_parser_recording.py tests/test_parser_generation.py tests/test_generated_parser.py tests/test_parser_policy.py -q --tb=short --basetemp .lladar/test-repros/parser-cache-v2-final-focused-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k 'cached_parser or local_review_renders or (generated_parser_handles and YES)' -q --tb=short --basetemp .lladar/test-repros/parser-cache-v2-browser-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。

## 2026-09-25：以請求來源確認獨立性，不要求答案文字不同

- TDD 的真 Chromium／公開 CLI 案例先證明：校正、驗證、資料集三個不同請求都回
  「I do not have that information.」時，舊 body hash 檢查誤擋驗證。修正後三個
  問題各自有一次實際 HTTP 請求，獨立本機核對通過，原樣保留答案，不看 expected_answer。
- `ResponseObservationSource` 只由可信 transport owner 持有，每個 browser driver
  一個來源；每個新請求一個 capture，所有串流 snapshot 留在同一 capture。
  本機 receipt 綁定來源物件、請求物件、request identity、開始序號與原始 response
  body／content type／完成狀態的 hash。它不是網站 payload 聲稱的 ID，不交給
  模型／Monty，也不放入 cache、三欄、一般 sidecar。這依賴可信宿主，不是對抗
  同權限惡意 Python 的密碼學證明；生成程式仍只在 Monty 執行。
- 候選凍結時取得相同序列的 checkpoint，驗證要求同 browser、不同請求、於 checkpoint
  之後開始、request identity 相符且內容未變更。起初以 monotonic 時間比較，在 Windows
  實測會遇到同一刻度；已改成可信宿主遞增序號，不靠 sleep 或放寬先後判斷。
- 保守的 body-hash 排除仍用於沒有 live provenance 的離線 fixture；不把「不同文字」
  宣稱成真 browser 請求證明。帶 provenance 的校正不能混入沒有 provenance 的驗證。
  dataset decode 同樣核對所屬 request，改名重用會封鎖後續解碼。
- 新增八個負例：校正改名、凍結前的舊回應、舊請求的新 snapshot、其他 browser、錯誤
  request、body 變更、完成狀態變更、去掉 provenance；全部拒絕。另驗證 provenance
  不進模型／runtime input、相同答案可以保留、dataset 回應改名後 decoder 進入 blocked。
  一般 sidecar 僅增加既有安全原因碼的明確說明，不印出來源物件或私人材料。
- 相關非 browser 集合 `98 passed`（12.34 秒）；生成／快取／相同答案的真 Chromium
  集合 `11 passed`（43.22 秒）。最後完整集合：非 browser `352 passed, 2 skipped,
  54 deselected`（69.22 秒）；browser `54 passed, 354 deselected`（171.48 秒）。
  本次全綠不等於先前 1 秒／5 秒初始導航間歇性逾時根因已修好；未修改導航或 timeout。
- compileall、README diff check 通過；暫存診斷只輸出來源檢查的布林值，已全部移除。
  PRD 與 README 英／繁中同步獨立性語意；未增加一般使用者參數或授權範圍。
- 嘗試離線 wheel 檢查時，`.venv` 缺少 pip；只讀檢查另確認 build 存在、setuptools／
  wheel 不存在。沒有安裝或建立發行檔。已另詢問是否只在專案 `.venv` 安裝 pyproject
  已宣告的 `setuptools>=68`；未得同意前不繼續這個封裝步驟，不改用隔離環境自動下載。

尚待完整驗收：敏感常數／檔案系統 corpus、複雜抽取 witness、capture buffer 的端到端
資源界限、封裝排除私人產物的實證，以及另外授權的真 provider／外站生成路徑與 CI。
原真網站請求額度沒有增加；未傳模型、未安裝、未 commit／push，GP1–GP9 不宣告全部完成。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_generated_parser.py tests/test_parser_recording.py tests/test_parser_cache.py tests/test_parser_policy.py tests/test_parser_generation.py -q --tb=short --basetemp .lladar/test-repros/parser-request-provenance-corpus-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k 'generated_parser or cached_parser or distinct_browser_requests' -q --tb=short --basetemp .lladar/test-repros/parser-request-provenance-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-provenance-full-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-provenance-full-browser-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。

## 2026-09-25：瀏覽器回應容量與超限停止派送

- 依已確認的 browser transport／公開 CLI seam 做 TDD。首先證明舊流程會接收
  400,000 個三位元組字元（字數小於 1 Mi，UTF-8 大於 1 MiB）；校正、XHR、gzip
  與尚未關閉的 XHR 各自先出現失敗測試，再補足阻擋。不是只測 parser 收到資料後的限制。
- 每次 response 擷取上限為 1,048,576 個解壓後位元組。Fetch 在加入文字緩衝區前
  計算 chunk 大小，超限清空已保留片段並取消擷取 reader；回放另取消自身 request。
  校正只停止 clone，不取消網站原始 request；真 Chromium 測試確認頁面仍能顯示完整內容。
  取消後到達的 chunk 不再累積，也不把超限／取消偽裝為正常完成。
- XHR／完成回應備援在 `response.body()` 前要求相符、已完成的 Chromium 長度觀察。
  僅保留至多兩個 marker 相符 request 的 metadata 與飽和 byte counter；超限的開放
  串流不必等連線關閉才阻擋。沒有可靠長度時不無界讀取。附加觀察在校正結束後 detach。
  大小依 [Chromium Network.dataReceived 的 dataLength](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Network.pdl)
  計算，不能用較小的 `encodedDataLength` 或 Content-Length；本機 gzip 膨脹案例已實測。
- 真 Chromium 邊界案例確認：恰好 1,048,576 UTF-8 bytes 的普通 fetch 與 gzip XHR
  校正／回放可接受；超過上限回報 `BrowserResponseLimitExceeded`，不是截斷答案。
- 公開 CLI 覆蓋校正、獨立驗證、資料集三階段，分別使用 builtin-only 與 model-assisted。
  校正超限時零 provider／自動網站請求，驗證超限時零 dataset 請求；批次途中超限則
  已完成答案保留，之後不再派送。兩種政策皆寫安全 `failure_reason=response_size_limit`，
  未得到完整答案的 record 維持 null；合成 Cookie、request 秘密與超限回應不進模型、
  console 或一般 sidecar。此集合 `6 passed`（17.29 秒）。
  將 provider 輸入的秘密檢查補為未跳脫 Unicode 後，最後再跑同集合 `6 passed`
  （19.62 秒）；未新增模型或外站驗收。
- Driver 中間回歸 `29 passed`（86.57 秒）；加入開放 XHR 後容量／邊界集合 `7 passed`
  （17.49 秒）。完整非 browser 集合 `352 passed, 2 skipped, 67 deselected`
  （63.46 秒）；完整 browser 集合 `66 passed, 1 failed, 354 deselected`（194.68 秒）。
  唯一失敗為 `test_playwright_replay_stops_at_the_configured_timeout` 在 driver 建立時
  `Page.goto(/chat)` 的 1,000 ms 逾時，尚未執行 replay；與先前記錄的初始導航間歇性
  失敗相同。不能將 focused green 宣稱為全套全綠，沒有放寬 timeout 或跳過該測試。
  隨後以五個獨立 pytest process／新 profile 重跑該測試，第一回同樣失敗、後四回通過；
  目前已有單例重現證據（1/5），根因仍待縮小，不將成功重跑當作修復。
- README 英／繁中、PRD、CLI help 同步說明容量與 60 分鐘等待分開；實際
  `.venv/Scripts/lladar.exe run-agent --help` 顯示新說明，compileall 通過。
  `python -m lladar` 不是這個套件的入口，檢查時確認缺少 `__main__` 後改用現有 CLI，
  未為了檢查新增入口或安裝套件。

限制與剩餘工作：這是單次擷取的 response-material 上限，不是瀏覽器程序的總記憶體
限制，也不限制網站原本的 XHR／DOM buffer；request metadata、多個擷取與瀏覽器本身的
資源邊界不能由此宣稱全部驗收。複雜抽取 witness、剩餘安全 corpus 與封裝產物驗證仍待補齊。
再次只讀確認 `.venv` 有 build、沒有 setuptools／pip／wheel；新增建置後端仍等授權，
本輪沒有安裝。未新增內部網站請求、未呼叫真 provider、未 commit／push／觸發遠端 CI；
GP1–GP9 仍不宣告全部完成。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_playwright_driver.py -k 'oversized or one_mib' -q --tb=short --basetemp .lladar/test-repros/capture-open-xhr-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k response_size_limit -q --tb=short --basetemp .lladar/test-repros/capture-limit-preflight-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/capture-limit-full-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/capture-limit-full-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k response_size_limit -q --tb=short --basetemp .lladar/test-repros/capture-limit-cli-final-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。導航重跑另使用
`.lladar/test-repros/capture-timeout-isolated-20260925-1` 到 `-5`；不能覆用來宣稱新的一輪。

## 2026-09-25：可變數量串流片段的抽取證據

- 原本的單一路徑只能選到一個字串或字串清單，無法核對多個 event 內的物件欄位。
  真 Monty TDD 先以 `invalid_extraction_evidence` 失敗，再加入通用終端步驟
  `{"each": subpath}`，依序套用同一 subpath 並連接文字。可巢狀處理事件清單、
  JSON 字串內的結構和物件片段清單；不是加網站、框架或固定欄位特例。
- 校正使用一個 event，獨立驗證使用兩個 event，正式回答使用三個 event，內部片段數
  也不同；同一凍結規則精確保留全文。逆序、漏掉片段、補字及改為僅選最後一段皆拒絕。
  規則切換案例特別使新文字與新路徑相符，仍以 `extraction_rule_changed` 阻擋，
  不是只靠文字不相符碰巧失敗。
- 所有巢狀 path 共用既有 32 步上限；先證明 34 步可繞過舊 top-level 長度檢查，
  再加入先行 grammar 檢查。32 步邊界可接受，34 步拒絕。原始錄製的 1 MiB、
  4,096 nodes、32 層以及 Monty 短時／輸入輸出限制保持不變。each 不能附帶額外
  屬性或後續外層步驟，也不能讓不可信程式在宿主執行。
- 生成契約向 provider 說明這個通用證據操作；政策升為 `monty-0.0.23-parse-v2`，
  Monty 套件仍固定 0.0.23。舊政策 cache 不沿用；新增合法保護容器內舊 policy
  metadata 的拒絕案例。未新增使用者參數，未改三欄、runner 排程、eval 或 report。
- 公開 CLI 的真 Chromium fixture 使用正常登入／表單、開放 SSE、未知 event 與
  巢狀 JSON 字串片段；基準內建解析被證明不能表示此封裝。固定 provider 的第一次
  候選補字，被離線工具拒絕；第二次候選通過，再經另一個新 HTTP 回應與本機 MATCH
  核對，正式取得第三個請求的完整文字。總共三個網站請求、兩次合成模型提案，沒有
  真模型或外站請求。此新增 browser 案例 `1 passed`（7.28 秒）。
- 相關 parser／cache／policy／runtime／四種既有 protocol corpus `183 passed`
  （28.63 秒）；完整非 browser 集合 `360 passed, 2 skipped, 68 deselected`
  （64.98 秒）。完整 browser 回歸已完成：`68 passed, 362 deselected`
  （272.39 秒）；保留一則第三方 Akasha／langchain-experimental deprecation warning。

### 回放逾時測試：排除無關的瀏覽器冷啟動

- 沿用前節實際失敗的測試，縮小為只建立 driver 的本機 harness，10 次中第 9 次
  重現 `Page.goto: Timeout 1000ms`。不需 parser、資料集或 replay 就能出現。
- 只在 `.lladar/test-repros/navigation-startup-20260925/` 的診斷腳本記錄合成流量時間點。
  baseline 25 次觀察中重現多次；瀏覽器 request 事件約在 10–15 ms，server 接受連線／
  收到 GET 卻可能晚於 1 秒，handler 發送本文不到 1 ms，之後 DOMContentLoaded 很快。
  停用代理的十次對照仍有逾時，空白頁暖身的五次對照仍有約 650–893 ms 延遲。
- 已完成本機 HTTP 暖身的十次對照皆通過，第二次導航約 44–197 ms。這支持「測試
  混入首次 HTTP 冷啟動」的定位，不宣稱已查出 OS／Chromium 底層原因，也未更動
  使用者代理設定。原本的 driver 初始導航期限仍可能正確拒絕過慢的冷啟動。
- 僅在回放期限測試的外部 Playwright 啟動 fixture 加入本機暖身；仍啟動真 Chromium、
  呼叫真 driver、向真本機 HTTP endpoint 發送回放。沒有 mock 自有解析／driver 邏輯，
  沒有重試失敗的受測請求、沒有跳過測試；產品的 timeout 不變，受測 replay 仍為
  1 秒，elapsed < 1.8 秒的斷言保留。修改後五個獨立 pytest process／新 profile
  全部通過（5.30、4.71、5.82、4.45、4.49 秒，含 fixture/browser 清理）。
- 預防方式是將瀏覽器準備與回放 SLA 的測試分開。診斷工具與合成 profiles 已明確標記
  為本機 debug 產物而保留；production 與永久測試沒有 `[DEBUG-nav-startup]` 日誌。

尚未完整驗收 GP1–GP9：上述 provider 是固定 fixture，不是真實模型品質證據；
封裝後的私人產物排除、剩餘安全 corpus、已授權後的真 provider／內部網站生成路徑
與發布 commit 的 CI 仍待完成。未新增安裝、外站請求、commit 或 push。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_generated_parser.py tests/test_parser_generation.py tests/test_parser_cache.py tests/test_parser_policy.py tests/test_parser_recording.py tests/test_parser_provider.py tests/test_parser_runtime.py tests/test_response_decoder.py -q --tb=short --basetemp .lladar/test-repros/parser-each-all-protocols-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k generated-fragments -q --tb=short --basetemp .lladar/test-repros/parser-fragments-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-fragments-full-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-fragments-full-browser-20260925
```

上述目錄已使用，再跑請另選新的 `--basetemp`。回放逾時獨立重跑另使用
`.lladar/test-repros/replay-timeout-warmed-20260925-1` 到 `-5`。

## 2026-09-25：答案隔離、宿主行為攻擊與重複 JSON 欄位

- GP2 新增兩次公開 CLI 的成對實測：相同本機網站、相同題目及相同錯誤回答，
  只更換資料集 `expected_answer`（其中一次放入要求改答案的文字）。停用 cache，
  兩次都走真 Chromium、真 Monty、固定 provider 與各自的獨立核對。provider 收到的
  request 完全相同，parser descriptor 相同，兩份 responses 都原樣保留錯誤回答；
  期望答案與實際答案皆不出現在 provider 輸入。共六個本機網站問答請求、兩次提案。
  新增案例 `1 passed, 38 deselected`（12.30 秒）。這是既有隔離行為的回歸證據，
  未修改產品來製造一次失敗。
- GP4 在 Windows 真 Monty 新增五項攻擊：讀取合成 browser-profile Cookie 檔案、
  HTTP POST、Playwright browser／DOM 操作、執行錄製本文中的寫檔腳本，以及 pip
  安裝嘗試。都只送進隔離解析工具；不由宿主 exec／shell 執行。合成 profile 未改動、
  副作用檔案不存在、本機 HTTP 接收器零請求，固定安全錯誤與 console 不含秘密。
  安裝 payload 本身使用 `--no-index --no-deps` 與不存在的測試套件名；沒有真正安裝。
  完整 runtime 集合 `28 passed`（4.24 秒）。這不是其他作業系統的實測宣告。
- GP5 發現 JSON 重複欄位會讓標準 dict 解碼保留最後值，較早的私密值可能在建立
  敏感常數集合前消失。TDD 首先證明一般 JSON 未拒絕；修正後，再證明巢狀 JSON
  字串、NDJSON、SSE 三種封裝仍未拒絕，才逐步補足所有錄製 JSON 入口。現在依已
  解碼的 key 判定重複並回報安全 blocker，不猜應保留哪一個值，也不把歧義 SSE／
  巢狀資料靜默當作普通文字。合法未知欄位仍走原本的 alias 與生成流程。
  相關 recording／generation／generated-parser／cache 集合 `92 passed`（9.63 秒）。
- 公開 CLI 的三個真瀏覽器案例另證明：校正歧義時 provider 不初始化；獨立驗證歧義
  時零 dataset 請求；第二題回應才歧義時保留第一題，其他 `actual_response=null`，
  不再派送後續題目。網站問答請求精確為 1／2／4 次；被覆蓋值、保留值與 Cookie
  均未洩漏到 provider、console 或一般產物。`3 passed, 39 deselected`（11.97 秒）。
  之後將 provider 初始化次數改為在外部邊界記錄、測試結束後斷言，避免 factory
  內的失敗斷言被正常錯誤處理吸收；更新後納入下述完整回歸。
- 更新後完整非 browser 回歸 `369 passed, 2 skipped, 72 deselected`（77.46 秒）；
  完整 browser 回歸 `72 passed, 371 deselected`（259.30 秒）。兩個集合各保留一則
  第三方 Akasha／langchain-experimental deprecation warning。沒有改 runner、三欄格式、
  eval／report、登入方式、生成權限或逾時設定；沒有新安裝、外站或真模型請求。
  compileall 與 tracked diff check 通過，後者僅提示既有 LF／CRLF 換行轉換。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k expected_answer_changes -q --tb=short --basetemp .lladar/test-repros/expected-answer-isolation-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_runtime.py -q --tb=short --basetemp .lladar/test-repros/parser-host-actions-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_recording.py tests/test_parser_generation.py tests/test_generated_parser.py tests/test_parser_cache.py -q --tb=short --basetemp .lladar/test-repros/duplicate-json-wrappers-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k duplicate_json_fields -q --tb=short --basetemp .lladar/test-repros/duplicate-json-cli-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/parser-evidence-audit-full-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/parser-evidence-audit-full-browser-20260925
```

以上 basetemp 已使用，不能覆用作為新的測試紀錄。GP1–GP9 尚未宣告全部完成；
封裝產物檢查仍等建置後端安裝同意，真 provider／內部網站生成路徑及發布 commit CI
各需對應授權與新證據。先前內部網站兩次自動請求額度不因此增加。

## 2026-09-25：Windows 快取目錄連結驗證

- GP7 新增兩個檔案系統邊界案例：選定快取根目錄，或單一網站的快取目錄，被真實
  Windows junction 指向範圍外。目的地仍在各自測試暫存目錄內，包含由真 Monty
  驗證並由真 Windows DPAPI 保護的有效 parser；直接存取的正向對照可載入。
- 經 junction 存取時，`load`／`remember` 均拒絕，`clear` 移除零個項目。目的地
  parser 內容與無關檔案保持不變，直接存取仍可載入。沒有 mock 檔案系統或 cache
  內部函式，也沒有碰觸使用者的 browser profile。
- 第一輪一個案例通過，另一個在建立 junction 前被測試自身的路徑邊界斷言攔下：
  公開 artifact 路徑含 Windows extended-path prefix，而 tmp_path 沒有。改以測試
  已知根目錄與 artifact 的目錄名稱建構同一目的地後，兩個案例均通過；這是測試
  準備問題，不是產品防護失敗。本輪沒有修改 production code，也未製造產品 red。
- 測試結束只以非遞迴操作移除自己建立的 junction；目的地與原本資料保留。POSIX
  分支使用 directory symlink，但這輪只在 Windows 執行，不宣稱已驗證其他平台。
- 新案例 `2 passed, 27 deselected`（1.89 秒）；完整 cache／CLI policy 集合
  `47 passed`（14.21 秒）；真 Chromium 公開 CLI 的跨 run 快取重驗案例
  `1 passed, 41 deselected`（8.91 秒）。後兩者各有一則既有第三方 deprecation warning。
  本輪只有測試與紀錄變更，因此未重跑整個 suite；上一節完整回歸紀錄維持其原範圍。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py -k directory_link -q --tb=short --basetemp .lladar/test-repros/parser-cache-directory-links-checked-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_cache.py tests/test_parser_policy.py -q --tb=short --basetemp .lladar/test-repros/parser-cache-directory-links-corpus-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_browser_workflow.py -k cached_parser -q --tb=short --basetemp .lladar/test-repros/parser-cache-directory-links-cli-20260925
```

上述 basetemp 已使用。再次只讀確認 `.venv` 有 build、沒有 setuptools／pip／wheel；
尚未收到建置後端安裝或真模型資料傳輸的同意，未安裝、未發送真 provider／外站請求、
未 commit／push。GP5 的封裝產物實證，以及 GP9 真 provider、內部網站生成路徑與
發布 commit CI 仍欠對應授權與實際結果，整體目標不能標示完成。

## 2026-09-25：專案內建置與封裝產物檢查

- 使用者同意後，僅在專案 `.venv` 安裝 `setuptools==84.0.0`，符合既有
  `pyproject.toml` 的 `setuptools>=68` 建置需求；沒有更動全域環境或升級其他依賴。
  `pydantic-monty` 維持 0.0.23。沒有將 setuptools 加成執行期依賴。
- 首次以 `build --sdist --wheel` 分開建立產物時，wheel 沿用既有 `build/lib`，
  帶入 20 個目前來源已不存在的舊檔案。這份產物判定不通過，不能交付；未刪除使用者
  原本的 build 目錄。改用 `python -m build --no-isolation` 的預設流程，先建 sdist，
  再於新的建置目錄從 sdist 建 wheel。現有 release CI 也是這個預設流程。
- 將明確標示的純合成 Cookie／parser-cache canary 放在 `.lladar` 私人目錄後，逐檔
  檢查 wheel 與 sdist：沒有私人 runtime 路徑、canary、路徑穿越或 stale package file；
  package 檔案與 `src/lladar` 逐位元組一致，必要模組／skill 資產、CLI 入口及依賴 metadata
  都存在。這是特定路徑與 canary 的排除證據，不宣稱掃出了所有可能的硬編碼秘密。
- 本節最終產物包含下節的 alias 與 Gemini metadata 修正，位於本機忽略目錄
  `.lladar/package-acceptance-20260925/gemini38-dist/`。wheel 52 個檔案，SHA-256
  `c7c577ba5a50b735d973634432ecf5516c2978fe3bb988089fd1e65d61d03606`；
  sdist 84 個檔案，SHA-256
  `924b783c3e9319192183c632e28c920a69f6e5e927f095669f1b860769041de0`。
  直接從 wheel 匯入、真 Monty 解析及封裝後 `run-agent --help` 均通過，沒有安裝覆蓋工作區。
  這是未提交工作區的本機建置證據，不是發布或遠端 CI 通過宣告。

## 2026-09-25：真模型合成驗收與 Gemini 3.8 Flash 相容性

### 已使用的模型額度與結果

- 原先獨立授權的 Gemini 2.5 Flash 額度已用完：共 3 次提案、每次上限 8,192 輸出
  tokens、共用 60 分鐘，全部只傳合成證據。第一次候選未通過新回應；剩餘兩次中，
  一次離線 `parser_failed`，最後一次於獨立驗證回報 `invalid_extraction_evidence`。
  沒有取得已驗證 parser，沒有 dataset 請求，不能列為成功。
- 使用者另同意以 `gemini:gemini-3.8-flash` 開一輪相同上限的合成驗收。只替換這次
  驗收使用的模型；沒有修改全域 `run-agent`、dataset、eval 或 report 模型預設。
  第一次請求在 provider 階段失敗，未取得 source；沒有保留原始回應，因此無法確定
  那一次是何種 provider 錯誤，不能倒推為已證實的簽章問題。
- 修正下述相容性後，在同一輪原始截止時間及剩餘額度內發送第二次請求。這次取得的
  parser 通過離線試跑、新 HTTP 回應的獨立核對、兩筆資料集回放，CLI exit code 0，
  `target_status=verified`、`parser_cache=stored`；同一 source 重用，沒有逐題生成。
  第二段流程用時 25.5 秒，模型 source SHA-256：
  `70b7266f339d9439effd1995c46268ad51d9c56ebf842c738ff7b4017b59d514`。
- Gemini 3.8 這輪實際用了 **2／3 次模型請求**，成功後停止，不用完剩餘額度。
  成功段的本機網站問答精確為 4 次：1 次校正、1 次獨立驗證、2 題資料集；連同第一次
  失敗段共 5 次本機合成問答。沒有新增任何內部網站請求。
  記錄的是要求的模型名稱與設定上限，沒有伺服器解析版本或實際 token／費用明細。

### 別名錯誤的 red → green

- 將實際 Gemini 2.5 最後一份提案以使用者範圍 DPAPI 加密保留在本機診斷目錄，
  解密後只交真 Monty 離線重播，不由宿主 exec。結果確認文字完整正確，但 evidence
  使用了真實欄位名稱，而非穩定 alias。原本合成欄位名稱恰好等於 alias，讓此錯誤
  通過離線試跑，直到新回應才失敗。這項觀察不能套用到未保留的其他候選。
- 先加入公開 trial seam 的失敗測試，證明錯誤 witness 原本會被接受，再令合成 wire
  名稱為 `sample_field_N`／`sample_event_N`，與 `field_N`／`event_N` alias 不同。
  prompt 明確要求讀取本文時解析 symbols，回傳 evidence 時使用字面 alias。
  本機真實名稱綁定、Monty 權限、抽取驗證與資料契約不變，沒有網站欄位特例。
- 修正後重播同一份舊提案，在離線階段即以 `invalid_extraction_evidence` 拒絕，
  不再浪費獨立驗證。生成流程測試另證明可以只用安全 feedback 修正，再獨立驗證並
  重用。相關集合 `109 passed`（23.82 秒）；固定 provider 的真 Chromium 完整流程
  也通過，包含兩次刻意失敗、第三次有效提案與兩題回放，不算真模型品質證據。

### Gemini 回應 metadata 的相容性

- [Google 官方 Part 契約](https://ai.google.dev/api/generate-content#Part) 與
  [thought signatures 說明](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)
  允許文字 part 帶有 `thought`／`thoughtSignature`。本機 HTTP fixture 證明舊的
  「必須只有 text key」檢查會拒絕三種合法文字封裝：三例先 red，再做最小修正。
- 現在僅接受有嚴格型別的文字及上述 metadata，忽略 `thought=true` 摘要和不透明簽章，
  只從最終文字解出 `source`。這是獨立、無工具、無對話歷史的提案，不重播模型 history；
  未增加 function calling、模型工具或宿主執行。工具呼叫、型別錯誤、只有摘要、
  未完成及超大回應仍拒絕。完整 provider 集合 `20 passed`（18.18 秒）。
- 更換模型與上述修正都發生在成功之前，因此這不是只比較模型能力的對照實驗；不能說
  單純換 Gemini 3.8 就保證過關，也不能把真 provider＋合成網站成功宣稱為所有網站支援。

驗收 harness／額度帳本留在本機忽略目錄 `.lladar/parser-live-acceptance-20260925/`。
每次外送前先扣額度，已用帳本不能重跑覆寫；兩段 Gemini 3.8 帳本共用原始 deadline。
provider 僅收到合成結構，沒有內部網站、Cookie、真實回答、題目或 `expected_answer`；
獨立核對依合成網站的已知回應，而非模型自寫斷言。兩題原文精確保留，即使與刻意不同的
`expected_answer` 不符也不改寫。僅可信宿主使用正常瀏覽器流程，生成程式仍只在 Monty 執行。

GP9 的真 provider＋未知格式合成網站取得一個成功案例；其他協定的真模型品質、內部
SSE 網站的生成路徑，以及發布 commit 的遠端 CI 都不由此宣告完成。未 commit、push 或發布。

### 最終回歸與仍未修復的導航間歇性失敗

- 最終 parser／recording／generation／cache／provider 集合 `116 passed`（33.67 秒）。
  完整非 browser 集合 `380 passed, 2 skipped, 72 deselected`（77.76 秒）；兩項
  skipped 為 Windows 不適用的 POSIX signal-delivery 回歸測試。
- 完整 browser 集合 `71 passed, 1 failed, 382 deselected`（266.69 秒）。失敗為
  `test_playwright_replay_classifies_expired_browser_session_without_response_body`，
  建立 driver 的第一次 `Page.goto(/chat)` 超過 3,000 ms，尚未執行驗證登入逾期的
  replay，也尚未進入生成 parser。沒有放寬產品或測試逾時，沒有跳過該測試。
- 新 profile／獨立 pytest process 重跑該例 `1 passed, 29 deselected`（3.03 秒）。
  這只證明單例可通過，不表示已修復導航間歇性問題，不能把本輪 browser suite 改記全綠。
  與先前導航問題的底層原因是否相同仍未證實；本輪不擴大修改無關瀏覽器初始化。
- 完整兩組皆有一則既有 Akasha／langchain-experimental deprecation warning。
  compileall、tracked diff check 通過；後者另有既有 LF／CRLF 提示。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_recording.py tests/test_generated_parser.py tests/test_parser_generation.py tests/test_parser_cache.py tests/test_parser_provider.py -q --tb=short --basetemp .lladar/test-repros/gemini38-parser-final-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/gemini38-full-nonbrowser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m browser -q --tb=short --basetemp .lladar/test-repros/gemini38-full-browser-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_playwright_driver.py -k classifies_expired_browser_session -q --tb=short --basetemp .lladar/test-repros/gemini38-navigation-check-20260925
```

以上 basetemp 均已使用，重跑請選新目錄。完整回歸與封裝檢查不額外呼叫真模型或內部網站。

## 2026-09-25：3 秒初次導航逾時的隔離診斷（未修復）

- 繼續使用本機 `navigation-startup-20260925/probe.py`，只建立真 driver、使用新的
  合成 profile、開啟合成 `/chat`，不做 replay 或 parser。新增 `--timeout` 診斷參數，
  attempts 101–112 使用 3 秒；第 107 次重現 `Page.goto` 逾時，其餘 11 次成功。
  這把失敗從整套測試縮小到初始化本身，不需要登入失效、生成程式或模型請求。
- 同目錄 `timeline.py` baseline attempts 101–112 的導航皆完成，約 703–1,670 ms；
  request 事件約 10–18 ms，較長延遲在 server accept／讀到 GET 之前。此組沒有再次
  超過 3 秒，不能假裝每次都重現。伺服器送出本文之後到 DOM 完成相對短。
- warm-http attempts 101–112 的成功案例，第二次導航約 112–509 ms；但第 106、108
  次在前置 `/blank` 的 5 秒期限已失敗。因此暖身不能宣稱是底層問題修復。
  其中後半組與下面的兩例 targeted pytest 曾並行，故不作嚴格的效能因果比較。
- 曾嘗試把現有 HTTP 暖身設定共用到登入失效測試，targeted 結果 `1 passed, 1 failed`
  （26.71 秒），失敗也在前置 `/blank`，不是受測 replay。這項候選測試修改未證實改善，
  已撤回；原本測試與產品的 1／3 秒限制都保留，沒有延長期限、加重試或跳過測試。
- 上述工具和合成 profiles 留在明確標示的本機診斷目錄；沒有把 debug 日誌加進產品。
  尚不能判定 Windows／Chromium 底層原因；先保留這個未解項，不把先前整套失敗改記通過。

隨後收到新的真網站／Gemini 3.8 有界驗收授權，依使用者可手動配合的時間優先進行。
該輪使用新的獨立帳本與全新 LLaDAR-owned profile；先前網站額度與純合成模型額度不重置。

## 2026-09-25：真網站生成試跑失敗與修正上下文

- 新授權為 Gemini 3.8 Flash 最多 3 次提案、每次上限 8,192 輸出 tokens、共用
  60 分鐘，以及最多 1 次自動驗證與 1 題資料集問題。使用者手動校正後確實擷取到
  POST／SSE；模型只收到去識別的合成結構，未傳原始問題、答案、Cookie 或網站位址。
- 第一份生成程式在同類 SSE event 中略過缺少指定欄位的訊息，但宣告的 `each`
  evidence 表示逐項抽取所有訊息，兩者不一致，離線以 `invalid_extraction_evidence`
  拒絕。第二次提案在 provider 階段失敗，未取得 source；當時僅留下通用
  `provider_failed`，沒有保存原始 provider 回應，故無法回溯確定該次服務錯誤原因。
  此段用掉 **2／3 次模型請求、0／2 次自動網站問題**，CLI exit code 2，並非成功驗收。
- 依失敗案例檢查修正流程，發現每次 provider 請求是獨立的，原本只提供安全 feedback，
  未提供上一版候選程式。兩項公開 generation seam 測試先因缺少 `previous_source`
  失敗，再補上最後一份模型生成 source；該 source 明確標示為不可信資料，不帶入
  真實錄製、私密值或獨立驗證回應。首輪不帶此欄位，越權候選仍立即停止。
- prompt 另說明既有規則：負索引由尾端計算、`each` 並非 filter，任一項缺少路徑即
  無效。沒有新增網站欄位名稱、DSL 操作、放寬 witness 驗證或擴大 Monty 權限。
- provider 現在把 HTTP／未完成／無效／超大輸出轉成固定原因碼；不讀取 HTTP 錯誤
  本文、不輸出私密診斷、不跟隨 redirect、不自動 retry。CLI seam 的 9 種失敗情境
  先全數因原因碼被折疊成通用錯誤而失敗，再補上允許名單及固定使用者訊息。
  網路／未知錯誤仍可能回報通用 `provider_failed`，不宣稱已辨識先前第二次的原因。
- generation／provider／CLI policy 集合 `67 passed`（34.29 秒）；完整非 browser
  集合 `393 passed, 2 skipped, 72 deselected`（116.36 秒）。兩項 skip 仍為 Windows
  不適用的 POSIX signal-delivery 測試；既有 Akasha deprecation warning 仍在。
  本段未重跑完整 browser suite，前述導航間歇性失敗仍未修復。
- 以預設 sdist → wheel 流程重新建置至本機忽略目錄
  `.lladar/package-acceptance-20260925/site-repair-dist/`，逐檔來源一致、私人路徑與
  canary 排除、metadata、CLI entrypoint、wheel 匯入與真 Monty smoke 均通過。
  wheel 52 檔，SHA-256 `ce5f023d47a2eb0fa6eb5721c2170a8de4dd48c1b47e01b99f50dd31571a6fe7`；
  sdist 84 檔，SHA-256 `3aba3db0a1f96f1bd6ca0f1dd5cff1f9400c63f1692b96214e684223b3e2dfe8`。
  compileall 與 tracked diff check 通過，沒有安裝新套件、commit、push 或發布。
- 原始校正只在已關閉程序的記憶體中，沒有以合成樣本冒充真實獨立驗證。使用者另同意
  重開同一 LLaDAR-owned profile 再校正；續行帳本明列已使用的 2 次提案，沿用原始
  2026-09-25 15:35:29（UTC+8）deadline，最多再 1 次提案與尚未使用的 2 次自動網站
  問題，不重置額度。使用者再次完成手動校正，成功擷取 POST／SSE 後，在原始期限內
  送出第 3 次模型請求。此請求回報固定原因碼 `provider_output_incomplete`，沒有取得
  可試跑 source；CLI exit code 2、`target_status=parser_blocked`，沒有自動 retry。
  這只表示 provider 的完成狀態不是正常完成，不足以判定是否輸出 token 用盡、安全
  阻擋或其他結束原因，也不是網站 request timeout 或校正失敗的證據。
  本輪合計 **3／3 次模型請求、0／2 次自動網站問題**，未產生 responses JSONL，
  真網站生成解析驗收仍未通過。程序正常收尾並關閉本輪瀏覽器，保留專用 profile；
  後續模型請求須另有新授權，不能以尚餘網站額度或尚未到 deadline 為由繼續呼叫。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_generation.py -q --tb=short --basetemp .lladar/test-repros/parser-repair-source-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= tests/test_parser_policy.py tests/test_parser_generation.py tests/test_parser_provider.py -q --tb=short --basetemp .lladar/test-repros/provider-cli-status-green-20260925
.venv/Scripts/python.exe -m pytest -o addopts= -m 'not browser' -q --tb=short --basetemp .lladar/test-repros/site-repair-nonbrowser-20260925
.venv/Scripts/python.exe -m build --no-isolation --outdir .lladar/package-acceptance-20260925/site-repair-dist
```

上述 basetemp／封裝目錄均已使用；重跑請另選新目錄。真網站帳本與私密診斷留在
`.lladar/live-generated-site-20260925-1/`；不能藉重跑 helper 重置其授權。
