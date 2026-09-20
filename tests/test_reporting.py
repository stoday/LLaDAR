from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lladar.reporting import create_report
from lladar.exceptions import EvaluationError


def _writer(facts):
    return {
        "dataset_introduction": "資料來自已封存的來源。",
        "test_method": "執行待測對象回答題目。",
        "target_description": "身分依執行紀錄辨識。",
        "evaluation_method": "依封存規則評估。",
        "results_interpretation": "結果須連同覆蓋率與限制閱讀。",
    }


def _write(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def test_v3_directory_report_has_programmatic_appendix_and_metrics(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source_dir = bundle / "evidence" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "README.md").write_text("DO_NOT_SEND_SOURCE_TEXT", encoding="utf-8")
    _write(bundle / "cases.jsonl", {
        "id": "q1", "kind": "single_choice", "prompt": "選哪個？",
        "options": [{"id": "0", "text": "甲"}], "status": "ready",
    })
    _write(bundle / "scoring-plan.json", {"rules": [{"id": "exact", "method": "exact_option"}]})
    _write(bundle / "manifest.json", {"source": {"url": "https://example.org/test"},
                                     "counts": {"source": 1, "ready": 1}})
    answers = tmp_path / "answers.jsonl"
    _write(answers, {"id": "q1", "sample_id": "q1:1", "question": "選哪個？\\n0. 甲",
                     "answer": "答案甲", "status": "ok"})
    workspace = tmp_path / "project-snapshot"
    workspace.mkdir()
    (workspace / "README.md").write_text("A sample agent.", encoding="utf-8")
    _write(tmp_path / "answers.jsonl.run.json", {
        "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest(),
        "target": {"workspace_path": str(workspace),
                   "project_profile": {"status": "ready", "project_name": "範例專案",
                                      "agent_name": "範例 Agent", "model_name": None,
                                      "identity_status": "inferred",
                                      "evidence": ["README.md"], "candidates": [],
                                      "evidence_sha256": {"README.md": hashlib.sha256(
                                          (workspace / "README.md").read_bytes()).hexdigest()}}}
    })
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    scoring = evaluation / "scoring-plan.json"
    _write(scoring, {"rules": [{"id": "exact", "method": "exact_option"}]})
    _write(evaluation / "report.json", {
        "schema_version": 3, "dataset": str(bundle), "answers": str(answers),
        "scoring_plan": {"source": "rediscovered",
                         "sha256": hashlib.sha256(scoring.read_bytes()).hexdigest()},
        "summary": {"source": 1, "ready": 1, "scored": 1, "coverage": 1.0},
        "items": [{"id": "q1", "sample_id": "q1:1", "kind": "single_choice",
                   "status": "scored", "score": 1.0}],
        "source_metrics": [{"name": "accuracy", "group": "all", "eligible": 1,
                            "denominator": 1, "value": 1.0}],
        "unsupported_metrics": [{"name": "bias score", "reason": "unsupported"}],
    })
    output = tmp_path / "report.md"
    create_report(evaluation, output, writer=_writer)
    text = output.read_text(encoding="utf-8")
    assert "範例專案" in text
    assert "答案甲" in text
    assert "選哪個？" in text
    assert "0. 甲" in text
    assert "bias score" in text
    assert "accuracy" in text
    sidecar = json.loads((tmp_path / "report.md.meta.json").read_text(encoding="utf-8"))
    agent_input = json.dumps(sidecar["agent_input"], ensure_ascii=False)
    assert "答案甲" not in agent_input
    assert "DO_NOT_SEND_SOURCE_TEXT" not in agent_input
    assert sidecar["agent_input"]["evaluation_summary"]["scored"] == 1
    (workspace / "README.md").write_text("Changed after the run.", encoding="utf-8")
    stale = tmp_path / "stale.md"
    create_report(evaluation, stale, writer=_writer)
    assert "待測專案概述來源無法核對" in stale.read_text(encoding="utf-8")
    assert "範例專案" not in stale.read_text(encoding="utf-8")


def test_v2_missing_artifacts_and_zero_score_still_report(tmp_path: Path):
    source = tmp_path / "eval.json"
    _write(source, {
        "schema_version": 2,
        "evaluation": {"protocol": "lladar-bfs", "model": "judge",
                       "dataset": str(tmp_path / "gone.jsonl"),
                       "answers": str(tmp_path / "gone-answers.jsonl")},
        "summary": {"scheduled_comparisons": 1, "eligible_comparisons": 0,
                    "bfs_lladar": None},
        "items": [{"id": "v1", "label": "alignment_error"}],
        "sessions": [{"id": "v1", "kind": "contrastive", "status": "alignment_error"}],
    })
    output = tmp_path / "v2.md"
    create_report(source, output, writer=_writer)
    text = output.read_text(encoding="utf-8")
    assert "本次沒有可解讀的評分結果" in text
    assert "資料集" in text
    assert "逐題回答檔" in text
    assert "未取得" in text
    assert "評審模型：judge" in text


def test_report_agent_failure_preserves_diagnostics(tmp_path: Path):
    source = tmp_path / "eval.json"
    _write(source, {"schema_version": 3, "dataset": "missing", "answers": "missing",
                    "summary": {"scored": 0}, "items": [],
                    "scoring_plan": {"source": "sealed_import"}})
    def broken(_facts):
        raise RuntimeError("provider down")
    with pytest.raises(EvaluationError, match="report_generation_error"):
        create_report(source, tmp_path / "report.md", writer=broken)
    assert not (tmp_path / "report.md").exists()
    failure = json.loads((tmp_path / "report.md.failed.json").read_text(encoding="utf-8"))
    assert "provider down" in failure["error"]


def test_cli_uses_akasha_for_report_narrative(tmp_path: Path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from lladar.cli import main

    source = tmp_path / "eval.json"
    _write(source, {"schema_version": 3, "dataset": "missing", "answers": "missing",
                    "summary": {"scored": 0}, "items": [],
                    "scoring_plan": {"source": "sealed_import"}})
    prompts = []
    def agents(**_kwargs):
        def answer(prompt):
            prompts.append(prompt)
            return json.dumps(_writer({}), ensure_ascii=False)
        return answer
    monkeypatch.setitem(sys.modules, "akasha", SimpleNamespace(agents=agents))
    output = tmp_path / "report.md"
    assert main(["report", str(source), "--output", str(output)]) == 0
    assert output.is_file()
    assert len(prompts) == 1
    assert "FACTS_JSON" in prompts[0]


def test_report_rejects_unverified_agent_number(tmp_path: Path):
    source = tmp_path / "eval.json"
    _write(source, {"schema_version": 3, "dataset": "missing", "answers": "missing",
                    "summary": {"scored": 0}, "items": [],
                    "scoring_plan": {"source": "sealed_import"}})
    narrative = _writer({})
    narrative["results_interpretation"] = "100% 正確。"
    with pytest.raises(EvaluationError, match="unverified number"):
        create_report(source, tmp_path / "report.md", writer=lambda _: narrative)


def test_report_rejects_evaluator_identity_as_target(tmp_path: Path):
    source = tmp_path / "eval.json"
    _write(source, {"schema_version": 3, "dataset": "missing", "answers": "missing",
                    "summary": {"scored": 0}, "items": [],
                    "scoring_plan": {"source": "sealed_import"}})
    narrative = _writer({})
    narrative["target_description"] = "評審模型就是待測模型。"
    with pytest.raises(EvaluationError, match="mixed evaluator"):
        create_report(source, tmp_path / "report.md", writer=lambda _: narrative)
