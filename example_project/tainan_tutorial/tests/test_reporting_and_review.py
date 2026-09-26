import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from tainan_bias_demo.adapters import ReplayAdapter
from tainan_bias_demo.orchestration import apply_review, run_demo
from tainan_bias_demo.reporting import ReportStore


PROJECT_ROOT = Path(__file__).parents[1]


def make_run():
    return asyncio.run(
        run_demo(
            PROJECT_ROOT,
            ReplayAdapter(PROJECT_ROOT / "data" / "fixtures" / "responses.jsonl"),
            now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
            git_commit="test-commit",
        )
    )


def test_report_store_round_trips_json_and_exports_a_self_contained_html(tmp_path: Path):
    store = ReportStore(tmp_path)
    run = make_run()

    artifacts = store.save(run)
    loaded = store.load(run.run_id)

    assert artifacts.json_path.is_file()
    assert artifacts.html_path.is_file()
    assert loaded.run_id == run.run_id
    assert loaded.replay_fingerprint == run.replay_fingerprint
    assert loaded.results[4].verdict.evidence_spans == ("民國 21 年",)
    html = artifacts.html_path.read_text(encoding="utf-8")
    assert "固定題原則通過率" in html
    assert "情境題框架偏差率" in html
    assert "民國 21 年" in html
    assert "待專家簽核" not in html


def test_export_contains_no_secret_like_configuration_values(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-never-appear")
    monkeypatch.setenv("AUTHORIZATION", "Bearer private-token")
    store = ReportStore(tmp_path)

    artifacts = store.save(make_run())
    serialized = artifacts.json_path.read_text(encoding="utf-8") + artifacts.html_path.read_text(
        encoding="utf-8"
    )

    assert "should-never-appear" not in serialized
    assert "private-token" not in serialized
    json.loads(artifacts.json_path.read_text(encoding="utf-8"))


def test_human_review_preserves_the_judge_result_and_updates_the_final_metric():
    original = make_run()

    reviewed = apply_review(
        original,
        case_id="S01",
        status="pass",
        reviewer="demo-host",
        reason="依已審核服務紀錄修正。",
        now=lambda: datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
    )

    before = next(item for item in original.results if item.case.id == "S01")
    after = next(item for item in reviewed.results if item.case.id == "S01")
    assert before.verdict.framing_compliance == "fail"
    assert after.verdict.framing_compliance == "fail"
    assert after.final_review.status == "pass"
    assert after.final_review.reviewer == "demo-host"
    assert after.final_review.reason == "依已審核服務紀錄修正。"
    assert reviewed.summary["scenario_bias_rate"] < original.summary["scenario_bias_rate"]
