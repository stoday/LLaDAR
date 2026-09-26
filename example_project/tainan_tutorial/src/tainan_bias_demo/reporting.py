from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import (
    AgentResponse,
    CaseResult,
    DemoCase,
    DemoRun,
    DetectionSignal,
    ReviewRecord,
    Verdict,
)


@dataclass(frozen=True)
class ReportArtifacts:
    json_path: Path
    html_path: Path


def _case(item: dict[str, Any]) -> DemoCase:
    return DemoCase(
        id=item["id"],
        suite=item["suite"],
        question=item["question"],
        context=item["context"],
        facts=tuple(item["facts"]),
        rules=tuple(item["rules"]),
        tags=tuple(item["tags"]),
    )


def run_from_dict(data: dict[str, Any]) -> DemoRun:
    results: list[CaseResult] = []
    for item in data["results"]:
        response = item["response"]
        verdict = item["verdict"]
        review = item["final_review"]
        results.append(
            CaseResult(
                case=_case(item["case"]),
                response=AgentResponse(
                    text=response["text"],
                    raw=response.get("raw"),
                    model=response["model"],
                    latency_ms=int(response["latency_ms"]),
                    token_usage=response.get("token_usage", {}),
                    error=response.get("error"),
                ),
                signals=tuple(DetectionSignal(**signal) for signal in item["signals"]),
                verdict=Verdict(
                    factuality=verdict["factuality"],
                    framing_compliance=verdict["framing_compliance"],
                    violations=tuple(verdict["violations"]),
                    evidence_spans=tuple(verdict["evidence_spans"]),
                    rationale=verdict["rationale"],
                    confidence=float(verdict["confidence"]),
                ),
                final_review=ReviewRecord(**review),
                retry_count=int(item["retry_count"]),
                judge_version=item["judge_version"],
            )
        )
    return DemoRun(
        run_id=data["run_id"],
        created_at=data["created_at"],
        provenance=data["provenance"],
        results=tuple(results),
        summary=data["summary"],
        replay_fingerprint=data["replay_fingerprint"],
    )


def _status_label(status: str) -> str:
    return {"pass": "通過", "fail": "不通過", "review": "待討論"}.get(status, status)


def render_html(run: DemoRun) -> str:
    fixed = run.summary["fixed_principle_pass_rate"] * 100
    scenario = run.summary["scenario_bias_rate"] * 100
    cards: list[str] = []
    for item in run.results:
        evidence = "".join(
            f'<mark>{html.escape(span)}</mark>' for span in item.verdict.evidence_spans
        ) or '<span class="muted">無觸發片段</span>'
        violations = " ".join(
            f'<code>{html.escape(rule)}</code>' for rule in item.verdict.violations
        ) or '<span class="muted">無</span>'
        cards.append(
            f'''<article class="case-card {html.escape(item.case.suite)}">
              <header><span class="case-id">{html.escape(item.case.id)}</span>
                <span class="suite">{"固定題" if item.case.suite == "fixed" else "實際導覽題"}</span></header>
              <h3>{html.escape(item.case.question)}</h3>
              <p class="answer">{html.escape(item.response.text)}</p>
              <div class="verdicts">
                <span>史實 <strong>{_status_label(item.verdict.factuality)}</strong></span>
                <span>框架 <strong>{_status_label(item.final_review.status)}</strong></span>
              </div>
              <details><summary>查看判定證據</summary>
                <p class="evidence">{evidence}</p><p>{violations}</p>
                <p>{html.escape(item.verdict.rationale)}</p>
                <p class="muted">信心 {item.verdict.confidence:.2f} · {html.escape(item.judge_version)}</p>
              </details>
            </article>'''
        )
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>臺南古蹟導覽偏見測試報告</title><style>
:root{{--ink:#17201d;--paper:#f6f2e8;--red:#9b392c;--teal:#176b68;--gold:#d39b3a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 system-ui,"Noto Sans TC",sans-serif}}
main{{max-width:1120px;margin:auto;padding:48px 24px}}h1{{font-family:serif;font-size:clamp(2rem,5vw,4.5rem);line-height:1.05;margin:.25em 0}}
.eyebrow{{letter-spacing:.16em;color:var(--red);font-weight:800}}.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin:32px 0}}
.metric{{background:#fff;border:1px solid #d9d0bd;border-radius:18px;padding:24px;box-shadow:0 12px 36px #49392314}}.metric b{{display:block;font:700 3.5rem/1 serif;color:var(--teal)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}.case-card{{background:#fff;border:1px solid #d9d0bd;border-top:5px solid var(--teal);border-radius:14px;padding:20px}}
.case-card.scenario{{border-top-color:var(--red)}}header,.verdicts{{display:flex;justify-content:space-between;gap:10px}}.case-id{{font-weight:900}}.suite,.muted{{color:#6e746f}}.answer{{padding:14px;background:#f3efe5;border-radius:10px}}
.verdicts span{{flex:1;border:1px solid #ddd3c0;padding:8px 10px;border-radius:8px}}details{{margin-top:14px}}summary{{cursor:pointer;font-weight:800}}mark{{background:#ffdf8b;padding:2px 4px;margin-right:6px}}code{{color:var(--red);margin-right:6px}}footer{{margin-top:40px;color:#6e746f;font-size:.9rem}}
@media(max-width:640px){{.metrics{{grid-template-columns:1fr}}}}
</style></head><body><main><p class="eyebrow">TAINAN · REVIEWED SERVICE REPLAY</p>
<h1>它知道偏見，<br>不代表它不會偏見。</h1>
<p>固定題測「知不知道」，實際導覽題測「做不做得到」。史實與敘事框架分開判讀。</p>
<section class="metrics" aria-label="比較摘要"><div class="metric"><b>{fixed:.0f}%</b>固定題原則通過率</div><div class="metric"><b>{scenario:.0f}%</b>情境題框架偏差率</div></section>
<section class="grid">{''.join(cards)}</section>
<footer>run {html.escape(run.run_id)} · replay fingerprint {html.escape(run.replay_fingerprint[:16])}</footer>
</main></body></html>'''


class ReportStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str, suffix: str) -> Path:
        if not run_id or any(character not in "0123456789abcdef-" for character in run_id.lower()):
            raise ValueError("invalid run id")
        return self.root / f"{run_id}.{suffix}"

    def save(self, run: DemoRun) -> ReportArtifacts:
        json_path = self._path(run.run_id, "json")
        html_path = self._path(run.run_id, "html")
        json_path.write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        html_path.write_text(render_html(run), encoding="utf-8", newline="\n")
        return ReportArtifacts(json_path=json_path, html_path=html_path)

    def load(self, run_id: str) -> DemoRun:
        data = json.loads(self._path(run_id, "json").read_text(encoding="utf-8"))
        return run_from_dict(data)

    def list_runs(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                found.append({"run_id": data["run_id"], "created_at": data["created_at"]})
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return found
