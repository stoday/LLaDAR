"""Release gating must reject skipped live tests and stale main results."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.verify_release_ci import require_successful_jobs, require_successful_run


SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def run(number, *, sha=SHA, conclusion="success", status="completed", branch="main", event="push"):
    return {"id": number, "run_number": number, "head_sha": sha,
            "head_branch": branch, "event": event, "status": status,
            "conclusion": conclusion}


def test_latest_main_run_for_exact_tagged_commit_must_pass():
    assert require_successful_run([run(1), run(2)], SHA)["id"] == 2
    with pytest.raises(RuntimeError, match="has not passed"):
        require_successful_run([run(1), run(2, conclusion="failure")], SHA)
    with pytest.raises(RuntimeError, match="has not passed"):
        require_successful_run([run(1), run(2, status="in_progress", conclusion=None)], SHA)
    with pytest.raises(RuntimeError, match="No main push CI run"):
        require_successful_run([run(1, sha="b" * 40), run(2, branch="feature")], SHA)


def test_release_requires_two_python_versions_and_actual_live_success():
    jobs = [{"name": "Test (Python 3.11)", "conclusion": "success"},
            {"name": "Test (Python 3.12)", "conclusion": "success"},
            {"name": "Live vibe-testing (Gemini)", "conclusion": "success"}]
    require_successful_jobs(jobs)
    jobs[-1]["conclusion"] = "skipped"
    with pytest.raises(RuntimeError, match="Live vibe-testing"):
        require_successful_jobs(jobs)


def test_workflow_python_script_targets_exist():
    missing = []
    workflows = (ROOT / ".github" / "workflows").iterdir()
    for workflow in (path for path in workflows if path.suffix in {".yml", ".yaml"}):
        text = workflow.read_text(encoding="utf-8")
        for target in re.findall(r"\bpython\s+(scripts/[A-Za-z0-9_./-]+\.py)\b", text):
            if not (ROOT / target).is_file():
                missing.append(f"{workflow.name}: {target}")

    assert not missing, "Workflow references missing Python scripts:\n" + "\n".join(missing)


def test_live_rest_verification_entrypoint_loads():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "scripts/verify_rest_graph_live.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_live_rest_script_uses_current_runner_contract(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "_verify_rest_graph_live_test", ROOT / "scripts" / "verify_rest_graph_live.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    script = tmp_path / "scripts" / "verify_rest_graph_live.py"
    script.parent.mkdir()
    target = tmp_path / "tests" / "fixtures" / "rest_agent"
    target.mkdir(parents=True)
    monkeypatch.setattr(module, "__file__", str(script))
    monkeypatch.setattr(module, "dataset", lambda _kind: {
        "question": "fixture question",
        "expected_answer": "fixture answer",
        "actual_response": None,
    })

    runtime_calls = []
    monkeypatch.setattr(module, "runtime_evidence", lambda python: runtime_calls.append(python))
    inventory_calls = []
    monkeypatch.setattr(module, "inventory", lambda path: inventory_calls.append(path) or {"files": ["server.js"]})

    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def fake_run_agent(data, answers, **options):
        stale = {"intent", "resume_run", "candidate_id"} & options.keys()
        assert not stale, f"stale run_agent options: {sorted(stale)}"
        assert options["project"] == target
        assert options["interactive"] is False
        assert json.loads(data.read_text(encoding="utf-8"))["question"] == "fixture question"

        adapter = options["runs_root"] / "run-1" / "adapter"
        adapter.mkdir(parents=True)
        write_json(adapter / "run.json", {
            "graph": {"status": "ready", "parser_inputs": ["server.js", "client.ts", "engine.py"],
                      "version": "fixture"},
            "interface_selection": {"candidate": {"transport": "http"}},
            "status": "verified",
        })
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        events = ["server_started", "http_received", "initialized", "knowledge_loaded", "tool_called",
                  "model_answered", "http_postprocessed"]
        (workspace / "trace.jsonl").write_text(
            "".join(json.dumps({"event": event}) + "\n" for event in events), encoding="utf-8"
        )
        write_json(workspace / "lladar-service.json", {"stopped": True, "port": 0})
        observations = [
            {"phase": phase, "ok": True, "output": "API客服：fixture", "workspace": str(workspace)}
            for phase in ["verification", "dataset", "dataset", "dataset"]
        ]
        (adapter / "observations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in observations), encoding="utf-8"
        )
        write_json(adapter / "audit.json", [{"tool": "query_graph"}])
        return 1

    monkeypatch.setattr(module, "run_agent", fake_run_agent)
    monkeypatch.setattr(sys, "argv", [str(script), "--target-python", "fixture-python"])

    module.main()

    verification = json.loads(next((tmp_path / ".lladar").glob("live-rest-graph-*/verification.json"))
                              .read_text(encoding="utf-8"))
    assert verification["answers"] == 1
    assert verification["verified_requests"] == 4
    assert runtime_calls == [Path("fixture-python")]
    assert inventory_calls == [target, target]
