"""Real subprocess contract checks. No mocked models, executors or providers."""
from pathlib import Path
import json
import sys

import pytest

from lladar.auto_adapter import AutoAdapter
from lladar.adapter_workspace import WorkspaceExplorer, ExplorationBudget
from lladar.runner import run_agent


def controller(tmp_path, timeout=10):
    project = tmp_path / "project"
    project.mkdir()
    (project / "counter.py").write_text(
        "from pathlib import Path\n"
        "def next_value():\n"
        " p = Path('counter.txt')\n"
        " n = int(p.read_text()) + 1 if p.exists() else 1\n"
        " p.write_text(str(n))\n"
        " return str(n)\n", encoding="utf-8")
    return AutoAdapter(project, python=Path(sys.executable), env_file=None,
                       model="unused-for-subprocess-contract", timeout=timeout,
                       verbose=False)


def test_actual_subprocess_isolates_state_and_records_case_identity(tmp_path):
    adapter = controller(tmp_path)
    source = b'''import sys, json, os
sys.path.insert(0, os.getcwd())
from counter import next_value
r=json.load(sys.stdin)
print(json.dumps(dict(request_id=r['request_id'], output=next_value(), observation='counter module in fresh project')))
'''
    results = [adapter.execute(source, "next", phase="dataset", case_id=str(i)) for i in range(2)]
    assert all(result["ok"] and result["output"] == "1" for result in results)
    assert results[0]["request_id"] != results[1]["request_id"]
    assert not (adapter.workspace / "counter.txt").exists()
    observed = [json.loads(line) for line in (adapter.evidence / "observations.jsonl").read_text().splitlines()]
    assert [row["case_id"] for row in observed] == ["0", "1"]


@pytest.mark.parametrize("payload, expected", [
    ("dict(request_id='wrong', output='answer', observation='file')", "correlated"),
    ("dict(request_id=r['request_id'], output='', observation='file')", "nonempty"),
    ("dict(request_id=r['request_id'], output={'text':'answer'}, observation='file')", "string"),
    ("dict(request_id=r['request_id'], output='answer')", "observed"),
    ("dict(request_id=r['request_id'], output='AIMessage(response_metadata=private)', observation='debug')", "metadata"),
])
def test_actual_subprocess_rejects_invalid_protocol(tmp_path, payload, expected):
    adapter = controller(tmp_path)
    code = f"import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({payload}))\n".encode()
    result = adapter.execute(code, "question", phase="verification")
    assert not result["ok"]
    assert expected in result["error"]


def test_actual_subprocess_times_out(tmp_path):
    adapter = controller(tmp_path, timeout=0.2)
    result = adapter.execute(b"import time; time.sleep(30)", "question", phase="verification")
    assert not result["ok"]
    assert "TimeoutExpired" in result["error"]


def test_missing_protocol_reports_real_stderr_for_repair(tmp_path):
    adapter = controller(tmp_path)
    result = adapter.execute(b"import sys; print('missing required module',file=sys.stderr)",
                             "question", phase="exploration")
    assert not result["ok"]
    assert "missing required module" in result["error"]


def test_actual_subprocess_rejects_source_changes_and_redacts_error_secrets(tmp_path):
    adapter = controller(tmp_path)
    code = b'''import json,sys
from pathlib import Path
r=json.load(sys.stdin)
Path('counter.py').write_text('broken')
print(json.dumps(dict(request_id=r['request_id'], output='answer', observation='file')))
'''
    result = adapter.execute(code, "question", phase="verification")
    assert not result["ok"] and "changed target" in result["error"]
    assert "next_value" in (adapter.workspace / "counter.py").read_text()
    adapter.explorer.environment["TEST_API_KEY"] = "not-a-real-secret-12345"
    result = adapter.execute(
        b"import os,sys; print(os.environ['TEST_API_KEY'],file=sys.stderr); sys.exit(1)",
        "question", phase="verification")
    assert not result["ok"] and "[REDACTED]" in result["error"]
    assert "not-a-real-secret" not in (adapter.evidence / "observations.jsonl").read_text()


def test_tools_enforce_paths_secrets_and_budget(tmp_path):
    (tmp_path / ".env").write_text("SECRET=private")
    explorer = WorkspaceExplorer(tmp_path, budget=ExplorationBudget(max_tool_calls=4))
    assert explorer.list_files() == []
    with pytest.raises(ValueError, match="readable"):
        explorer.read_file(".env")
    with pytest.raises(ValueError, match="outside"):
        explorer.read_file("../outside.py")
    with pytest.raises(ValueError, match="filename"):
        explorer.write_harness("../adapter.py", "")
    with pytest.raises(RuntimeError, match="budget"):
        explorer.list_files()


def test_empty_dataset_needs_no_model_and_preserves_output_guard(tmp_path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("")
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "answers.jsonl"
    assert run_agent(dataset, output, project=project, runs_root=tmp_path / "runs",
                     model="invalid-provider:must-not-be-called", verbose=False) == 0
    assert output.read_text() == ""
    with pytest.raises(FileExistsError):
        run_agent(dataset, output, project=project)


def test_project_named_adapter_does_not_collide_with_evidence(tmp_path):
    project = tmp_path / "adapter"
    project.mkdir()
    adapter = AutoAdapter(project, python=Path(sys.executable), env_file=None, model="unused")
    assert adapter.evidence != project
    assert adapter.evidence.is_dir()
