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
    result = answer(request['question'], knowledge)
print(json.dumps({'answer': result}, ensure_ascii=False))
