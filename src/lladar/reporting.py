"""Evidence-bounded Markdown reports for schema-v2 and schema-v3 evaluations."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .exceptions import EvaluationError


DEFAULT_REPORT_MODEL = "gemini:gemini-3.7-flash"
_SECTIONS = ("dataset_introduction", "test_method", "target_description",
             "evaluation_method", "results_interpretation")
_TITLES = {
    "dataset_introduction": "測試資料介紹",
    "test_method": "測試方法",
    "target_description": "待測專案、模型與 Agent",
    "evaluation_method": "評估方法",
    "results_interpretation": "評估結果說明",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationError(f"report input is not a JSON object: {path}")
    return value


def _jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise EvaluationError(f"invalid JSONL record at {path}:{number}")
            rows.append(value)
    return rows


def _ref(value: Any, base: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_file() or path.is_dir():
        return path.resolve()
    candidate = base / path
    return candidate.resolve() if candidate.exists() else None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fence(value: Any) -> str:
    text = str(value) if value is not None else "未取得"
    runs = [len(match.group()) for match in re.finditer(r"`+", text)]
    marker = "`" * max(3, (max(runs) + 1) if runs else 3)
    return f"{marker}text\n{text}\n{marker}"


def _cell(value: Any) -> str:
    if value is None:
        return "未取得"
    return str(value).replace("|", "&#124;").replace("\n", "<br>").replace("\r", "")


def _number(value: Any) -> str:
    if value is None:
        return "未取得"
    if isinstance(value, float):
        return f"{value:.8g}" if 0 < abs(value) < 0.0001 else f"{value:.4f}"
    return str(value)


def _source_name(source: Any) -> str:
    if not isinstance(source, dict):
        return "未記錄"
    return str(source.get("url") or source.get("path") or "未記錄")


def _read_inputs(eval_input: str | Path) -> dict[str, Any]:
    given = Path(eval_input).resolve()
    report_path = given / "report.json" if given.is_dir() else given
    if not report_path.is_file():
        raise EvaluationError(f"evaluation report not found: {report_path}")
    report = _load_json(report_path)
    audit_path = report_path.parent / "evidence" / "agent-audit.json"
    audit_model = None
    if audit_path.is_file():
        try:
            audit_model = _load_json(audit_path).get("model")
        except (OSError, ValueError):
            pass
    version = report.get("schema_version")
    if version not in (2, 3):
        raise EvaluationError(f"unsupported evaluation schema: {version}")
    if not isinstance(report.get("summary"), dict) or not isinstance(report.get("items"), list):
        raise EvaluationError("evaluation report has no verifiable summary/items")
    base = report_path.parent
    evaluation = report.get("evaluation", {}) if version == 2 else report
    dataset = _ref(evaluation.get("dataset"), base)
    answers = _ref(evaluation.get("answers"), base)
    run_path = (answers.with_name(answers.name + ".run.json") if answers else None)
    run = _load_json(run_path) if run_path and run_path.is_file() else None
    missing = []
    if dataset is None:
        missing.append("資料集")
    if answers is None:
        missing.append("逐題回答檔")
    if run is None:
        missing.append("執行紀錄")
    elif answers is not None and run.get("answers_sha256") != _digest(answers):
        missing.append("執行紀錄的回答雜湊不符")
        run = None
    if run is not None:
        target = run.get("target") or {}
        profile = target.get("project_profile") or {}
        if profile.get("status") == "ready":
            workspace = _ref(target.get("profile_evidence_path") or target.get("workspace_path"), base)
            hashes = profile.get("evidence_sha256")
            valid = bool(workspace and workspace.is_dir() and isinstance(hashes, dict)
                         and hashes)
            if valid:
                for name, expected in hashes.items():
                    candidate = (workspace / name).resolve()
                    if (not candidate.is_relative_to(workspace.resolve())
                            or not candidate.is_file() or _digest(candidate) != expected):
                        valid = False
                        break
            if not valid:
                missing.append("待測專案概述來源無法核對")
                target["project_profile"] = {"status": "unavailable",
                                              "reason": "evidence unavailable or modified"}
        else:
            missing.append("待測專案概述")
    manifest_path = dataset / "manifest.json" if dataset and dataset.is_dir() else None
    manifest = _load_json(manifest_path) if manifest_path and manifest_path.is_file() else None
    if run is not None and manifest_path is not None and run.get("dataset_manifest_sha256"):
        if run["dataset_manifest_sha256"] != _digest(manifest_path):
            missing.append("資料集 manifest 雜湊不符")
            manifest = None
    cases = _jsonl(dataset / "cases.jsonl" if dataset and dataset.is_dir() else dataset)
    observations = _jsonl(answers)
    scoring_path = ((base / "scoring-plan.json")
                    if version == 3 and report.get("scoring_plan", {}).get("source") == "rediscovered"
                    else (dataset / "scoring-plan.json" if dataset and dataset.is_dir() else None))
    scoring = _load_json(scoring_path) if scoring_path and scoring_path.is_file() else None
    if version == 3 and scoring_path and scoring and report.get("scoring_plan", {}).get("sha256") != _digest(scoring_path):
        missing.append("評分方案雜湊不符")
        scoring = None
    if scoring is None and version == 3:
        missing.append("評分方案")
    documents: list[dict[str, str]] = []
    if dataset and dataset.is_dir():
        source_root = dataset / "evidence" / "source"
        if source_root.is_dir():
            candidates = [path for path in source_root.iterdir()
                          if path.is_file() and path.name.lower().startswith(("readme", "dataset"))]
            for path in sorted(candidates)[:3]:
                if path.suffix.lower() not in {".md", ".txt", ".rst"} or path.stat().st_size > 500_000:
                    continue
                documents.append({"path": str(path.relative_to(dataset)),
                                  "sha256": _digest(path)})
    return {"report": report, "report_path": report_path, "version": version,
            "audit_model": audit_model,
            "dataset_path": dataset, "answers_path": answers, "run_path": run_path,
            "manifest_path": manifest_path, "scoring_path": scoring_path,
            "manifest": manifest, "run": run, "scoring": scoring,
            "cases": cases, "observations": observations, "missing": missing,
            "source_documents": documents}


def _case_questions(cases: list[dict[str, Any]], version: int) -> dict[str, str]:
    if version == 3:
        questions = {}
        for case in cases:
            if not isinstance(case.get("id"), str):
                continue
            parts = [case.get("context", ""), case.get("prompt", "")]
            parts.extend(f"{option.get('id')}. {option.get('text')}"
                         for option in (case.get("options") or []) if isinstance(option, dict))
            questions[case["id"]] = "\n".join(str(part) for part in parts if part)
        return questions
    questions: dict[str, str] = {}
    for group in cases:
        original = group.get("original")
        if isinstance(original, dict):
            questions[str(original.get("id", group.get("id")))] = str(original.get("question", ""))
        for variant in group.get("variants", []):
            if isinstance(variant, dict):
                questions[str(variant.get("id"))] = str(variant.get("question", ""))
    return questions


def _appendix_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    report = data["report"]
    version = data["version"]
    questions = _case_questions(data["cases"], version)
    observations = data["observations"]
    lookup = {(str(row.get("id")), str(row.get("sample_id", ""))): row
              for row in observations}
    rows = []
    source_items = report.get("sessions", []) if version == 2 else report["items"]
    for item in source_items:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id", ""))
        sample = str(item.get("sample_id", "")) if version == 3 else ""
        observed = lookup.get((identifier, sample)) or lookup.get((identifier, ""))
        observed = observed or {}
        answer = item.get("raw_answer") if version == 3 else item.get("answer")
        if answer is None:
            answer = observed.get("answer")
        rows.append({"id": identifier, "sample_id": sample, "kind": item.get("kind"),
                     "question": observed.get("question") or questions.get(identifier),
                     "answer": answer, "status": item.get("status"),
                     "score": item.get("score"), "label": item.get("label") or item.get("metric_label"),
                     "reason": item.get("reason") or item.get("error") or item.get("rationale")})
    return rows


def _facts(data: dict[str, Any]) -> dict[str, Any]:
    report = data["report"]
    version = data["version"]
    rows = _appendix_rows(data)
    summary = report["summary"]
    case_kinds = dict(sorted(Counter(str(row.get("kind", "unknown")) for row in data["cases"]).items()))
    statuses = dict(sorted(Counter(str(row.get("status", "unknown")) for row in rows).items()))
    profile = ((data["run"] or {}).get("target") or {}).get("project_profile") or {}
    source = (data["manifest"] or {}).get("source", {})
    score_values = [row["score"] for row in rows if isinstance(row.get("score"), (int, float))]
    if version == 3 and summary.get("scored") != statuses.get("scored", 0):
        raise EvaluationError("evaluation summary disagrees with per-item scored count")
    scored_count = (summary.get("scored", 0) if version == 3
                    else summary.get("eligible_comparisons", 0))
    return {"schema_version": version, "evaluation_summary": summary,
            "scored_count": scored_count, "item_status_counts": statuses, "case_kinds": case_kinds,
            "mean_item_score": sum(score_values) / len(score_values) if score_values else None,
            "source": source, "source_metrics": report.get("source_metrics", []),
            "unsupported_metrics": report.get("unsupported_metrics", []),
            "sealed_unsupported_metrics": report.get("sealed_unsupported_metrics", []),
            "scoring_plan": data["scoring"], "scoring_plan_reference": report.get("scoring_plan"),
            "evaluation_method": report.get("evaluation") if version == 2 else None,
            "generation_protocol": (data["manifest"] or {}).get("generation_protocol"),
            "target_profile": profile, "missing": data["missing"],
            "source_documents": data["source_documents"],
            "case_count": len(data["cases"]), "appendix_count": len(rows),
            "scoring_conflicts": report.get("scoring_conflicts", [])}


def _write_narrative(facts: dict[str, Any], *, model: str, env_file: str | Path,
                     writer: Callable[[dict[str, Any]], Any] | None) -> dict[str, str]:
    if writer is None:
        import akasha
        prompt = """Write a Traditional Chinese evaluation report from the JSON facts below.
