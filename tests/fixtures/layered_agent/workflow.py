from pathlib import Path
import json


def record(event: str, detail: str = "") -> None:
    with Path("trace.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event": event, "detail": detail}, ensure_ascii=False) + "\n")


def respond(question: str, knowledge: str) -> str:
    """Internal workflow; public_input owns initialization and final processing."""
    from engine import answer

    record("workflow_started")
    return answer(question, knowledge)
