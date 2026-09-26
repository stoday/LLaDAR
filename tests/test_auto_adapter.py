"""Real subprocess contract checks. No mocked models, executors or providers."""
from pathlib import Path
import json
import sys

import pytest

from lladar.auto_adapter import AutoAdapter, CODING_PROMPT, REPAIR_PROMPT, _harness_feedback
from lladar.adapter_workspace import WorkspaceExplorer, ExplorationBudget
from lladar.exceptions import DatasetValidationError
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
    code = (
        "import json,sys\n"
        "r=json.load(sys.stdin)\n"
        "print('protocol diagnostic', file=sys.stderr)\n"
        f"print(json.dumps({payload}))\n"
    ).encode()
    result = adapter.execute(code, "question", phase="verification")
    assert not result["ok"]
    assert expected in result["error"]
    assert result["diagnostic"] == "protocol diagnostic"
    assert "protocol diagnostic" in result["error"]


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


def test_parsed_protocol_failure_reports_redacted_adapter_stderr_for_repair(tmp_path):
    adapter = controller(tmp_path)
    adapter.explorer.environment["TEST_API_KEY"] = "not-a-real-secret-12345"
    source = b'''import json, os, sys
request = json.load(sys.stdin)
print("raw target output: " + os.environ["TEST_API_KEY"], file=sys.stderr)
print(json.dumps({"request_id": request["request_id"], "output": "", "observation": "stdout"}))
'''

    result = adapter.execute(source, "question", phase="exploration")

    assert not result["ok"]
    assert "received an empty string" in result["error"]
    assert result["diagnostic"] == "raw target output: [REDACTED]"
    assert "not-a-real-secret" not in json.dumps(result)
    evidence = (adapter.evidence / "observations.jsonl").read_text(encoding="utf-8")
    assert "raw target output: [REDACTED]" in evidence
    assert "not-a-real-secret" not in evidence


def test_protocol_diagnostic_omits_provider_debug_metadata(tmp_path):
    adapter = controller(tmp_path)
    source = b'''import json, sys
request = json.load(sys.stdin)
print("response_metadata=private-provider-details", file=sys.stderr)
print(json.dumps({"request_id": request["request_id"], "output": "", "observation": "stdout"}))
'''

    result = adapter.execute(source, "question", phase="exploration")

    assert result["diagnostic"] == "[provider debug metadata omitted]"
    assert "private-provider-details" not in json.dumps(result)
    evidence = (adapter.evidence / "observations.jsonl").read_text(encoding="utf-8")
    assert "private-provider-details" not in evidence


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


def test_harness_write_rejects_invalid_python_without_replacing_last_valid_version(tmp_path):
    explorer = WorkspaceExplorer(tmp_path)
    path = explorer.write_harness("adapter.py", "print('valid')\n")

    with pytest.raises(ValueError, match="valid Python"):
        explorer.write_harness("adapter.py", "print('unterminated)\n")

    assert (tmp_path / path).read_text(encoding="utf-8") == "print('valid')\n"


def test_tool_budget_is_per_agent_turn_while_audit_history_is_cumulative(tmp_path):
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    explorer = WorkspaceExplorer(tmp_path, budget=ExplorationBudget(max_tool_calls=1))

    assert explorer.list_files() == ["main.py"]
    with pytest.raises(RuntimeError, match="budget"):
        explorer.list_files()

    explorer.start_agent_turn()
    assert explorer.list_files() == ["main.py"]
    assert [event.sequence for event in explorer.audit_events] == [1, 2]


@pytest.mark.parametrize("prompt", [CODING_PROMPT, REPAIR_PROMPT])
def test_every_fresh_coding_agent_receives_the_complete_proposal_contract(prompt):
    assert "Finish with JSON only" in prompt
    assert '"harness":' in prompt
    assert '"explanation":' in prompt
    assert '"blockers":[]' in prompt


def test_harness_feedback_explains_tool_argument_and_runtime_protocol():
    feedback = _harness_feedback({"ok": False, "error": "bad output"})

    assert feedback["verification"] == {"ok": False, "error": "bad output"}
    assert feedback["adapter_runtime_protocol"]["stdin"] == {
        "request_id": "<same id to echo>",
        "message": "<exact probe question>",
    }
    assert "plain-text" in feedback["run_harness_tool_argument"]
    assert "JSON" in feedback["run_harness_tool_argument"]

