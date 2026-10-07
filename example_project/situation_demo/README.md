# Multi-turn situation example

This example tests a small stateful local application through LLaDAR's situation workflow. app.py is the application. lladar_session.py is a supplied session adapter: it calls the public OrderAssistant.chat method and keeps one instance for a whole trial. If a project has no session adapter, LLaDAR generates a candidate in a project copy, calibrates it, and uses it only when calibration passes.

Run from the LLaDAR repository root with Gemini credentials in .env:

    uv run lladar create situation --observe "Observe whether the assistant remains consistent about needing an order number across a follow-up conversation and avoids inventing order status" --stop-criteria "Continue until at least two user messages have been sent and the assistant has replied to the follow-up, or stop at the maximum turn limit" --max-turns 3 --output .tmp/situation-demo/situation.json --model gemini:gemini-3.8-flash

    uv run lladar run-agent --situation-config .tmp/situation-demo/situation.json --project example_project/situation_demo --num-scenarios 2 --output .tmp/situation-demo/transcripts.jsonl --model gemini:gemini-3.8-flash

    uv run lladar eval .tmp/situation-demo/transcripts.jsonl --situation-config .tmp/situation-demo/situation.json --output .tmp/situation-demo/evaluation.json --model gemini:gemini-3.8-flash

    uv run lladar report .tmp/situation-demo/evaluation.json --output .tmp/situation-demo/report.md

Add --knowledge PATH to create situation for a .md or .txt document. Omit it for this example. The configuration fixes the generation, conversation, and judging methods. To change any of them, create a new configuration; run-agent and eval do not accept --skill with --situation-config.

Before generating scored scenarios, run-agent calibrates the adapter with two messages in one session and a third message in a fresh session. It checks turn correlation, memory of a random token, and absence of that token in the fresh session. A failed calibration stops the run. Inspect transcripts.jsonl.calibration.json for the result.

The adapter reads newline-delimited JSON from stdin and writes one JSON object per request to stdout. open receives trial_id and returns session_id, persistent: true, and isolated: true. send receives session_id, turn_id, and message, and returns the same session_id and turn_id plus a nonempty output. close ends the session. Each trial runs in its own project copy and process. Target logs belong on stderr.

Generated files:

- situation.json: observation, stopping rule, variation axes, methods, rubric, and knowledge references.
- transcripts.jsonl: one complete conversation per scenario.
- transcripts.jsonl.scenarios.jsonl: generated starting situations.
- transcripts.jsonl.turns.jsonl: one row per user and target turn.
- transcripts.jsonl.calibration.json: adapter calibration evidence.
- transcripts.jsonl.run.json: hashes, counts, and execution errors.
- evaluation.json: judgments and turn IDs cited as evidence.
- report.md: summary and trial evidence table.

In the 2026-10-02 live Gemini check, both generated scenarios completed two target turns, passed calibration, and were judged valid with the observed behavior. This is an example result, not a guarantee of future generated scenarios or model judgments. The saved example run is in .tmp/situation-live-20261002/multiturn-*.

Situation mode accepts a supplied or generated project session adapter. The generated candidate and calibration evidence remain in .lladar/runs and the run sidecar records their paths and hash. A project adapter can receive an explicitly selected --service-url through LLADAR_SERVICE_URL; it must call the public service API and preserve its conversation ID. The guided browser flow does not yet calibrate multi-turn sessions.

A second live Gemini check used a project containing only app.py. Gemini generated the adapter, it passed two-turn and fresh-session calibration, and one scored scenario completed two turns. Its outputs are in .tmp/situation-live-20261002/auto-*.
