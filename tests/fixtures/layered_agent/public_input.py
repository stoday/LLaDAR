"""The user-facing boundary: initialization plus final reply processing."""
from pathlib import Path
import json

from workflow import respond, record


def _submit(question: str, channel: str) -> str:
    settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
    record("initialized", channel)
    knowledge = Path(settings["knowledge"]).read_text(encoding="utf-8")
    record("knowledge_loaded", settings["knowledge"])
    answer = respond(question, knowledge)
    final = settings["prefixes"][channel] + answer
    record("postprocessed", channel)
    if channel == "ticket":
        Path("ticket.txt").write_text(final, encoding="utf-8")
        record("ticket_saved", "ticket.txt")
    return final


def chat(question: str) -> str:
    """Public customer chat entrypoint."""
    return _submit(question, "chat")


def ticket(question: str) -> str:
    """Public ticket workflow entrypoint."""
    return _submit(question, "ticket")
