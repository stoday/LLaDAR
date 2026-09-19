# Shop REST agent

The public input is HTTP `POST /api/chat`, implemented in server.js. Run:

```
node server.js --host 127.0.0.1 --port PORT --python PATH_TO_PYTHON
```

`GET /health` returns 200 when ready. No credentials are required for this local
fixture's HTTP endpoint. The Python worker needs GEMINI_API_KEY and the installed
langchain/langchain-google-genai packages. This is a real model/tool workflow.

Submit `{"request_id":"unique-id","input":{"text":"question"}}` with
Content-Type application/json. The final JSON response is
`{"request_id":"same-id","data":{"answer":"API客服：..."}}`.

The server performs input validation, records the public request, launches worker.py
with the configured Python, then formats the final answer. worker.py loads knowledge
and uses the configured agent's policy tool. Calling worker.py or engine.answer
directly bypasses the public request processing and final answer formatting.

This project has no browser frontend. client.ts is an example API client only;
it does not add reference data or business logic. Use the existing API unchanged.
trace.jsonl is an append-only lifecycle log for acceptance testing; preserve it.
