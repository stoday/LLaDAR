# PRD：產生可編修的 LLaDAR 階段 Skill 範本

狀態：已在本機工作樹實作，尚未發布
日期：2026-10-02
作者指南修訂：2026-10-05
所屬流程：create → run-agent → eval → report

## Problem Statement

LLaDAR 的公開命令可用 --skill DIRECTORY 選擇方法，但使用者難以從這個選項知道各階段 agent 會收到什麼請求、能呼叫哪些工具、必須提交什麼結果，以及哪些規則由 LLaDAR 主程式控制。現有內建 Skill 適合作為精簡的執行指令，卻不足以作為自訂 Skill 的完整作者指南。使用者往往得閱讀內部程式碼、猜測工具契約，才能寫出能被載入並完成工作的 SKILL.md。

差異依階段而異：create test-dataset 有多個工具作用範圍不同的生成階段；run-agent 的內建排程可直接執行隨附策略，但自訂 Skill 走 agent 產生排程的路徑；eval 對部分題型及 probe 使用確定性判定；report 只讓 agent 撰寫有證據界限的敘述。正在開發的 create situation 也有獨立的 authoring Skill，不能與情境執行或情境評估的固定方法混為一談。

## Solution

新增五個不呼叫模型的範本產生命令：

    lladar create test-dataset-skill [--output DIRECTORY] [--force]
    lladar create situation-skill [--output DIRECTORY] [--force]
    lladar create run-agent-skill [--output DIRECTORY] [--force]
    lladar create eval-skill [--output DIRECTORY] [--force]
    lladar create report-skill [--output DIRECTORY] [--force]

未指定 --output 時，在目前工作目錄建立 ./lladar-skills/<stage>/；stage 依序為 test-dataset、situation、run-agent、eval、report。指定 --output 時，該路徑就是 Skill 目錄，不再加上一層階段目錄。每個範本包含可直接載入的 SKILL.md，以及供人閱讀的 AUTHORING.md。SKILL.md 從對應的現行內建方法出發，提供可修改、可運作的完整指令；AUTHORING.md 說明該階段的請求、可用工具與參數、必要提交、完成條件、主程式持有的驗證，以及最小使用範例。

成功時印出實際建立的目錄及使用該目錄的完整 --skill 命令。目的目錄已存在時預設停止；五種範本命令均接受 `--force`，明確覆寫 `SKILL.md` 與 `AUTHORING.md`，保留其餘檔案。覆寫失敗時嘗試還原原檔。產生命令本身不需要 provider 憑證，也不執行受測 Agent。

## User Stories

1. 身為第一次自訂 LLaDAR 方法的使用者，我希望用一條命令取得可運作的 Skill，讓我從已知可用的內容開始修改。
2. 身為測試資料作者，我希望取得 test-dataset 範本，讓我知道知識點、語意圖和測試計畫的階段順序與完成條件。
3. 身為情境測試作者，我希望取得 situation 範本，讓我能修改情境設定的生成方法，而不誤認它會控制後續對話執行。
4. 身為受測 Agent 的測試者，我希望取得 run-agent 範本，讓我能修改案例選取與重複次數的策略。
5. 身為評估方法作者，我希望取得 eval 範本，讓我能修改開放式回答的評估指令，並知道哪些判定由主程式確定性處理。
6. 身為報告作者，我希望取得 report 範本，讓我能修改摘要與發現的寫法，同時保留原始評估數字。
7. 身為 Skill 作者，我希望看到每個階段的請求欄位與工具名稱、參數及回傳資訊，讓我不必閱讀內部 Python 才能撰寫指令。
8. 身為 Skill 作者，我希望看到必須呼叫的提交工具與成功條件，讓 agent 不會只輸出文字卻沒有交付結果。
9. 身為 Skill 作者，我希望看到工具的階段作用範圍，讓我不會在錯誤階段要求 agent 呼叫不可用工具。
10. 身為 Skill 作者，我希望清楚區分 Skill 可決定的內容與主程式驗證、資料契約、確定性計算，讓修改的效果可預期。
11. 身為 Skill 作者，我希望產生的 SKILL.md 能直接由既有 --skill DIRECTORY 載入，讓我可以先執行基準版本再逐步修改。
12. 身為將 Skill 放在自訂路徑的使用者，我希望範本 metadata 與目錄名稱一致，讓載入與執行證據檢查都能成功。
13. 身為在同一專案撰寫多個方法的使用者，我希望每種範本有獨立的預設目錄，避免彼此覆蓋。
14. 身為已有自訂 Skill 的使用者，我希望碰到既有目的目錄時命令安全地停止，避免我的修改遺失。
15. 身為在 CI 或離線環境工作的使用者，我希望產生範本時不需要 API key、模型呼叫或目標 Agent，讓建立範本是可重現的本機動作。
16. 身為使用者，我希望命令輸出一條可複製的使用範例，讓我能立即知道產物如何接回原本流程。
17. 身為維護者，我希望範本中的工具與完成條件和目前執行契約同步，讓功能演進時不會留下過時的作者指引。
18. 身為維護者，我希望以公開 CLI 驗證產物可用，而不只檢查範本含有特定文字，讓測試涵蓋實際使用流程。

## Implementation Decisions

