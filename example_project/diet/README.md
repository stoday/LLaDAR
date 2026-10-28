# 飲食問答範例

從 LLaDAR repository 根目錄建立獨立環境並執行：

```powershell
uv sync --project example_project/diet --locked --extra test
uv run --project example_project/diet --locked python example_project/diet/main.py
```

程式讀取 `example_project/.env` 的模型憑證及同資料夾內的 `DIET-v1.md`，回答問題時會呼叫模型。

`run-lladar-eval.bat` 使用 repository 根目錄的 LLaDAR 控制程式，以及這個範例的獨立 `.venv`；執行前也要在根目錄完成 `uv sync --locked --extra test`。該流程會呼叫模型並產生資料集與評估產物。

環境版本、certifi 升級及其他範例見[統一建置說明](../README.md)。
