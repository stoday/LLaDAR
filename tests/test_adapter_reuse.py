"""Explicit adapter replay with real processes and no coding-model calls."""
import json
import hashlib
from pathlib import Path
import sys
import venv

import pytest

from lladar.auto_adapter import AutoAdapter
from lladar.cli import main
from lladar.runner import run_agent
from lladar.session_adapter import SessionAutoAdapter


SINGLE = """import json,sys,os
sys.path.insert(0, os.getcwd())
from app import answer
r=json.load(sys.stdin)
print(json.dumps({'request_id':r['request_id'],'output':answer(r['message']),'observation':'app.answer'}))
"""


def fixture_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def answer(message): return 'reply: ' + message\n", encoding="utf-8")
    return project


def test_reused_single_adapter_is_verified_without_coding_agent(tmp_path, monkeypatch):
    project = fixture_project(tmp_path)
    original = tmp_path / "saved.py"
    raw = b"\xef\xbb\xbf" + SINGLE.encode()
    original.write_bytes(raw)
    builder = AutoAdapter(project, python=Path(sys.executable), env_file=None, model="unused", verbose=False)
    monkeypatch.setattr(builder, "prepare", lambda *a, **k: pytest.fail("Must not discover"))
    builder.prepare_existing(original, ["probe"])
    assert builder.report["status"] == "verified"
    assert builder.source == raw
    assert builder.answer("question", "case-1") == "reply: question"
    assert original.read_bytes() == raw
    assert builder.report["repairs"] == []
    assert builder.report["reused_adapter"]["path"] == str(original.resolve())


def test_failed_reuse_does_not_repair_or_modify_source(tmp_path):
    project = fixture_project(tmp_path)
    original = tmp_path / "broken.py"
    raw = b"print('not JSON')\n"
    original.write_bytes(raw)
    builder = AutoAdapter(project, python=Path(sys.executable), env_file=None, model="unused", verbose=False)
    with pytest.raises(RuntimeError, match="Existing adapter verification failed"):
        builder.prepare_existing(original, ["probe"])
    assert original.read_bytes() == raw
    assert builder.report["status"] == "failed"
    assert builder.report["repairs"] == []
    assert json.loads((builder.evidence / "run.json").read_text())["status"] == "failed"


def test_reused_session_adapter_uses_session_verification(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    demo = Path(__file__).parents[1] / "example_project" / "situation_demo"
    (project / "app.py").write_bytes((demo / "app.py").read_bytes())
    original = demo / "lladar_session.py"
    raw = original.read_bytes()
    builder = SessionAutoAdapter(project, python=Path(sys.executable), env_file=None,
                                 model="unused", max_turns=3, verbose=False)
    builder.prepare_existing(original, ["probe"])
    assert builder.report["status"] == "verified"
    assert builder.calibration["same_session_recalled"] is True
    assert builder.calibration["fresh_session_leaked"] is False
    assert original.read_bytes() == raw


def test_runner_reuses_adapter_with_real_target_environment(tmp_path, monkeypatch):
    project = fixture_project(tmp_path)
    venv.EnvBuilder(with_pip=False).create(project / ".venv")
    original = tmp_path / "saved.py"
    original.write_text(SINGLE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "question", "expected_answer": "reply", "actual_response": None}) + "\n")
    monkeypatch.setattr(AutoAdapter, "prepare", lambda *a, **k: pytest.fail("Must not discover"))
    output = tmp_path / "answers.jsonl"
    assert run_agent(dataset, output, project=project, adapt=original,
                     runs_root=tmp_path / "runs", verbose=False) == 1
    assert json.loads(output.read_text())["actual_response"] == "reply: question"
    run = json.loads(Path(str(output) + ".run.json").read_text())
    assert run["target"]["reused_adapter"]["path"] == str(original.resolve())
    assert Path(run["target"]["builder_evidence"]).is_dir()


def test_adapt_requires_explicit_project():
    with pytest.raises(SystemExit):
        main(["run-agent", "data.jsonl", "--adapt", "saved.py"])


def test_known_wrong_protocol_is_rejected_before_execution(tmp_path, monkeypatch):
    project = fixture_project(tmp_path)
    original = tmp_path / "saved.py"
    original.write_text(SINGLE)
    (tmp_path / "run.json").write_text(json.dumps({"protocol": "single",
        "adapter_sha256": hashlib.sha256(original.read_bytes()).hexdigest()}))
    builder = SessionAutoAdapter(project, python=Path(sys.executable), env_file=None,
                                 model="unused", max_turns=1, verbose=False)
    monkeypatch.setattr(builder, "execute", lambda *a, **k: pytest.fail("Wrong protocol must fail before execution"))
    with pytest.raises(ValueError, match="requires session"):
        builder.prepare_existing(original, ["probe"])


@pytest.mark.parametrize("situation", [False, True])
def test_cli_forwards_reuse_path(monkeypatch, situation):
    received = {}
    def run(*args, **kwargs):
        received.update(kwargs)
        return 1
    monkeypatch.setattr("lladar.cli.run_situation" if situation else "lladar.cli.run_agent", run)
    args = ["--situation", "s.json", "--num-scenarios", "1"] if situation else ["data.jsonl"]
    assert main(["run-agent", *args, "--project", "project", "--adapt", "saved.py"]) == 0
    assert received["adapt"] == "saved.py"
