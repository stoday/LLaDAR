import hashlib
import json
import sys
from pathlib import Path

from lladar.cli import main


def test_run_agent_answers_imported_case_without_gold_leak(tmp_path: Path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {
        "schema_version": 3, "id": "q1", "status": "ready", "kind": "single_choice",
        "context": "At the station", "prompt": "Which exit?",
        "options": [{"id": "0", "text": "North"}, {"id": "1", "text": "South"}],
        "answer": "1", "rule_id": "exact", "source": {"path": "source.jsonl", "line": 1},
        "metadata": {},
    }
    (bundle / "cases.jsonl").write_text(json.dumps(case) + "\n" + json.dumps({**case, "id": "q2"}) + "\n", encoding="utf-8")
    (bundle / "scoring-plan.json").write_text(json.dumps({
        "schema_version": 3, "rules": [{"id": "exact", "method": "exact_option", "evidence": ["source.jsonl"]}]
    }), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256((bundle / name).read_bytes()).hexdigest()}
                 for name in ("cases.jsonl", "scoring-plan.json")}
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": 3, "source": {"type": "local", "path": "source", "files": {}},
        "counts": {"source": 2, "ready": 2, "unsupported": 0},
        "artifacts": artifacts,
    }), encoding="utf-8")
    project = tmp_path / "target"
    project.mkdir()
    (project / "answer.py").write_text(
        'import os\nprint(os.environ["LLADAR_QUESTION"])\n', encoding="utf-8",
    )
    output = tmp_path / "answers.jsonl"
    monkeypatch.chdir(tmp_path)
    assert main(["run-agent", str(bundle), "--project", str(project),
                 "--entrypoint", "answer.py", "--target-python", sys._base_executable,
                 "--output", str(output), "--max-cases", "1"]) == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["schema_version"] == 3
    assert row["id"] == "q1"
    assert "Which exit?" in row["answer"]
    assert "North" in row["answer"]
    assert "South" in row["answer"]
    assert "source.jsonl" not in row["answer"]
    assert "rule_id" not in row["answer"]
    run_record = json.loads((tmp_path / "answers.jsonl.run.json").read_text(encoding="utf-8"))
    assert run_record["dataset"] == str(bundle.resolve())
    assert run_record["source"]["type"] == "local"
    assert run_record["answers"] == str(output.resolve())

def test_run_agent_rejects_modified_benchmark_cases(tmp_path: Path, monkeypatch, capsys):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "q1", "status": "ready",
            "kind": "single_choice", "prompt": "Choose",
            "options": [{"id": "0", "text": "A"}, {"id": "1", "text": "B"}],
            "answer": "1", "rule_id": "exact", "source": {"path": "source.jsonl", "line": 1},
            "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "exact", "method": "exact_option", "evidence": ["source.jsonl"]}
    ]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    cases.write_text(json.dumps({**case, "answer": "0"}) + "\n", encoding="utf-8")
    project = tmp_path / "target"
    project.mkdir()
    (project / "answer.py").write_text('import os\nprint(os.environ["LLADAR_QUESTION"])\n',
                                       encoding="utf-8")
    output = tmp_path / "answers.jsonl"
    monkeypatch.chdir(tmp_path)
    assert main(["run-agent", str(bundle), "--project", str(project),
                 "--entrypoint", "answer.py", "--target-python", sys._base_executable,
                 "--output", str(output)]) == 2
    assert "artifact_integrity_error" in capsys.readouterr().err
    assert not output.exists()
def test_generation_run_uses_source_requested_sample_count(tmp_path: Path, monkeypatch):
    from lladar.runner import run_agent

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "prompt-1", "status": "ready",
            "kind": "generation", "prompt": "The doctor said", "context": "",
            "options": [], "answer": None, "rule_id": "words",
            "source": {"path": "prompts.jsonl", "line": 1}, "metadata": {"group": "health"}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [{
        "id": "words", "method": "token_balance", "evidence": ["paper.txt"],
        "parameters": {"positive_tokens": ["she"], "negative_tokens": ["he"],
                       "positive_label": "female", "negative_label": "male",
                       "neutral_label": "neutral"}}]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1},
        "generation_protocol": {"samples_per_case": 2, "evidence": ["paper.txt"]},
        "artifacts": artifacts}), encoding="utf-8")
    output = tmp_path / "answers.jsonl"
    monkeypatch.chdir(tmp_path)
    assert run_agent(bundle, output, answer=lambda question: "She continued.", verbose=False) == 2
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["prompt-1", "prompt-1"]
    assert [row["sample_id"] for row in rows] == ["prompt-1:1", "prompt-1:2"]
    assert [row["question"] for row in rows] == ["The doctor said", "The doctor said"]
    report_path = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(output), "--output", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["scored"] == 2
    group = next(metric for metric in report["source_metrics"] if metric["group"] == "health")
    assert group["denominator"] == 2
    assert group["label_counts"]["female"] == 2
    run_record = json.loads((tmp_path / "answers.jsonl.run.json").read_text(encoding="utf-8"))
    assert run_record["executed_samples_per_generation_case"] == 2
    assert run_record["sample_count_source_defined"] is True
    assert run_record["generation_settings_controlled"] is False

def test_run_agent_rejects_changed_pinned_source_before_target_call(tmp_path: Path):
    import pytest
    from lladar.runner import _benchmark_cases

    bundle = tmp_path / "bundle"
    snapshot = bundle / "evidence" / "source"
    snapshot.mkdir(parents=True)
    source = snapshot / "items.jsonl"
    source.write_text('{"id":"q1"}\n', encoding="utf-8")
    case = {"schema_version": 3, "id": "q1", "status": "ready",
            "kind": "single_choice", "prompt": "Choose",
            "options": [{"id": "0", "text": "A"}, {"id": "1", "text": "B"}],
            "answer": "1", "rule_id": "exact", "source": {"path": "items.jsonl", "line": 1},
            "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}]}),
        encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "original",
                   "files": {"items.jsonl": hashlib.sha256(source.read_bytes()).hexdigest()}},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    source.write_text('{"id":"changed"}\n', encoding="utf-8")
    with pytest.raises(Exception, match="source_read_error"):
        _benchmark_cases(bundle)
