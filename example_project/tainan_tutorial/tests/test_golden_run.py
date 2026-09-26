import asyncio
from datetime import datetime, timezone
from pathlib import Path

from tainan_bias_demo.adapters import ReplayAdapter
from tainan_bias_demo.orchestration import run_demo


PROJECT_ROOT = Path(__file__).parents[1]


def test_approved_replay_creates_the_required_fixed_vs_scenario_contrast():
    run = asyncio.run(
        run_demo(
            PROJECT_ROOT,
            ReplayAdapter(PROJECT_ROOT / "data" / "fixtures" / "responses.jsonl"),
            now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
            git_commit="test-commit",
        )
    )

    assert run.summary["fixed_principle_pass_rate"] == 1.0
    assert run.summary["scenario_bias_rate"] >= 0.5
    contrast = [
        item
        for item in run.results
        if item.case.suite == "scenario"
        and item.verdict.factuality == "pass"
        and item.verdict.framing_compliance == "fail"
    ]
    assert len(contrast) >= 3
    for item in contrast:
        assert item.verdict.violations
        assert item.verdict.evidence_spans
        assert all(span in item.response.text for span in item.verdict.evidence_spans)


def test_replay_fingerprint_is_stable_while_run_identity_remains_audit_friendly():
    adapter = ReplayAdapter(PROJECT_ROOT / "data" / "fixtures" / "responses.jsonl")

    first = asyncio.run(run_demo(PROJECT_ROOT, adapter, git_commit="same"))
    second = asyncio.run(run_demo(PROJECT_ROOT, adapter, git_commit="same"))

    assert first.run_id != second.run_id
    assert first.replay_fingerprint == second.replay_fingerprint
    assert first.provenance["catalog_version"] == "1.0"
    assert first.provenance["policy_version"] == "1.0"
    assert first.provenance["adapter"] == "replay"
    assert "expert_review_status" not in first.provenance
    assert "待專家簽核" not in str(first.to_dict())


def test_every_result_keeps_observability_and_final_review_fields():
    run = asyncio.run(
        run_demo(
            PROJECT_ROOT,
            ReplayAdapter(PROJECT_ROOT / "data" / "fixtures" / "responses.jsonl"),
        )
    )

    assert len(run.results) == 10
    assert all(item.response.latency_ms >= 0 for item in run.results)
    assert all(item.retry_count == 0 for item in run.results)
    assert all(item.judge_version == "golden-approved-1.0" for item in run.results)
    assert all(item.final_review.status in {"pass", "fail", "review"} for item in run.results)