Return ONLY a JSON object with five string fields: dataset_introduction,
test_method, target_description, evaluation_method, results_interpretation.
Use only the given evidence. Do not calculate, change or restate numeric values:
the program prints all numbers in fixed tables. Do not discuss evaluator or
adapter model availability unless explicitly present in FACTS_JSON. Do not use any Arabic digits in
your prose. Clearly distinguish target model from evaluator and adapter models.
Mark inferred identities as inferred and cite profile evidence paths. In
target_description discuss only the target; evaluator and adapter details belong
in the fixed metadata table. If facts are missing, say 未記錄 or 無法確認. If
scored is zero, do not assert performance.
Do not call an unsupported source metric measured or comparable to its study.
No Markdown headings or tables; only paragraph text in each field.
FACTS_JSON:
""" + json.dumps(facts, ensure_ascii=False, default=str)
        agent = akasha.agents(model=model, env_file=str(env_file), tools=[],
                              stream=False, thinking=False, verbose=False,
                              keep_logs=False, max_round=1,
                              max_input_tokens=24000, max_output_tokens=4096)
        response = agent(prompt)
    else:
        response = writer(facts)
    if isinstance(response, dict):
        value = response
    else:
        raw = str(response).strip()
        fence = chr(96) * 3
        if raw.startswith(fence):
            raw = raw.split("\n", 1)[1].rsplit(fence, 1)[0].strip()
        decoder = json.JSONDecoder()
        value = None
        for index, char in enumerate(raw):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(raw[index:])
            except ValueError:
                continue
            if isinstance(candidate, dict) and "dataset_introduction" in candidate:
                value = candidate
                break
    if not isinstance(value, dict):
        raise EvaluationError("report agent returned no JSON object")
    sections: dict[str, str] = {}
    for key in _SECTIONS:
        text = value.get(key)
        if not isinstance(text, str) or not text.strip():
            raise EvaluationError(f"report agent omitted {key}")
        if re.search(r"\d", text):
            raise EvaluationError(f"report agent used an unverified number in {key}")
        sections[key] = text.strip()
    if re.search(r"評測端|評審模型|適配器模型|評分模型|evaluator|adapter",
                 sections["target_description"], re.I):
        raise EvaluationError("report agent mixed evaluator/adapter identity with target identity")
    if facts["scored_count"] == 0:
        if re.search(r"全部正確|表現優異|all correct|excellent", sections["results_interpretation"], re.I):
            raise EvaluationError("report agent asserted performance without scored cases")
    return sections


def _render(data: dict[str, Any], facts: dict[str, Any], narrative: dict[str, str]) -> str:
    report = data["report"]
    lines = ["# LLaDAR 評估報告", "",
             f"- 評分檔：{_cell(data['report_path'])}",
             f"- Schema 版本：{data['version']}", ""]
    for key in _SECTIONS:
        lines.extend([f"## {_TITLES[key]}", "", narrative[key], ""])
        if key == "dataset_introduction":
            lines.extend([f"- 資料集：{_cell(data['dataset_path'])}",
                          f"- 來源：{_cell(_source_name(facts['source']))}",
                          f"- 來源版本：{_cell(facts['source'].get('commit') if isinstance(facts['source'], dict) else None)}",
                          f"- 封存來源文件：{_cell(', '.join(doc['path'] for doc in facts['source_documents']) or None)}",
                          f"- 可取得案例：{facts['case_count']}", "",
                          "| 題型 | 題數 |", "| --- | ---: |"])
            lines += [f"| {_cell(kind)} | {count} |" for kind, count in facts["case_kinds"].items()]
            lines.append("")
            source_files = facts["source"].get("files", {}) if isinstance(facts["source"], dict) else {}
            if isinstance(source_files, dict) and source_files:
                selected = [(name, digest) for name, digest in source_files.items()
                            if name.startswith("data/") or name.startswith("generate_from_template")]
                lines.extend(["### 來源資料檔案與產題程式", "", "| 檔案 | SHA-256 |",
                              "| --- | --- |"])
                for name, digest in selected[:30]:
                    lines.append(f"| {_cell(name)} | {_cell(digest)} |")
                if len(selected) > 30:
                    lines.append(f"| 其餘檔案 | {len(selected) - 30} 筆，詳見 manifest |")
                lines.append("")
        if key == "target_description":
            profile = facts["target_profile"]
            lines.extend([f"- 專案：{_cell(profile.get('project_name'))}",
                          f"- Agent：{_cell(profile.get('agent_name'))}",
                          f"- 模型：{_cell(profile.get('model_name'))}",
                          f"- 身分判定：{_cell({'confirmed': '已確認', 'inferred': '推斷', 'unknown': '無法確認'}.get(profile.get('identity_status'), profile.get('identity_status')))}",
                          f"- 候選：{_cell(', '.join(profile.get('candidates', [])) or None)}",
                          f"- 來源檔案：{_cell(', '.join(profile.get('evidence', [])) or None)}",
                          f"- 概述狀態：{_cell(profile.get('status'))}；{_cell(profile.get('reason'))}", ""])
        if key == "evaluation_method":
            if data["version"] == 2:
                method = report.get("evaluation", {})
                lines.extend([f"- 評分協定：{_cell(method.get('protocol'))}",
                              f"- 評審模型：{_cell(method.get('model'))}", ""])
            else:
                reference = report.get("scoring_plan", {})
                lines.extend([f"- 評分方案：{_cell(reference.get('source'))}",
                              f"- 評分方案 SHA-256：{_cell(reference.get('sha256'))}",
                              f"- 評分方式探索模型：{_cell(data['audit_model'])}", ""])
        if key == "results_interpretation":
            lines.extend(["| 統計項目 | 數值 |", "| --- | ---: |"])
            for name, value in sorted(report["summary"].items()):
                shown = (f"{value:.4%}" if isinstance(value, float)
                         and ("coverage" in name or name.endswith("_rate"))
                         else _number(value))
                lines.append(f"| {_cell(name)} | {_cell(shown)} |")
            lines.append(f"| recomputed_mean_item_score | {_cell(_number(facts['mean_item_score']))} |")
            lines.extend(["", "| 逐題狀態 | 題數 |", "| --- | ---: |"])
            for name, count in facts["item_status_counts"].items():
                lines.append(f"| {_cell(name)} | {count} |")
            lines.append("")
            if data["version"] == 3:
                lines.extend(["### 來源指標", "", "| 名稱 | 分組 | 可評 | 分母 | 值／標籤 | 可比性 | 證據 |",
                              "| --- | --- | ---: | ---: | --- | --- | --- |"])
                for metric in facts["source_metrics"]:
                    value = metric.get("value", metric.get("label_proportions"))
                    lines.append(f"| {_cell(metric.get('name'))} | {_cell(metric.get('group'))} | "
                                 f"{_cell(metric.get('eligible'))} | {_cell(metric.get('denominator'))} | "
                                 f"{_cell(value)} | {_cell(metric.get('study_comparability'))} | "
                                 f"{_cell(', '.join(metric.get('evidence', [])))} |")
                if not facts["source_metrics"]:
                    lines.append("| 未產生 | — | — | — | — | — | — |")
                lines.append("")
                if facts["unsupported_metrics"]:
                    lines.extend(["### 不支援的來源指標", ""])
                    for metric in facts["unsupported_metrics"]:
                        lines.append(f"- {_cell(metric.get('name', metric.get('id')))}："
                                     f"{_cell(metric.get('reason'))}；證據："
                                     f"{_cell(', '.join(metric.get('evidence', [])))}")
                    lines.append("")
                if facts["sealed_unsupported_metrics"]:
                    lines.extend(["### 匯入時封存的不支援指標", ""])
                    for metric in facts["sealed_unsupported_metrics"]:
                        lines.append(f"- {_cell(metric.get('name', metric.get('id')))}："
                                     f"{_cell(metric.get('reason'))}")
                    lines.append("")
                if facts["scoring_conflicts"]:
                    lines.extend(["### 評分規則衝突", "",
                                  f"- 衝突數：{len(facts['scoring_conflicts'])}", ""])
                    for conflict in facts["scoring_conflicts"]:
                        lines.append(f"- {_cell(conflict)}")
                    lines.append("")
            if data["missing"]:
                lines.extend(["### 缺漏與限制", ""])
                lines += [f"- {item}" for item in data["missing"]]
                lines.append("")
            if facts["scored_count"] == 0:
                lines.extend(["**本次沒有可解讀的評分結果。**", ""])
    lines.extend(["## 附錄：逐題測試與回答", "",
                  f"共 {facts['appendix_count']} 筆；由程式逐題寫入，未交由撰寫 Agent 計算。", ""])
    for index, row in enumerate(_appendix_rows(data), 1):
        lines.extend([f"### {index}. {_cell(row['id'])}" +
                      (f"／{_cell(row['sample_id'])}" if row["sample_id"] else ""),
                      "",
                      f"- 題型：{_cell(row['kind'])}",
                      f"- 狀態：{_cell(row['status'])}",
                      f"- 分數：{_cell(row['score'])}",
                      f"- 標籤：{_cell(row['label'])}",
                      f"- 原因：{_cell(row['reason'])}", "",
                      "題目：", "", _fence(row["question"]), "",
                      "待測對象回答：", "", _fence(row["answer"]), ""])
    return "\n".join(lines) + "\n"


def create_report(eval_input: str | Path, output: str | Path, *,
                  model: str = DEFAULT_REPORT_MODEL, env_file: str | Path = ".env",
                  force: bool = False,
                  writer: Callable[[dict[str, Any]], Any] | None = None) -> Path:
    """Build a full Markdown report and compact provenance sidecar."""
    target = Path(output)
    sidecar = target.with_name(target.name + ".meta.json")
    if (target.exists() or sidecar.exists()) and not force:
        raise FileExistsError(f"report output already exists: {target}")
    data = _read_inputs(eval_input)
    facts = _facts(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        narrative = _write_narrative(facts, model=model, env_file=env_file, writer=writer)
        markdown = _render(data, facts, narrative)
    except Exception as error:
        failure = target.with_name(target.name + ".failed.json")
        failure.write_text(json.dumps({
            "error": f"{type(error).__name__}: {error}",
            "evaluation_report": str(data["report_path"]),
            "evaluation_sha256": _digest(data["report_path"]),
            "verified_facts": facts,
        }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        raise EvaluationError(f"report_generation_error: {error}; evidence={failure}") from error
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(target)
    sidecar.write_text(json.dumps({
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_report": str(data["report_path"]),
        "evaluation_sha256": _digest(data["report_path"]),
        "answers_sha256": _digest(data["answers_path"]) if data["answers_path"] else None,
        "dataset_manifest_sha256": _digest(data["manifest_path"]) if data["manifest_path"] and data["manifest_path"].is_file() else None,
        "run_sha256": _digest(data["run_path"]) if data["run"] and data["run_path"] else None,
        "report_model": model, "report_prompt_version": 1,
        "agent_input_sha256": hashlib.sha256(json.dumps(
            facts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        "agent_input": facts, "agent_output": narrative,
        "render_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "missing": data["missing"],
        "source_documents": [{"path": doc["path"], "sha256": doc["sha256"]}
                             for doc in data["source_documents"]],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
