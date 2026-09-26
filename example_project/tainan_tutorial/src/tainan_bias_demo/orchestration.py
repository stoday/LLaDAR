from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .adapters import ReplayAdapter
from .catalog import load_catalog
from .domain import (
    AgentAdapter,
    AgentRequest,
    CaseResult,
    DemoRun,
    DetectionSignal,
    ReviewRecord,
    Verdict,
)
from .rules import detect_signals


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _sha256(path: Path) -> str:
    if not path.is_file():
        return "unavailable"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _error_verdict(error: str) -> Verdict:
    return Verdict(
        factuality="unknown",
        framing_compliance="review",
        violations=(),
        evidence_spans=(),
        rationale=f"Agent 執行失敗：{error}",
        confidence=0.0,
    )


def _verdict(item: dict[str, Any]) -> Verdict:
    return Verdict(
        factuality=item["factuality"],
        framing_compliance=item["framing_compliance"],
        violations=tuple(item.get("violations", ())),
        evidence_spans=tuple(item.get("evidence_spans", ())),
        rationale=item["rationale"],
        confidence=float(item["confidence"]),
    )


def _live_verdict(signals: tuple[DetectionSignal, ...]) -> Verdict:
    return Verdict(
        factuality="unknown",
        framing_compliance="review",
        violations=tuple(dict.fromkeys(signal.rule_id for signal in signals)),
        evidence_spans=tuple(signal.evidence for signal in signals),
        rationale="Live 回答僅完成 deterministic signal 偵測，需由核准 judge 或人工覆核。",
        confidence=0.0,
    )


def _summary(results: list[CaseResult]) -> dict[str, float | int]:
    fixed = [item for item in results if item.case.suite == "fixed"]
    scenario = [item for item in results if item.case.suite == "scenario"]
    fixed_pass = sum(item.final_review.status == "pass" for item in fixed)
    scenario_fail = sum(item.final_review.status == "fail" for item in scenario)
    return {
        "fixed_principle_pass_rate": fixed_pass / len(fixed) if fixed else 0.0,
        "scenario_bias_rate": scenario_fail / len(scenario) if scenario else 0.0,
        "fixed_total": len(fixed),
        "scenario_total": len(scenario),
        "errors": sum(bool(item.response.error) for item in results),
        "reviews": sum(item.final_review.status == "review" for item in results),
    }


async def run_demo(
    project_root: str | Path,
    adapter: AgentAdapter,
    *,
    now: Callable[[], datetime] | None = None,
    git_commit: str | None = None,
) -> DemoRun:
    root = Path(project_root).resolve()
    catalog = load_catalog(root / "data")
    config = _load_yaml(root / "data" / "demo_config.yaml")
    golden = _load_yaml(root / "data" / "golden_verdicts.yaml")
    verdicts = {item["case_id"]: item for item in golden["verdicts"]}
    clock = now or (lambda: datetime.now(timezone.utc))
    created = clock().astimezone(timezone.utc).isoformat()
    judge_version = str(golden["version"])
    adapter_name = "replay" if isinstance(adapter, ReplayAdapter) else "akasha"
    results: list[CaseResult] = []

    for case in catalog.cases:
        request = AgentRequest(
            case_id=case.id,
            messages=({"role": "user", "content": case.question},),
            system_prompt=config["system_prompt"],
            timeout_s=float(config["timeout_s"]),
            metadata={"suite": case.suite, "catalog_version": catalog.version},
        )
        response = await adapter.invoke(request)
        signals = detect_signals(response.text)
        if response.error:
            verdict = _error_verdict(response.error)
        elif adapter_name == "replay":
            verdict = _verdict(verdicts[case.id])
        else:
            verdict = _live_verdict(signals)
        if response.error is None and adapter_name == "replay":
            missing = [span for span in verdict.evidence_spans if span not in response.text]
            if missing:
                verdict = Verdict(
                    factuality=verdict.factuality,
                    framing_compliance="review",
                    violations=verdict.violations,
                    evidence_spans=(),
                    rationale="核准證據未出現在本次回答，需重新覆核。",
                    confidence=0.0,
                )
        results.append(
            CaseResult(
                case=case,
                response=response,
                signals=signals,
                verdict=verdict,
                final_review=ReviewRecord(
                    status=verdict.framing_compliance,
                    reviewer="approved-golden",
                    reviewed_at=created,
                    reason="使用已核准的展示服務 golden verdict。",
                ),
                retry_count=0,
                judge_version=judge_version,
            )
        )

    stable_payload = {
        "catalog_version": catalog.version,
        "policy_sha256": _sha256(root / "data" / "policy.yaml"),
        "config": config,
        "results": [
            {
                "case_id": item.case.id,
                "answer": item.response.text,
                "model": item.response.model,
                "verdict": {
                    "factuality": item.verdict.factuality,
                    "framing": item.verdict.framing_compliance,
                    "violations": item.verdict.violations,
                    "evidence": item.verdict.evidence_spans,
                },
            }
            for item in results
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    provenance = {
        "git_commit": git_commit or _git_commit(root),
        "lock_sha256": _sha256(root / "uv.lock"),
        "catalog_version": catalog.version,
        "policy_version": catalog.version,
        "model": config["model"],
        "system_prompt_sha256": hashlib.sha256(
            config["system_prompt"].encode("utf-8")
        ).hexdigest(),
        "prompt_version": config["prompt_version"],
        "adapter": adapter_name,
        "temperature": config["temperature"],
        "seed": config["seed"],
    }
    return DemoRun(
        run_id=str(uuid.uuid4()),
        created_at=created,
        provenance=provenance,
        results=tuple(results),
        summary=_summary(results),
        replay_fingerprint=fingerprint,
    )


def apply_review(
    run: DemoRun,
    *,
    case_id: str,
    status: str,
    reviewer: str,
    reason: str,
    now: Callable[[], datetime] | None = None,
) -> DemoRun:
    if status not in {"pass", "fail", "review"}:
        raise ValueError("status must be pass, fail, or review")
    if not reviewer.strip() or not reason.strip():
        raise ValueError("reviewer and reason are required")
    timestamp = (now or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    found = False
    updated: list[CaseResult] = []
    for item in run.results:
        if item.case.id != case_id:
            updated.append(item)
            continue
        found = True
        updated.append(
            replace(
                item,
                final_review=ReviewRecord(
                    status=status,
                    reviewer=reviewer.strip(),
                    reviewed_at=timestamp,
                    reason=reason.strip(),
                ),
            )
        )
    if not found:
        raise KeyError(f"unknown case id {case_id}")
    return replace(run, results=tuple(updated), summary=_summary(updated))
