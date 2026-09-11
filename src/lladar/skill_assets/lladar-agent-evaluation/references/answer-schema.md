# Schema-v2 observed-answer contract

The answer artifact is UTF-8 JSONL with one record per attempted original or
variant session.

Successful call:

```json
{"schema_version":2,"id":"group-1-omission","group_id":"group-1","kind":"information_omission","question":"Which plan applies?","status":"ok","answer":"The Agent's exact response"}
```

Failed call:

```json
{"schema_version":2,"id":"group-1-omission","group_id":"group-1","kind":"information_omission","question":"Which plan applies?","status":"execution_error","error":"TimeoutError: timed out"}
```

Rules:

- The original uses its group ID and `kind: "original"`.
- A variant uses its variant ID and kind.
- Copy `id`, `group_id`, `kind`, and `question` exactly from the dataset.
- Emit one record per attempted ready case; skipped groups emit none.
- Preserve answer text without judging, summarizing, or repairing it.
- An empty successful response remains `status: "ok"` with `answer: ""`; the
  evaluator classifies it as `completed_no_answer`.
- Keep credentials, cookies, session state, hidden prompts, and raw provider
  logs out of both `answer` and `error`.
