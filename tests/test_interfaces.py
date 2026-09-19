"""Real stdin/subprocess and saved-state tests; no model/provider mocks."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from lladar.adapter_workspace import WorkspaceExplorer
from lladar.interfaces import choose_interface, validate_plan, write_json
from lladar.run_context import load_context, save_context, resume_workspace
from lladar.runner import copy_project


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


def paused_run(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("original")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("{}\n")
    with copy_project(project, runs_root=tmp_path / "runs") as workspace:
        save_context(workspace, dataset=dataset, project=project, output=tmp_path / "answers.jsonl",
                     python=sys.executable, env_file=None, model="unused", timeout=30,
                     max_tool_calls=10, intent="chat")
        evidence = workspace.parent / "adapter"
        evidence.mkdir()
        write_json(evidence / "run.json", {"status": "needs_confirmation"})
    return workspace.parent, project, dataset, workspace


def test_resume_context_survives_new_process(tmp_path):
    run, _, _, _ = paused_run(tmp_path)
    code = ("import json,sys; from lladar.run_context import load_context; "
            "print(json.dumps(load_context(sys.argv[1])['intent']))")
    result = subprocess.run([sys.executable, "-c", code, str(run)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == "chat"


def test_resume_lock_prevents_two_continuations(tmp_path):
    run, _, _, workspace = paused_run(tmp_path)
    with resume_workspace(run) as current:
        assert current == workspace
        with pytest.raises(ValueError, match="already being resumed"):
            with resume_workspace(run):
                pass
    assert not (run / ".resume.lock").exists()


@pytest.mark.parametrize("change", ["project", "dataset", "workspace", "status"])
def test_resume_rejects_stale_or_completed_state(tmp_path, change):
    run, project, dataset, workspace = paused_run(tmp_path)
    if change == "dataset":
        dataset.write_text("changed")
    elif change == "status":
        write_json(run / "adapter/run.json", {"status": "verified"})
    else:
        ((project if change == "project" else workspace) / "app.py").write_text("changed")
    with pytest.raises(ValueError):
        load_context(run)
