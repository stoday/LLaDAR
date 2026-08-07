# LLaDAR PRD：偏見偵測測試資料集生成

## Problem Statement

大語言模型 agent 在知識庫資訊不完整時，可能為了繼續回答而自行補上未被證實的資訊。這種 unsupported assumption 可能與使用者原本的預期不同，造成具有偏見的回答。

這個問題不是第一階段所稱的性別、年齡、種族或宗教分類偏見，而是「資訊缺失時，agent 是否把單一推測當成事實」。例如，知識庫只說某位殺人犯是某個女兒的父親或母親，但問題移除這項資訊後，agent 不應直接假設答案一定是「爸爸」或「媽媽」。

目前專案只有基本的 Python／Akasha 參考程式，尚未具備可安裝的 LLaDAR 套件。需要建立第一個產品功能：從文件知識庫生成可供未來 agent evaluator 使用的對照測試資料集。

## Solution

建立可透過 pip 安裝的 `lladar` Python 套件，提供 Python API 與 CLI，完成以下流程：

1. 讀取單一檔案、資料夾或路徑清單中的 `.txt`／`.md` 文件。
2. 將文件依段落、句子與字元邊界切成 chunks。
3. 透過 Akasha 呼叫 `gemini:gemini-2.5-flash`。
4. 由模型找出一個會影響答案、但容易被忽略的關鍵資訊。
5. 為每個 chunk 預設生成一組對照問題：完整資訊問題與移除關鍵資訊後的 underspecified question。
6. 產生完整答案、缺失資訊、錯誤假設與可接受行為。
7. 驗證生成結果，必要時重試，最後輸出 JSONL 或 JSON。

未來產品路線包含第二個功能「執行 agent 評測」及第三個功能「生成評測報告與改善建議」；本規格只涵蓋第一個功能。

## User Stories

1. 作為 Python 開發者，我想以 `python -m pip install lladar` 安裝套件，以便在自己的專案中使用資料集生成能力。
2. 作為 Python 開發者，我想呼叫 `lladar.create_test_dataset(...)`，以便從知識庫生成測試資料。
3. 作為命令列使用者，我想使用 `lladar create test-dataset`，以便不撰寫 Python 程式也能生成資料集。
4. 作為使用者，我想提供單一文件，以便只針對特定知識內容產生測試資料。
5. 作為使用者，我想提供資料夾，以便遞迴處理整個知識庫。
6. 作為使用者，我想提供路徑清單，以便組合多個指定文件。
7. 作為使用者，我想處理 `.txt` 與 `.md` 文件，以便使用常見的純文字知識庫格式。
8. 作為使用者，我想設定 chunk 大小，以便控制每次送給模型的知識範圍。
9. 作為使用者，我想設定 chunk overlap 比例，以便保留相鄰文字的上下文。
10. 作為使用者，我想讓系統優先在段落與句子邊界切割，以便減少語意被截斷。
11. 作為使用者，我想指定內建的 `ambiguity` 策略，以便生成資訊缺失下的 unsupported assumption 測試。
12. 作為使用者，我想提供自訂 prompt 文字，以便控制問題生成策略與限制。
13. 作為使用者，我想提供 prompt 檔案，以便保存可重複使用的生成策略。
14. 作為使用者，我想在同一 chunk 上生成多組問題，以便透過 `num_pairs` 增加測試變化。
15. 作為使用者，我想讓同一組問題維持相同的主要缺失資訊，以便不同問法仍能測試同一風險。
16. 作為使用者，我想看到完整問題，以便知道加入完整資訊後的預期情境。
17. 作為使用者，我想看到 underspecified question，以便測試 agent 是否會承認資訊不足。
18. 作為使用者，我想看到完整答案，以便比較 agent 在資訊完整時的回答。
19. 作為使用者，我想看到完整的原始 chunk，以便追溯答案的知識來源。
20. 作為使用者，我想看到明確的 `missing_information`，以便知道問題刻意移除了什麼。
21. 作為使用者，我想看到 `invalid_assumptions`，以便知道哪些單一推論是不被來源支持的。
22. 作為使用者，我想看到 `acceptable_behaviors`，以便未來 evaluator 能接受要求澄清、列出可能性或聲明資訊不足等行為。
23. 作為使用者，我想看到 `bias_type=unsupported_assumption`，以便區分這類風險與未來其他 bias type。
24. 作為使用者，我想保存來源檔案與 chunk index，以便定位資料集項目的原始位置。
25. 作為使用者，我想輸出 JSONL，以便處理大型資料集。
26. 作為使用者，我想輸出 JSON，以便與需要一般 JSON 陣列的工具整合。
27. 作為使用者，我想指定輸出檔案，以便控制資料集的保存位置。
28. 作為使用者，我想避免意外覆寫既有資料集，以便保護已產生的模型結果。
29. 作為使用者，我想使用 `--force` 明確覆寫資料集，以便在確認後重新生成。
30. 作為使用者，我想指定模型，以便使用預設模型以外的 Akasha 相容模型。
31. 作為使用者，我想從環境變數或 `.env` 取得 API 設定，以便不將秘密放進原始碼或資料集。
32. 作為使用者，我想在文件包含指令文字時仍將它視為不可信資料，以便避免知識內容注入生成流程。
33. 作為使用者，我想在單一項目失敗時繼續處理其他 chunks，以便大型批次不因一筆錯誤全部中斷。
34. 作為使用者，我想使用 strict 模式，以便任何輸入或生成錯誤都能使流程失敗。
35. 作為使用者，我想看到成功、跳過與失敗數量，以便判斷資料集是否完整。
36. 作為使用者，我想讓 malformed JSON 自動重試，以便降低模型格式不穩定造成的失敗。
37. 作為使用者，我想使用 fake provider 測試流程，以便不依賴真實模型 API 驗證程式行為。
38. 作為使用者，我想選擇性啟用本地 cache，以便避免重複支付相同的模型請求。
39. 作為使用者，我想清除或刷新 cache，以便在 prompt、模型或策略變更後重新生成。
40. 作為未來 evaluator 開發者，我想依賴穩定的 schema version，以便在未來增加評測功能時保持相容。
41. 作為套件維護者，我想讓公開 API 與 CLI 保持穩定，以便後續新增 evaluator 與報告功能時不破壞資料集生成使用者。
42. 作為使用者，我想閱讀 README、CLI、schema 與自訂 prompt 範例，以便快速開始並正確擴充策略。
43. 作為使用者，我想預設看到帶有本地時間、階段、耗時與預估剩餘時間的生成進度，並可關閉它，以便掌握大型資料集的執行狀態。

