from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from engine import answer
from workflow import record

request = json.load(sys.stdin)
with redirect_stdout(sys.stderr):
    record('initialized')
    knowledge = Path('knowledge.md').read_text(encoding='utf-8')
    record('knowledge_loaded')
    try:
        result = answer(request['question'], knowledge)
    except Exception as error:
        # Provider callbacks preserve safe classification and elapsed time.
        # Never print the SDK exception's arbitrary request/response text.
        print(json.dumps({'event': 'target_failed', 'error_type': type(error).__name__}),
              file=sys.stderr, flush=True)
        raise SystemExit(1) from None
print(json.dumps({'answer': result}, ensure_ascii=False))
