# Answer adapter contract

The adapter must produce UTF-8 JSONL with one object per dataset item:

```json
{"id":"dataset-item-id","answer":"the agent's actual answer"}
```

Rules:

- Copy `id` exactly from the source dataset; do not use line numbers as IDs.
- Write one answer record per attempted item.
- Preserve the agent's answer text without adding a model-generated summary.
- Do not write credentials, cookies, session state, hidden prompts, or raw provider logs.
- If an item fails, write `status: "error"` and an `error` field instead of inventing an answer.

The evaluator also accepts the richer records produced by the repository's
`qa_agent.py`; it reads the same top-level `id` and `answer` fields.