## Implementation Decisions

- 使用 `pyproject.toml` 建立可安裝套件，支援 Python 3.11、3.12 與 3.13。
- 公開 Python 入口為 `lladar.create_test_dataset`。
- 公開 CLI 入口為 `lladar create test-dataset`。
- 主要生成 seam 是 `create_test_dataset`；CLI 應是同一行為的薄介面。
- 內部模組分為 API、CLI、資料模型、例外、loader、chunking、prompt、generation、validation、cache 與 provider 層。
- 定義 `LLMProvider` 抽象介面，第一版實作 `AkashaProvider`。
- Akasha provider 使用 `gemini:gemini-2.5-flash` 作為預設模型，支援 `model`／`--model` 覆寫。
- Akasha provider 負責讀取 `.env`、建立 agent、消費 stream event、取得最終文字並解析結構化 JSON。
- 生成邏輯不直接依賴 Gemini SDK，也不把 Akasha 細節暴露給資料集生成核心。
- 每個 chunk 一次模型呼叫生成一整組 pair，以降低欄位不一致與 API 成本。
- 每個 chunk 預設生成一組 pair；`num_pairs` 生成同一主要缺失資訊的多種問法。
- 內建策略名稱為 `ambiguity`；未指定 prompt 時使用此策略。
- prompt 來源優先支援內建策略、自訂 prompt 文字與 prompt 檔案；同時指定多種來源時報錯。
- 知識來源接受單一路徑、資料夾與路徑清單，資料夾遞迴讀取 `.txt`／`.md`。
- 預設使用 UTF-8；檔案讀取錯誤依 `strict`／`best-effort` 處理。
- 檔案先依標準化相對路徑排序，再依 chunk index 處理，以提高可重現性。
- 空白檔案與空白 chunk 跳過；重複 chunk 預設去重並保留來源資訊。
- `chunk_size` 使用字元數；`overlap` 是前一 chunk 大小的比例，限制為 `0 <= overlap < 1`。
- chunking 優先按段落、句子切割，只有超長句子才按字元硬切。
- 輸出每筆資料包含 `schema_version`、來源、chunk、完整問題、完整答案、資訊缺失問題、缺失資訊、錯誤假設、可接受行為、bias type 與 metadata。
- `source_text` 是主要 ground truth；完整答案可由模型整理，但必須能由來源 chunk 支持。
- 第一版 `bias_type` 固定為 `unsupported_assumption`。
- `acceptable_behaviors` 第一版支援 `ask_clarification`、`list_possibilities` 與 `state_insufficient_information`。
- 模型回應必須是結構化 JSON；解析或驗證失敗最多重試三次。
- 驗證需檢查來源可追溯、缺失資訊非空、完整問題包含關鍵資訊、缺失問題未直接揭露關鍵資訊，以及錯誤假設與缺失資訊相關。
- 預設為 `best-effort`；輸入或生成失敗的項目記錄並跳過。`strict` 模式遇到錯誤即失敗。
- 單次執行失敗率超過 5% 時顯示警告；strict 模式直接失敗。
- 第一版循序處理 chunks，不提供 async 或並行生成；保留未來增加 `max_concurrency` 的設計空間。
- Python API 在指定 output 時寫檔，但仍回傳 `list[dict]`；未指定 output 時只回傳資料。
- CLI 預設輸出 `test-dataset.jsonl`；`--format json` 輸出一般 JSON 陣列。
- 既有輸出檔案預設報錯，只有 `--force` 才能覆寫。
- cache 預設關閉；啟用後放在 `.lladar/cache/`，以 chunk、prompt、model 與相關參數雜湊為 key。
- `--refresh-cache` 或對應 API 參數可強制重新生成。
- 定義 `LladarError`、`KnowledgeLoadError`、`ChunkingError`、`ProviderError`、`GenerationError` 與 `DatasetValidationError`。
- 支援 provider injection，以便 fake provider 測試與未來替換模型供應商。
- Python API 的 `verbose` 預設為 `True`；CLI 提供預設啟用的 `--verbose` 與 `--no-verbose`。進度一律寫入 stderr，包含有效的非秘密配置、來源、semantic window、chunk、cache、retry、pair、寫檔與完成狀態；每行使用本地時間，真實 TTY 的標籤使用 ANSI 顏色，pair 顯示 elapsed 與 best-effort ETA。不得輸出 prompt 內容、環境檔內容、credential 或 provider exception 訊息；Akasha 自身 raw verbose 維持關閉。

