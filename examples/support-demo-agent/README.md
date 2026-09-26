# Support demo Agent

This is a deliberately small, dependency-free target Agent for the LLaDAR
documentation. Its only public user workflow is:

```text
POST /api/chat
Content-Type: application/json

{"input":{"text":"What is the standard reply target?"}}
```

The response is:

```json
{"data":{"answer":"The standard reply target is one business day."}}
```

Start it as an existing test service with any Python 3.11 or 3.12 interpreter:

```text
python server.py --host 127.0.0.1 --port 8000
```

`GET /health` returns `{"ready": true}`. `engine.answer_question()` is an
internal implementation detail, not a substitute for testing the public HTTP
workflow.
