# 範例環境建置

五個範例各自使用 `pyproject.toml`、`uv.lock` 與資料夾內的 `.venv`。LLaDAR 控制程式使用 repository 根目錄的環境；受測範例使用自己的環境。

請使用 uv 0.13.4 以上，以及最新安全修補的 Python 3.11 或 3.12。從 repository 根目錄執行：

```powershell
# 安裝 LLaDAR 控制程式
uv sync --locked --extra test

# 選擇一個範例；可改成下表其他目錄
uv sync --project example_project/diet --locked --extra test
```

也可以進入任一範例資料夾，使用相同命令：

```powershell
uv sync --locked --extra test
```

若要從 repository 根目錄一次建置全部範例：

```powershell
foreach ($name in 'diet', 'resume_review', 'situation_demo', 'streamlit_qa', 'tainan_tutorial') {
    uv sync --project "example_project/$name" --locked --extra test
    if ($LASTEXITCODE -ne 0) { throw "Environment setup failed: $name" }
}
```

不需要手動啟用虛擬環境。啟動時用 `uv run --project <範例路徑> --locked ...`；已啟動的 Python 程序請重新啟動，才會載入更新的套件。`--locked` 會在設定與鎖檔不一致時停止，避免啟動時意外重新解析版本。

| 範例 | 從 repository 根目錄啟動 |
| --- | --- |
| [diet](diet/README.md) | `uv run --project example_project/diet --locked python example_project/diet/main.py` |
| [resume_review](resume_review/README.md) | `uv run --project example_project/resume_review --locked python example_project/resume_review/console.py --color` |
| [situation_demo](situation_demo/README.md) | 由根目錄的 `uv run --locked lladar run-agent ... --project example_project/situation_demo` 啟動受測程式 |
| [streamlit_qa](streamlit_qa/README.zh-TW.md) | `uv run --project example_project/streamlit_qa --locked streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false` |
| [tainan_tutorial](tainan_tutorial/README.md) | `uv run --project example_project/tainan_tutorial --locked tainan-demo serve` |

各範例原有的 `.env` 讀取規則不變；建置不會建立或改寫金鑰檔。飲食與履歷範例會呼叫模型，需要先設定其說明中的憑證；Streamlit 頁面與台南 replay 可以離線執行。

飲食與履歷範例預設共用 `example_project/.env`，履歷資料夾內的 `.env` 優先。歷史 `.lladar` 執行快照保留在 `diet/.lladar`；快照中的舊路徑僅供回溯，新的執行請使用 `diet/` 路徑。

## certifi 安全版本

主專案與每個範例都要求 `certifi>=2026.10.19`，對應 [Python 官方 eMudhra／emSign 憑證事件公告](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576/1)。鎖檔固定實際安裝版本與套件雜湊。

日常建置使用上面的 `uv sync --locked --extra test`。維護者要升級 certifi 時，在根目錄及各範例資料夾分別執行：

```powershell
uv lock --upgrade-package certifi
uv sync --locked --extra test
```

同步後可確認實際版本：

```powershell
uv run --locked python -c "import certifi; print(certifi.__version__)"
```

若另用 pip 安裝套件，pip 必須獨立升級至 26.2.2 以上；升級專案的 certifi 不會更新 pip 內附的憑證。安全升級方式見官方公告。pip 與 uv 是建置工具，不列入應用程式的執行依賴。