## Testing Decisions

- 測試以公開 API 的外部行為為最高層 seam，CLI 測試只驗證參數解析、輸出與錯誤映射，不重複測試生成核心。
- loader 測試涵蓋單檔案、資料夾遞迴、路徑清單、UTF-8、空檔案與錯誤輸入。
- chunking 測試涵蓋字元大小、overlap 邊界、段落／句子優先切割、排序、去重與來源追蹤。
- prompt 測試涵蓋 `ambiguity`、自訂文字、prompt 檔案、來源衝突與預設策略。
- schema／validation 測試涵蓋完整欄位、缺少欄位、非法值、來源追溯、資訊移除與可接受行為。
- generation 測試使用 fake provider 驗證成功流程、malformed JSON 重試、三次失敗、strict／best-effort 與 `num_pairs`。
- cache 測試涵蓋命中、未命中、參數變更造成 cache miss 與 refresh。
- CLI smoke test 驗證 JSONL、JSON、預設輸出、`--force`、進度摘要與非零錯誤碼。
- package test 驗證可建立 distribution，並可在乾淨環境安裝後匯入 `lladar` 與執行 CLI。
- 真實 Akasha／Gemini 測試為 opt-in，不納入一般離線測試流程。
- verbose 測試從公開 API／CLI seam 驗證預設啟用、stderr 分流、本地時間、TTY 顏色、配置與各階段事件、elapsed／ETA、`--no-verbose`，以及 provider 錯誤內容不會洩漏到進度。
- 品質門檻是每筆資料通過 schema 與語意驗證；生成失敗率超過 5% 必須顯示警告。

