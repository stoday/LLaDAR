"""Real stdin/subprocess and saved-state tests; no model/provider mocks."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from lladar.adapter_workspace import WorkspaceExplorer
from lladar.interfaces import NeedsConfirmation, choose_interface, validate_plan, write_json


def plan():
    return {"candidates": [
        {"id": name, "label": name, "entrypoint": f"app.py:{name}",
         "public_boundary": True, "rationale": "Public function", "flow": ["init", "agent", "final"],
         "output": "Final text", "evidence": [{"path": "app.py", "line": i + 1,
                                                  "quote": f"def {name}(): pass"}]}
        for i, name in enumerate(("chat", "ticket"))], "unresolved": [], "summary": "Two features"}


def test_evidence_and_noninteractive_selection(tmp_path):
    (tmp_path / "app.py").write_text("def chat(): pass\ndef ticket(): pass\n")
    proposal = validate_plan(plan(), WorkspaceExplorer(tmp_path))
    assert choose_interface(proposal, interactive=False) == ("pause", "")
    assert choose_interface(proposal, interactive=False, candidate_id="ticket") == ("select", "ticket")
    proposal["candidates"] = proposal["candidates"][:1]
    assert choose_interface(proposal, interactive=False) == ("automatic", "chat")
    proposal["unresolved"] = ["Which tenant?"]
    assert choose_interface(proposal, interactive=False) == ("pause", "")


def test_unsubstantiated_or_internal_candidates_cannot_be_selected(tmp_path):
    (tmp_path / "app.py").write_text("def chat(): pass\ndef ticket(): pass\n")
    proposal = plan()
    proposal["candidates"][0]["evidence"][0]["quote"] = "invented"
    with pytest.raises(ValueError, match="Evidence does not match"):
        validate_plan(proposal, WorkspaceExplorer(tmp_path))
    proposal = plan()
    proposal["candidates"][0]["public_boundary"] = False
    with pytest.raises(ValueError, match="not an evidenced"):
        choose_interface(proposal, interactive=False, candidate_id="chat")


@pytest.mark.parametrize("stdin, expected", [
    ("1\n", ["select", "chat"]), ("ticket\n", ["select", "ticket"]),
    ("bad\n2\n", ["select", "ticket"]), ("c\nUse the customer chat\n", ["clarify", "Use the customer chat"]),
    ("q\n", ["pause", ""]), ("", ["pause", ""]),
])
def test_terminal_input_in_real_process(tmp_path, stdin, expected):
    path = tmp_path / "plan.json"
    write_json(path, plan())
    code = ("import json,sys; from lladar.interfaces import choose_interface; "
            "p=json.load(open(sys.argv[1])); print(json.dumps(choose_interface(p,interactive=True)))")
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(path)],
                            input=stdin, capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == expected
    assert "流程" in result.stderr and "入口" in result.stderr


def test_confirmation_message_points_to_the_supported_workflow(tmp_path):
    message = str(NeedsConfirmation(tmp_path))
    assert "--interactive" in message
    assert "resume-agent" not in message
