# Streamlit Q&A test page

A native `st.chat_input` / `st.chat_message` page with session history. Each answer is `Answer: <question>`. No external model or extra HTTP endpoint is used.

Run from the LLaDAR repository root:

```powershell
uv run --extra test --extra streamlit-test streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open http://127.0.0.1:8501. Stop the page with Ctrl+C.

Run the real-browser compatibility test with an already provisioned Playwright Chromium:

```powershell
uv run --extra test --extra streamlit-test pytest tests/test_streamlit_browser.py -m browser
```

The test owns a loopback server and an isolated browser profile and stops its server afterward.

Verified with Streamlit 1.65.0: transport-only capture cannot replay the native binary persistent WebSocket. Browser mode now selects DOM fallback during calibration, observes the current chat turn's running-to-idle completion, then uses native chat components for verification and dataset questions. Tests cover both the transport limitation and successful BrowserTarget/CLI execution with safe route evidence. The selected route stays fixed, and failures stop later submissions.

To run your dataset against this page (Gemini extraction credentials and interactive YES/MATCH review are required):

```powershell
uv run lladar run-agent path/to/dataset.jsonl --page-url http://127.0.0.1:8501
```

The integration tests use a synthetic extraction provider; they validate browser orchestration and artifacts, not a real model's fidelity.

`agent.py` exposes `ask_agent(question)` for future project-mode adaptation. The default tests do not exercise auto-adapters or real-model extraction.


The opt-in real Gemini CLI test passed with `gemini-3.8-flash`: four extraction calls and two exact Chinese answers. See the [live validation record](../../docs/streamlit-browser-validation.zh-TW.md). Explicitly set `LLADAR_LIVE_GEMINI=1` and select `-m live_gemini`; ordinary tests skip this paid API case.
