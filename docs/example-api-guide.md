# Akasha Chat API 使用說明

本文件描述 `chat-api/main.py` 實作的 HTTP API，供 API 呼叫端與 LLaDAR 文件驅動 adapter 開發使用。契約核對日期：2026-10-16。

## 1. 服務與連線位址

本服務接收單一文字問題，透過 Akasha 呼叫模型，再回傳完整文字回答。預設使用繁體中文；實際回答內容由模型產生，不保證逐字固定。

本機啟動後的 base URL 範例：

```text
http://127.0.0.1:8000
```

所有端點均相對於 base URL。呼叫端應使用外部設定的服務位址；文件中的主機與連接埠只是範例。LLaDAR 的規劃用法以 `--service-url` 決定實際 base URL。

| 方法 | 路徑 | 用途 | 是否需要認證 |
| --- | --- | --- | --- |
| GET | `/health` | 檢查 HTTP 服務是否可回應，不呼叫模型 | 否 |
| POST | `/chat` | 提交問題並取得模型完整回答 | 是 |
| GET | `/docs` | Swagger 互動文件 | 否 |
| GET | `/openapi.json` | OpenAPI 規格 | 否 |

## 2. 認證

`POST /chat` 必須傳送以下 HTTP header：

```http
Authorization: Bearer <API_AUTH_TOKEN>
```

`API_AUTH_TOKEN` 是服務與呼叫端共用的 API 存取憑證。呼叫端從同名環境變數讀取，組成 `Bearer ` 加上 token 的 header 值；不得把 `<API_AUTH_TOKEN>` 佔位文字當成真實 token。

服務端另需 `GEMINI_API_KEY` 以呼叫模型。API 呼叫端只需 `API_AUTH_TOKEN`，不需取得或傳送 `GEMINI_API_KEY`。不要將真實 token 寫入本文件、adapter 原始碼或日誌。

服務端啟動時要求：

- `GEMINI_API_KEY` 不可為空白。
- `API_AUTH_TOKEN` 至少 32 個字元，不可含前後空白。
- 兩者不可相同。

## 3. 提交問題：POST /chat

### 請求

```http
POST /chat HTTP/1.1
Host: 127.0.0.1:8000
Authorization: Bearer <API_AUTH_TOKEN>
Content-Type: application/json; charset=utf-8

{"message":"請用一句話說明什麼是單元測試。"}
```

JSON 本文必須是物件，且只能包含 `message`：

| 欄位 | 型別 | 必填 | 規則 |
| --- | --- | --- | --- |
| `message` | string | 是 | 去除前後空白後長度為 1 至 8,000 字元；不接受數字、null 或額外欄位 |

此長度是字元數，不是 token 數。例如 `{"message":"  你好  "}` 會以 `你好` 呼叫模型；空字串或全空白字串則驗證失敗。

不要傳送 `messages`、`history`、`model`、`stream`、`conversation_id` 或 `request_id` 等額外欄位。模型由服務端設定，呼叫端不能透過本文選擇。

### 成功回應

HTTP 狀態為 `200`，`Content-Type` 為 `application/json`：

```json
{
  "response": "單元測試是針對程式中個別功能單位進行驗證，確認其行為符合預期的測試。"
}
```

上例回答僅供格式示意。唯一答案欄位是頂層 `response`，其型別為非空白字串，可能包含換行及 Markdown。呼叫端應保留完整文字。

### 完成與對話語義

- 服務採非串流回應，一個請求只回傳一份最終 JSON；沒有 SSE、WebSocket、輪詢或額外的結束事件。
- 讀取完整 HTTP 回應後，確認狀態為 `200`、本文為 JSON 物件，且 `response` 為非空白字串，才算取得完整回答。
- 每個請求建立自己的模型呼叫物件，使用空的聊天歷史。不同請求互相獨立，不需要建立或重設 session。
- 服務不提供請求識別碼、冪等鍵或去重機制。連線逾時後，服務端是否已完成模型呼叫可能未知；重送可能再次呼叫模型。
- 服務未設定固定的模型呼叫硬性逾時。呼叫端需自行設定有限的等待時間；例如 120 秒可作為測試起點，但不是服務的完成時間保證。

## 4. 健康檢查：GET /health

無須認證、無須本文：

```http
GET /health HTTP/1.1
Host: 127.0.0.1:8000
```

成功時 HTTP `200`：

```json
{"status":"ok"}
```

健康檢查成功只表示 HTTP 服務可回應，不代表模型供應商、額度或 API token 已通過實際問答驗證。此回應不是問答答案。

## 5. 錯誤回應

| HTTP 狀態 | 情境 | JSON 本文 |
| --- | --- | --- |
| 401 | 缺少或錯誤 Bearer token，或使用其他認證方式 | `{"detail":"Invalid or missing Bearer token"}` |
| 422 | 無效 JSON、缺少 message、型別錯誤、去除前後空白後為空或超過 8,000 字元、含額外欄位 | `{"detail":"Invalid message or JSON"}` |
| 502 | 模型建立或呼叫失敗 | `{"detail":"Model request failed"}` |
| 502 | 模型回傳非字串或空白回答 | `{"detail":"Model returned an empty response"}` |

`401` 另含 `WWW-Authenticate: Bearer` header。若請求同時包含多種問題，不應假設認證與 JSON 驗證錯誤的回傳優先順序。

