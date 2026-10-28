# Streamlit Q&A test page

A native `st.chat_input` / `st.chat_message` page with session history. Each answer is `Answer: <question>`. No external model or extra HTTP endpoint is used.

Run from the LLaDAR repository root:

```powershell
uv sync --locked --extra test
uv sync --project example_project/streamlit_qa --locked --extra test
uv run --project example_project/streamlit_qa --locked streamlit run example_project/streamlit_qa/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open http://127.0.0.1:8501. Stop the page with Ctrl+C.

The page uses its own `.venv`; the LLaDAR controller and browser harness use the root environment. See the [shared environment guide](../README.md) for version requirements and certifi updates. The root `streamlit-test` extra remains available for controller integration tests.

Install Playwright Chromium before using LLaDAR's browser mode:

```powershell
uv run python -m playwright install chromium
```

The current `--page-url` flow uses an Akasha Agent to explore actual page operations and communication, generate a protocol/session programme and qualify it with independent probes and new verification turns. Native Streamlit binary WebSocket traffic is observed through generic tools; the core does not select a fixed Streamlit DOM route or ship a predefined Streamlit codec. A generated decoder still needs host source checks and independent complete-answer verification.

Keep the Streamlit terminal running, then use a second terminal for your dataset:

```powershell
uv run lladar run-agent path/to/dataset.jsonl --page-url http://127.0.0.1:8501 --output streamlit.responses.jsonl
```

Configure the selected Akasha model locally; `--model PROVIDER:MODEL` chooses exploration, repair and tool-free extraction. The page itself uses no model, but LLaDAR's discovery and extraction can make paid model calls. Follow the terminal's website/model-transfer approval and complete-answer checks.

Automatic formal-answer verification requires at least three distinct trusted full-answer qualification turns and a valid current-turn source profile, completion guard and complete DOM capture. The current independent reader requires an observed `aria-busy` true-to-false transition and verbatim whole-subtree text. If this page does not supply that evidence, or original formatting cannot be reproduced exactly, manual `MATCH` remains necessary for the affected answers; model agreement alone is insufficient.

The [native Streamlit harness](../../scripts/verify_streamlit_discovery_live.py) has a dry mode with no model calls:

```powershell
uv run python scripts/verify_streamlit_discovery_live.py --timeout 30 --output-root .tmp/streamlit-discovery-dry
```

It expects an already running native page at `http://127.0.0.1:8517` by default. Use `--page-url http://127.0.0.1:8501` for the example started above. Dry checks establish native UI answers, conversation history and persistent binary WebSocket observations; they do not prove Agent-generated binary decoding. Adding `--paid` explicitly enables real Akasha discovery/extraction and network-decoding acceptance checks. Consult the [discovery PRD](../../docs/PRD-browser-agent-discovery.zh-TW.md) and [current validation evidence](../../docs/browser-agent-discovery-validation.zh-TW.md) for results; an available paid harness is not a passing acceptance result.

The [2026-10-09 validation record](../../docs/streamlit-browser-validation.zh-TW.md) describes the retired calibration/fixed-route implementation and does not establish current Streamlit acceptance. Ordinary regression tests do not authorize paid model calls.

`agent.py` also exposes `ask_agent(question)` for project-mode adaptation; this README demonstrates the normal Streamlit webpage.
