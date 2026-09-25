"""Contract tests for the dependency-free documentation target Agent."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
from urllib.request import Request, urlopen


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "support-demo-agent"
sys.path.insert(0, str(EXAMPLE_DIR))

import engine  # noqa: E402
import server  # noqa: E402


def test_support_policy_answers_are_deterministic() -> None:
    assert engine.answer_question("What is the reply target?") == "The standard reply target is one business day."
    assert engine.answer_question("How are urgent incidents handled?") == "Urgent incidents are handled by the on-call process."
    assert engine.answer_question("電子郵件支援在哪些日子提供？") == "Email support is available on business days."


def test_public_http_workflow_returns_final_answer() -> None:
    httpd = server.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        assert json.load(urlopen(base_url + "/health")) == {"ready": True}
        request = Request(
            base_url + "/api/chat",
            data=json.dumps({"input": {"text": "What is the standard reply target?"}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assert json.load(urlopen(request)) == {
            "data": {"answer": "The standard reply target is one business day."}
        }
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