- 作者指南須列出用途、可修改規則、參數資料結構、完整階段工具清單、回傳值及完整提交範例。不能只把現行內建指令複製給使用者。
- run-agent 分別說明 Agent 工具與 Python 策略入口，定義 `cases`、案例屬性、`schedule`、回傳值與重複排程累加行為，提供全量、抽樣及條件選取範例，列出受限 Python 環境提供的函數。
- `AUTHORING.md` 承擔人類撰寫指南的角色，不新增重複的 guide.md。它不會自動註冊為 Skill，但仍可被 Agent 作為參考資源讀取；檔名不能作為執行隔離。明確標示範例為替代方案，實際行為以 SKILL.md 的單一策略為準。

- 五個 create <stage>-skill 子命令分別對應 create test-dataset、create situation、run-agent、eval、report 的 --skill 入口。新命令只建立 Skill，不執行原階段工作。
- --output 一律表示最終 Skill 目錄。預設值為 ./lladar-skills/<stage>/，相對於呼叫時的工作目錄；命令回報解析後的位置。
- 每個產物至少有有效的 SKILL.md 和人類作者指南 AUTHORING.md。作者指南不構成執行契約；即使 agent 不讀取它，SKILL.md 仍須能獨立完成對應工作。
- 範本取材於同版本的內建方法，不建立與內建行為分岔的第二套方法。產生的 SKILL.md 應可直接使用，且含清楚可修改的策略區段；不得以未完成占位字句取代必要步驟。
- 產生時依目錄 basename 設定並驗證 Skill frontmatter 的 name。若使用者指定的目錄名稱不符合 Skill 載入規則，命令在寫入前以可操作的訊息拒絕。這也適用於自訂 --output 路徑。
- 作者指南逐一說明本階段 agent 可見的請求、工具呼叫格式、返回資訊、必要提交和完成條件；資料生成需涵蓋其多階段工具作用範圍。指南使用目前載入及主程式驗證的術語，不宣稱不存在的工具。
- run-agent 指南明確說明內建預設策略可直接執行隨附程式；複製到自訂路徑後，--skill 會走自訂 Skill 的 agent 排程流程。範本不能暗示複製內建 strategy.py 就能保有內建快速路徑。受測 target Agent 與排程 agent 是不同角色。
- eval 指南明確說明開放式回答可走 Skill 評估，具答案協定的題型與部分 probe 由主程式確定性判定；彙總與穩定度由主程式計算。report 指南明確說明 Skill 只提交敘述，數字與表格取自保存的評估結果。
- situation-skill 對應情境設定的 authoring Skill。指南說明情境執行及 --situation-config 評估模式目前不能改由一般 run-agent 或 eval 的 --skill 取代。
- 目的目錄已存在時預設停止；`--force` 僅覆寫 SKILL.md 與 AUTHORING.md，保留其他檔案，失敗時嘗試還原原檔。輸出路徑不可建立或 metadata 不合法時，回報明確錯誤；避免留下可被誤認為完整 Skill 的部分產物。
- 此功能沿用目前單一 --skill DIRECTORY 載入介面、Akasha Skill 格式和現有主程式驗證，不另建 Skill registry、套件安裝流程或新的 agent 工具集。

## Testing Decisions

- 以公開 CLI 為主要測試介面：對五個新命令檢查預設與指定輸出位置、建立的檔案、成功訊息、既有目錄拒絕、非法目錄名稱及失敗後無部分產物。測試不依賴 provider 或網路。
- 產物以現有 Skill 載入介面驗證，並用各階段的離線 agent 替身或現有 fixture 走最小工作案例。測試應確認必要工具可由範本引導呼叫、結果能被主程式接受，而非逐字比對 Skill 敘述。
- 對 run-agent 特別驗證產生的自訂 Skill 走 agent 排程路徑，並與內建策略的免模型路徑區分；對 eval 驗證範本不改變確定性判定；對 report 驗證 Skill 只能改變敘述，不改變統計表。
- 用與工具註冊及階段契約相連的測試檢查作者指南列出的工具和必要提交，降低文件與實作漂移的風險；避免只測文件內的固定字串。
- 沿用既有資料生成 Skill、單題 CLI 流程和情境流程測試方式，將新測試放在同等的公開介面層；只在確認產物可載入且可完成最小案例所需時增加低層測試。

## Out of Scope

- 不新增 lladar skill validate、互動式編輯器、模型協助改寫 Skill 或自動試跑命令；作者指南可示範如何用既有命令跑最小案例。
- 不改變四階段的資料格式、工具權限、驗證邏輯、預設模型、內建方法或受測 Agent 的執行方式。
- 不讓 Skill 覆寫確定性評估、統計、報告表格、情境執行規則或主程式安全邊界。
- 不將現有目錄中的 Skill 自動升級或合併；覆寫僅透過使用者明確指定 `--force`。不建立 Skill 市集或跨專案安裝機制。

## Further Notes

- 這個功能的價值在於公開「可修改方法的邊界」：範本是可執行的起點，作者指南是對工具與交付契約的說明。只有複製現有短版 SKILL.md，無法解決使用者不知道如何串接工具的原始問題。
- create situation 目前在工作樹中開發；實作此 PRD 時應以其最終公開 CLI 與 Skill 契約為準，避免把開發中的介面當成已發布承諾。
- 建立範本屬本機檔案操作。接受測試與文件範例應區分「產生成功且可載入」與「實際 provider／受測 Agent 已執行成功」。
