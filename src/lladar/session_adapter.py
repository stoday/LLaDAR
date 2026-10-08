"""Session protocol verification behind the shared coding-agent builder."""
from __future__ import annotations

import hashlib
import json
import uuid

from .auto_adapter import (AutoAdapter, ADAPTER_PROTOCOL_PROMPT, CODING_PROMPT,
                           REPAIR_PROMPT, _runtime_diagnostic)

SESSION_RUNTIME_PROTOCOL = {
    "open": {"stdin": {"op": "open", "trial_id": "<trial id>"},
             "stdout": {"session_id": "<unique id>", "persistent": "<boolean>", "isolated": True}},
    "send": {"stdin": {"op": "send", "session_id": "<id from open>",
                        "turn_id": "<turn id>", "message": "<exact user message>"},
             "stdout": {"session_id": "<same id>", "turn_id": "<same turn id>", "output": "<real nonempty answer>"}},
    "close": {"stdin": {"op": "close", "session_id": "<same id>"},
              "stdout": {"closed": "<same id>"}},
    "streams": "One JSON object per line, flushed immediately. Target logs go to stderr. One process per trial.",
}
SESSION_PROTOCOL_PROMPT = """
INVARIANT SESSION ADAPTER RUNTIME PROTOCOL:
Write the standalone adapter through write_harness. LLaDAR installs its source as
lladar_session.py in each isolated project copy. Read stdin JSONL until close.
open receives op=open, trial_id; return a unique session_id, persistent (boolean),
and isolated=true. send receives op=send, session_id, turn_id, message; submit the
exact message through the selected application's public interface and return the
same session_id, turn_id and output (real nonempty final answer string).
close receives op=close, session_id; stop managed child services and return closed
with the same session_id. Never stop an authorized existing service.
Flush every stdout response line; send ALL target logs to stderr.
run_harness(message=...) is a plain-text probe. For single-turn mode the host sends
that exact probe; for multi-turn calibration it uses its own random-token probes
to verify real memory and fresh-session isolation. Never read stdin as one whole JSON document.
Use the project's actual session interface; never manufacture memory by appending
conversation history to a single question. Report blockers when multi-turn is
required but the target cannot preserve context. A single-turn target needs no memory.
Use sys.executable for target Python; keep application source and credentials intact.
The lladar_service_runtime.py helper is available in the project copy's parent
directory for managed services; add that directory to sys.path when importing it.
Session requests are not request_id/message single-shot calls.
"""


class SessionAutoAdapter(AutoAdapter):
    """Reuse AutoAdapter.prepare; only its wire protocol and verifier differ."""

    runtime_protocol = SESSION_RUNTIME_PROTOCOL
    protocol_mode = "session"
    run_harness_guidance = "run_harness(message=...) takes a plain-text probe for single-turn mode; multi-turn calibration uses host token probes. The host drives open/send/close JSONL."

    def __init__(self, *args, max_turns: int, **kwargs):
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        super().__init__(*args, **kwargs)
        self.max_turns = max_turns
        mode = f"\nRequired mode: max_turns={max_turns}; memory_required={max_turns > 1}.\n"
        self.coding_prompt = CODING_PROMPT.replace(ADAPTER_PROTOCOL_PROMPT, SESSION_PROTOCOL_PROMPT).replace(
            "It lives beside adapter.py; do not overwrite or copy it.",
            "It lives in the project copy's parent directory; add that directory to sys.path. Do not overwrite or copy it.") + mode
        self.repair_prompt = REPAIR_PROMPT.replace(ADAPTER_PROTOCOL_PROMPT, SESSION_PROTOCOL_PROMPT) + mode
        self.discovery_context = mode + "Find the real public conversation/session interface if memory is required."
        self.report.update(protocol="session", max_turns=max_turns)
        self.calibration: dict = {"status": "failed", "error": "No session adapter verified"}

    def execute(self, source: bytes, question: str, *, phase: str, case_id: str | None = None) -> dict:
        from .runner import copy_project
        from .situation import calibrate_session

        result = {"request_id": uuid.uuid4().hex, "phase": phase, "case_id": case_id,
                  "adapter_sha256": hashlib.sha256(source).hexdigest(), "ok": False}
        with copy_project(self.workspace, runs_root=self.evidence / "requests") as workspace:
            (workspace / "lladar_session.py").write_bytes(source)
            calibration = calibrate_session(workspace, self.python, self.env_file, self.timeout,
                                            runs_root=self.evidence / "calibration", service_url=self.service_url,
                                            max_turns=self.max_turns, probe=question)
        self.calibration = calibration
        result["calibration"] = calibration
        if calibration["status"] == "passed":
            result.update(ok=True, output=calibration["first_response"], observation="Real session adapter calibration")
        else:
            result["error"] = _runtime_diagnostic(calibration.get("error", "Calibration failed"), self.explorer.environment)
        with (self.evidence / "observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        return result