驗證錯誤範例（假設認證有效）：

```json
{"message":"你好","history":[]}
```

回應為 HTTP `422`：

```json
{"detail":"Invalid message or JSON"}
```

錯誤處理原則：`401` 先修正憑證；`422` 先修正請求。`502`、網路中斷或逾時應記錄為該次呼叫失敗，不把錯誤本文當成模型回答。是否重試由呼叫端明確決定；本 API 不保證重試只執行一次。

## 6. Python 呼叫範例

以下範例使用 Python 標準函式庫。執行前，將有效的 `API_AUTH_TOKEN` 放入呼叫端環境；可用 `LLADAR_SERVICE_URL` 覆寫範例位址。

```python
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ask(message: str) -> str:
    base_url = os.environ.get("LLADAR_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ["API_AUTH_TOKEN"]
    body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url + "/chat",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as result:
            if result.status != 200:
                raise RuntimeError("Unexpected API status")
            payload = json.loads(result.read().decode("utf-8"))
    except HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(f"Chat API HTTP {status}") from None
    except URLError:
        raise RuntimeError("Chat API connection failed") from None
    if not isinstance(payload, dict):
        raise ValueError("Chat API response must be a JSON object")
    answer = payload.get("response")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Chat API response must contain a nonempty response string")
    return answer


if __name__ == "__main__":
    print(ask("請用一句話說明什麼是單元測試。"))
```

範例拒絕自動重新導向，以免帶有憑證的請求被轉送至其他位址。若部署位址有變，直接設定正確 base URL。

## 7. PowerShell 呼叫範例

執行前應已設定 `$env:API_AUTH_TOKEN`；以下指令不包含真實憑證。

```powershell
$chatBaseUrl = 'http://127.0.0.1:8000'
if (-not $env:API_AUTH_TOKEN) { throw '請先設定 API_AUTH_TOKEN 環境變數' }

Invoke-RestMethod -Uri "$chatBaseUrl/health" -Method Get

$chatBody = @{ message = '請用一句話說明什麼是單元測試。' } | ConvertTo-Json -Compress
$chatResult = Invoke-RestMethod `
  -Uri "$chatBaseUrl/chat" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $env:API_AUTH_TOKEN" } `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($chatBody)) `
  -TimeoutSec 120 `
  -MaximumRedirection 0

if ($chatResult.response -isnot [string] -or [string]::IsNullOrWhiteSpace($chatResult.response)) {
    throw 'API 未回傳有效的 response 字串'
}
$chatResult.response
```

## 8. 服務端啟動

以下指令在 `chat-api` 目錄執行，需 Python 3.12 與 uv：

```powershell
uv sync --locked
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

服務端載入 `chat-api/.env`，已存在的環境變數優先。新安裝可依 `.env.example` 建立設定；已有 `.env` 時保留既有設定。修改設定後需重啟服務。

## 9. LLaDAR --api-guide 開發測試對照

以下是待實作功能的預期用法；本文件不表示目前版本已支援 `--api-guide`。指令在 `LLaDAR-test` 根目錄執行，服務需另行啟動：

```powershell
.\.venv\Scripts\lladar.exe run-agent `
  .\runs\umezawa-three-once-20261016\dataset.jsonl `
  --service-url http://127.0.0.1:8000 `
  --api-guide .\chat-api\API_GUIDE.md `
  --env-file .\chat-api\.env `
  --output .\runs\api-guide-smoke\responses.jsonl
```

此處 `--env-file` 提供本機測試所需設定；API adapter 發送認證時只使用 `API_AUTH_TOKEN`。

文件驅動 adapter 應推導出下列對照：

| 項目 | 對照結果 |
| --- | --- |
| 連線位址 | 使用 `--service-url` 指定值 |
| 端點 | `POST /chat` |
| 認證 | 環境變數 `API_AUTH_TOKEN` → `Authorization: Bearer ...` |
| 問題 | 每筆測試的問題文字 → JSON `message` |
| 回答 | JSON `response` → 完整文字答案 |
| 請求關聯 | API 不接受也不回傳 request_id；由 adapter 在本地保存並對應原始請求 |
| 獨立性 | 每題獨立，不傳歷史，不建立 session |
| 完成判定 | 完整 HTTP 200 JSON，且 response 為非空白字串 |
| 錯誤判定 | 非 200、無效 JSON、缺少答案或空白答案皆為失敗 |

若沿用既有 LLaDAR adapter 的 `request_id`、`output`、`observation` 協定，可在本地回傳原始 `request_id`，並將完整 `response` 文字填入 `output` 與 `observation`。這些是 adapter 的輸出欄位，不是 `/chat` 的請求欄位。

最小驗收流程：先以 `/health` 確認連線，再以一個新問題驗證 `/chat` 能取得完整回答，最後執行資料集。健康檢查、僅收到 HTTP 200 或示意答案文字相同，均不能單獨證明問答流程已通過驗證。

## 10. 契約依據

- `main.py`：路由、認證、輸入驗證、模型呼叫、答案與錯誤回應。
- `tests/test_api.py`：認證拒絕、輸入限制、獨立請求與供應商錯誤處理。
- `.env.example`：設定名稱；本文件未讀取或收錄真實憑證。

目前文件描述非串流、單題問答契約。範例回答為示意文字；使用替身模型驗證 API 契約與使用真實模型完成問答是不同的驗證階段。
