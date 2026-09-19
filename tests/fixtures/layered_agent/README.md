# Layered shop agent: two public workflows

This is a real paid-API agent for testing public-interface discovery. Install the
LangChain and Google GenAI integrations in its own environment or supply an existing
compatible target interpreter. Set GEMINI_API_KEY for the normal model client.

Two equally supported public user features exist:

- `public_input.chat(question)`: customer chat. Loads shop settings and knowledge,
  constructs the tool-enabled agent, and returns a final reply beginning `客服回覆：`.
- `public_input.ticket(question)`: ticket handling. Uses the same configured services
  and tools, but records the final reply to a local ticket file and returns its text
  beginning `工單回覆：`.

There is no default feature. A caller must select which user workflow is being tested.
`workflow.respond` and `engine.answer` are internal seams: calling them directly
skips public initialization or final response handling and is not a full-system test.
The app records its own lifecycle in `trace.jsonl`, inside its working directory.

Do not substitute policy lookups for the real agent or change its provider/model.
