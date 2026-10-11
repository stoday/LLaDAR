# CLI 主要階段的輸出路徑契約

所有主要階段的 `--output` 都可省略，也都接受目錄或明確檔案路徑。相對路徑以呼叫命令的工作目錄為準。

| 命令 | 省略時的主檔 | 指定目錄時的主檔 |
| --- | --- | --- |
| `create test-dataset` | `./test-dataset-YYYYMMDD-HHMMSS.jsonl` | 目錄內同樣使用時間戳檔名，撞名加流水號 |
| `create situation` | `./situation.json` | `situation.json` |
| `run-agent`（單題與情境） | `./responses.jsonl` | `responses.jsonl` |
| `eval`（單題與情境） | `./evaluation.json` | `evaluation.json` |
| `report` | `./report.md` | `report.md` |

- 已存在的目錄、以 `/` 或 `\` 結尾的路徑，以及尚不存在且最後一段沒有副檔名的路徑，都視為目錄。
- 已存在的檔案，以及尚不存在且最後一段有副檔名的路徑，都視為明確檔案路徑。檔案內容格式由階段決定，不自動補副檔名。
- 尚不存在而名稱含點號的目錄可加結尾分隔符號，例如 `--output ./results.v1/`。
- 父目錄在正式寫入時自動建立。路徑解析本身不建立目錄。
- 附屬檔以解析後的主檔路徑為基準，放在主檔旁。
- 固定檔名或明確檔名若已存在，仍需 `--force`；不得因指定目錄而略過覆寫保護。
- 五種 `create <stage>-skill` 保留原本的目錄契約與預設位置。

此契約適用 CLI；底層 Python API 的 `output` 仍是主檔路徑。

每個階段成功完成後，都顯示工作摘要：完成的工作、解析後的絕對輸出目錄、
主檔與實際存在的附屬檔絕對路徑、每個檔案的內容簡述，以及下一步命令或閱讀提示。
Skill 範本命令也說明 `SKILL.md` 和 `AUTHORING.md` 的用途。
摘要不受 `--no-verbose` 影響，並透過 `--log` 同步保存；失敗時不顯示成功摘要。

`--help` 引數列表後的補充說明須有用途小標。`create` 使用 `Available commands:`，
保留主要流程與進階 Skill 範本分組；`run-agent` 使用
`Additional guidance:`，依瀏覽器準備、登入與校準、核對與授權、執行限制、
輸出與工作階段、專案模式分段。保留既有操作資訊，段落間留空行，
文字依 help 顯示寬度換行。
模型預設值及既有容量常數直接取自實作；瀏覽器補充說明須區分排程試跑與
資料筆數、授權後開始的共用期限與單次等待、輸出檔排除憑證與本機 profile
保存登入狀態，以及預設保留 profile 與 `--fresh-browser-profile` 結束清除的行為。
