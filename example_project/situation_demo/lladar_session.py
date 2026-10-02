"""LLaDAR JSONL session bridge to the application's public chat method."""
from __future__ import annotations

import json
import sys
import uuid

from app import OrderAssistant


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


assistant: OrderAssistant | None = None
session_id: str | None = None
for line in sys.stdin:
    try:
        request = json.loads(line)
        op = request.get("op")
        if op == "open":
            if assistant is not None:
                raise ValueError("session already open")
            assistant = OrderAssistant()
            session_id = uuid.uuid4().hex
            emit({"session_id": session_id, "persistent": True, "isolated": True})
        elif op == "send":
            if assistant is None or request.get("session_id") != session_id:
                raise ValueError("unknown session")
            turn_id = request["turn_id"]
            output = assistant.chat(request["message"])
            emit({"session_id": session_id, "turn_id": turn_id, "output": output})
        elif op == "close":
            emit({"closed": session_id})
            break
        else:
            raise ValueError("unknown operation")
    except Exception as error:
        emit({"error": f"{type(error).__name__}: {error}"})