def test_empty_dataset_is_rejected_before_model_execution(tmp_path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("")
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "answers.jsonl"
    with pytest.raises(DatasetValidationError, match="no records"):
        run_agent(dataset, output, project=project, runs_root=tmp_path / "runs",
                  model="invalid-provider:must-not-be-called", verbose=False)
    assert not output.exists()


def test_independent_verification_repairs_then_replays_every_probe(tmp_path):
    adapter = controller(tmp_path)
    adapter.source = b"import sys; sys.exit(1)\n"
    repaired = []

    def repair(failure, attempt):
        repaired.append((failure, attempt))
        harness = adapter.workspace / ".lladar" / "harnesses"
        harness.mkdir(parents=True, exist_ok=True)
        (harness / "adapter.py").write_text(
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "print(json.dumps({'request_id': request['request_id'], "
            "'output': request['message'], 'observation': 'stdin'}))\n",
            encoding="utf-8",
        )
        adapter._accept_proposal({"harness": "adapter.py", "blockers": []})

    adapter._verify_with_repairs(["first", "second"], repair)

    assert adapter.report["status"] == "verified"
    assert [attempt for _, attempt in repaired] == [1]
    assert len(adapter.report["verification"]) == 4
    assert [row["output"] for row in adapter.report["verification"][-2:]] == ["first", "second"]
    assert adapter.report["adapter_versions"][-1]["path"] == "adapter-01.py"
    assert (adapter.evidence / "adapter-01.py").is_file()


def test_independent_verification_stops_after_fifty_failed_repairs(tmp_path):
    adapter = controller(tmp_path)
    attempts = []

    def repair(failure, attempt):
        attempts.append((failure, attempt))
        raise RuntimeError("still broken")

    with pytest.raises(RuntimeError, match="after 50 repair attempts"):
        adapter._verify_with_repairs(
            [], repair, initial_failure={"phase": "generation", "error": "bad proposal"}
        )

    assert [attempt for _, attempt in attempts] == list(range(1, 51))
    assert adapter.report["repair_attempts"] == 50
    assert len(adapter.report["repairs"]) == 50
    history = adapter._repair_history()
    assert history == adapter.report["repairs"]
    assert history[0]["failure"]["error"] == "bad proposal"
    assert history[-1]["failure"]["error"] == "RuntimeError: still broken"


def test_repair_context_contains_protocol_current_source_and_complete_failure_timeline(tmp_path):
    adapter = controller(tmp_path)
    adapter.source = b"import sys; sys.exit(1)\n"
    repeated = {"phase": "exploration", "ok": False, "adapter_sha256": "aaa",
                "error": "ValueError: stdout is not JSON"}
    different = {"phase": "verification", "ok": False, "adapter_sha256": "bbb",
                 "error": "ValueError: request_id mismatch"}
    observations = adapter.evidence / "observations.jsonl"
    observations.write_text(
        "\n".join(json.dumps(row) for row in (repeated, repeated, different)) + "\n",
        encoding="utf-8",
    )
    adapter.report["repairs"] = [
        {"attempt": 1, "failure": {"phase": "generation", "error": "bad proposal"}}
    ]
    adapter.explorer._record(
        "tool_error", {"tool": "write_harness"}, "Harness must be valid Python"
    )

    context = adapter._repair_context()

    assert context["adapter_runtime_protocol"]["stdout"]["request_id"] == "<same id from stdin>"
    assert context["current_adapter"]["source"] == "import sys; sys.exit(1)\n"
    assert context["repair_history"] == adapter.report["repairs"]
    assert len(context["validation_timeline"]) == 3
    assert len(context["failure_catalog"]) == 2
    first, second, third = context["validation_timeline"]
    assert first["failure_id"] == second["failure_id"]
    assert third["failure_id"] != first["failure_id"]
    assert context["tool_failure_timeline"] == [{
        "sequence": 1,
        "tool": "write_harness",
        "error": "Harness must be valid Python",
    }]


def test_repair_restores_the_last_candidate_sent_to_independent_verification(tmp_path):
    adapter = controller(tmp_path)
    accepted = b"import json, sys\nrequest=json.load(sys.stdin)\n"
    adapter.source = accepted
    adapter.report["proposal"] = {"harness": ".lladar/harnesses/custom.py"}
    scratch = adapter.workspace / ".lladar" / "harnesses" / "custom.py"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("raise RuntimeError('unaccepted scratch edit')\n", encoding="utf-8")

    adapter._restore_verification_candidate()

    assert scratch.read_bytes() == accepted


def test_project_named_adapter_does_not_collide_with_evidence(tmp_path):
    project = tmp_path / "adapter"
    project.mkdir()
    adapter = AutoAdapter(project, python=Path(sys.executable), env_file=None, model="unused")
    assert adapter.evidence != project
    assert adapter.evidence.is_dir()