## Out of Scope

- agent 評測執行器。
- 評測報告、統計分析與改善建議生成器。
- 傳統性別、年齡、種族、國籍、宗教或身心障礙偏見分類器。
- PDF、DOCX、HTML、CSV、JSON 等非純文字 loader。
- 直接整合 Gemini SDK、Anthropic、OpenAI 非 Akasha provider、Ollama 或其他本地模型。
- async API、並行生成、遠端 cache 與資料庫。
- Web UI、雲端服務、排程與多使用者管理。
- 自動移除、遮蔽或上傳原始文件中的敏感資訊。
- 跨模型版本的完全 deterministic 保證。
- 建立或修改第三方 issue tracker ticket。

## Further Notes

- 本 PRD 描述的是第一個功能，而不是整個 LLaDAR roadmap 的終點。
- 未來 evaluator 應能直接使用 `missing_information`、`invalid_assumptions` 與 `acceptable_behaviors`。
- 以「父母、女兒、爸爸／媽媽」案例作為 README 與測試資料的核心示例。
- 文件內容一律標記為不可信資料；模型不得將文件中的指令視為系統或開發者指令。
- cache 可能包含原始 chunk 與模型回應，應加入 Git ignore，並提醒使用者妥善管理。
- 這份 PRD 確認前不開始套件實作；確認後再依 MVP acceptance criteria 建立實作與測試。

## Auto Semantic Chunking Addendum

- `chunk_size` accepts a positive integer or the literal `"auto"`.
- Auto mode is a separate model stage before contrastive-pair generation.
- The library labels exact source units; the model returns contiguous unit IDs and concise facts, and the library extracts final passages from original offsets.
- A semantic window uses 80% of the selected model's maximum output-token limit as a conservative character budget. Gemini 2.5 Flash therefore uses 52,428-character windows. Larger documents are processed window by window with 10% internal overlap; final chunks are deduplicated by source offsets.
- Public `overlap` is ignored in auto mode.
- Auto metadata records `chunk_method`, `source_start`, `source_end`, and `knowledge_facts`.
- Semantic segmentation uses a cache namespace separate from generated-pair cache entries.
- After three invalid segmentation responses, strict mode raises `ChunkingError`; best-effort mode falls back to non-overlapping 800-character chunks.
- This first strategy selects one important fact per generated pair. A future strategy may generate multiple questions from one semantic passage.
- Model input/output limits and the auto-window ratio are resolved from one model profile; provider and chunking code must not duplicate those constants.
- Python users may override max_input_tokens, max_output_tokens, and auto_window_ratio. CLI users may override them with --max-input-tokens, --max-output-tokens, and --auto-window-ratio.
- Gemini 2.5 Flash defaults to 1,048,576 input tokens, 65,536 output tokens, and ratio 0.8. Unknown models use conservative 16,384/8,192 token limits.
## 後續待辦：生成問題的語意品質

狀態：**尚未實作，後續版本必須處理。** 現行 validate_generated_pair() 只驗證欄位與 schema；模型輸出即使格式正確，仍可能不是有效的 unsupported-assumption 對照題。

真實 knowledge/diet.md 測試已觀察到弱對照案例：完整問題使用「血糖平緩上升」，移除資訊後只剩「血糖上升」。這種改寫未必產生多個合理答案，因此不足以可靠測量 agent 是否腦補資訊。

後續品質驗證／過濾至少需要檢查：

- 移除 missing_information 後，問題必須真的資訊不足，且存在兩個以上由來源支持的可能答案。
- 完整問題與 underspecified question 必須維持相同實體、任務、關係與要求的答案類型，只能移除一項關鍵資訊。
- complete_answer 必須由 source_text 支持；問題不得憑空引入會改變推理的新人物、產品、條件或背景。
- missing_information 必須是唯一主要差異，不能只是刪除形容詞、改寫語氣或降低精確度。
- invalid_assumptions 必須是資訊缺失時可能出現、但未被來源證實的單一答案。
- 應加入語意品質分數或第二階段 judge/filter，低品質資料預設不輸出，strict 模式則回報明確失敗原因。
- 應使用 knowledge/diet.md 的真實案例與人工標註的正反例建立回歸測試，不能只驗 JSON schema。
