import hashlib
import json
from pathlib import Path

from lladar.cli import main


def test_eval_uses_sealed_source_rule_for_choice_case(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {
        "schema_version": 3, "id": "q1", "status": "ready", "kind": "single_choice",
        "context": "At the station", "prompt": "Which exit?",
        "options": [{"id": "0", "text": "North"}, {"id": "1", "text": "South"}],
        "answer": "1", "rule_id": "source_exact", "source": {"path": "questions.jsonl", "line": 1},
        "metadata": {},
    }
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "source_exact", "method": "exact_option", "evidence": ["questions.jsonl"]}
    ]}), encoding="utf-8")
    manifest = {"schema_version": 3, "source": {"type": "local", "path": "source"},
                "counts": {"source": 1, "ready": 1, "unsupported": 0},
                "artifacts": {
                    name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))
                }}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "sample_id": "q1:1",
                                   "status": "ok", "answer": "1"}) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == 3
    assert report["items"][0]["score"] == 1.0
    assert report["items"][0]["status"] == "scored"
    assert report["summary"]["scored"] == 1
    assert report["summary"]["scheduled_comparisons"] == 1
    assert report["source_metrics"][0]["name"] == "accuracy"
    assert report["source_metrics"][0]["denominator"] == 1
    assert report["source_metrics"][0]["value"] == 1.0

def test_eval_scores_multiple_choice_as_an_unordered_set(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "m1", "status": "ready", "kind": "multiple_choice",
            "prompt": "Choose two", "options": [
                {"id": "0", "text": "Red"}, {"id": "1", "text": "Blue"},
                {"id": "2", "text": "Green"}], "answer": ["0", "2"],
            "rule_id": "set", "source": {"path": "items.jsonl", "line": 1}, "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "set", "method": "exact_option_set", "evidence": ["items.jsonl"]}
    ]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local"}, "counts": {"source": 1, "ready": 1},
        "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "m1", "status": "ok",
                                   "answer": "2, 0"}) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(output)]) == 0
    item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "scored"
    assert item["score"] == 1.0

def test_eval_from_requires_a_matching_completed_run(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    exit_code = main(["eval", "--from", "https://github.com/example/benchmark"])
    assert exit_code == 2
    assert "run_not_found" in capsys.readouterr().err
    assert not list(tmp_path.glob("lladar-eval-*"))

def test_eval_from_uses_akasha_to_rediscover_pinned_scoring(tmp_path: Path, monkeypatch):
    import sys
    import types

    bundle = tmp_path / "bundle"
    source_snapshot = bundle / "evidence" / "source"
    source_snapshot.mkdir(parents=True)
    (source_snapshot / "items.jsonl").write_text(json.dumps({
        "id": "q1", "prompt": "Choose", "options": ["A", "B"], "gold": 1,
    }) + "\n", encoding="utf-8")
    (source_snapshot / "paper.txt").write_text("A source method gives accuracy.",
                                                encoding="utf-8")
    case = {"schema_version": 3, "id": "q1", "status": "ready", "kind": "single_choice",
            "prompt": "Choose", "options": [{"id": "0", "text": "A"},
                                              {"id": "1", "text": "B"}],
            "answer": "1", "rule_id": "exact", "source": {"path": "items.jsonl", "line": 1},
            "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    rules = [{"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}]
    scoring.write_text(json.dumps({"schema_version": 3, "rules": rules}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    source_path = str(tmp_path / "original-source")
    manifest = {"schema_version": 3, "source": {"type": "local", "path": source_path,
        "files": {name: hashlib.sha256((source_snapshot / name).read_bytes()).hexdigest()
                  for name in ("items.jsonl", "paper.txt")}},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok",
                                   "answer": "1"}) + "\n", encoding="utf-8")
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"schema_version": 3, "dataset": str(bundle),
        "dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "source": manifest["source"], "answers": str(answers),
        "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest()}), encoding="utf-8")
    calls = []
    attempts = []

    def agents(**kwargs):
        calls.append(kwargs)
        def rediscover(prompt):
            attempts.append(prompt)
            assert "token_balance" in prompt
            assert ".lladar-papers" in prompt
            assert "positive_label" in prompt
            assert "**/*.txt" in prompt
            assert "items.jsonl" in kwargs["tools"][0]()
            assert "gold" in kwargs["tools"][1]("items.jsonl")
            assert "source method" in kwargs["tools"][4]("source method")[0]["snippet"]
            assert "source method" in kwargs["tools"][2]("source method", pattern="**/*.txt")[0]["snippet"]
            if len(attempts) == 1:
                return json.dumps({"rules": [{"id": "exact", "method": "exact_option",
                                               "evidence": ["items.jsonl"],
                                               "parameters": {"partial_credit": 0.5}}]})
            assert "parameters" in prompt
            return json.dumps({"rules": [{"id": "source_accuracy",
                "method": "exact_option", "evidence": ["items.jsonl"],
                "parameters": {}}]})
        return rediscover

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    output = tmp_path / "evaluation"
    assert main(["eval", "--from", source_path, "--run", str(run),
                 "--output", str(output)]) == 0
    assert calls
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["scored"] == 1
    assert report["scoring_plan"]["source"] == "rediscovered"
    assert (output / "scoring-plan.json").is_file()
    assert (output / "evidence" / "agent-audit.json").is_file()

def test_eval_rejects_unknown_benchmark_case_field(tmp_path: Path, capsys):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "q", "status": "ready",
            "kind": "single_choice", "prompt": "Choose", "context": "",
            "options": [{"id": "0", "text": "A"}, {"id": "1", "text": "B"}],
            "answer": "0", "rule_id": "exact", "source": {"path": "items.jsonl", "line": 1},
            "metadata": {}, "unrecognized": True}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    plan = bundle / "scoring-plan.json"
    plan.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", plan))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q", "status": "ok",
                                   "answer": "0"}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 2
    assert "unrecognized" in capsys.readouterr().err
    assert not report.exists()
def test_conflicted_missing_rediscovered_rule_is_reported_unsupported(tmp_path: Path):
    from lladar.benchmark_eval import evaluate_benchmark

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "q1", "status": "ready",
            "kind": "single_choice", "prompt": "Choose",
            "options": [{"id": "0", "text": "A"}, {"id": "1", "text": "B"}],
            "answer": "1", "rule_id": "sealed", "source": {"path": "items.jsonl", "line": 1},
            "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    sealed = bundle / "scoring-plan.json"
    sealed.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "sealed", "method": "exact_option", "evidence": ["items.jsonl"]}
    ]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", sealed))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    rediscovered = tmp_path / "rediscovered.json"
    rediscovered.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "different", "method": "exact_text", "evidence": ["items.jsonl"]}
    ]}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok", "answer": "1"}) + "\n",
                       encoding="utf-8")
    report = evaluate_benchmark(bundle, answers, output=tmp_path / "report.json",
                                scoring_plan_override=rediscovered,
                                conflicted_rules={"sealed", "different"})
    assert report["items"][0]["status"] == "unsupported"
    assert report["items"][0]["reason"] == "scoring_rule_conflict"
def test_token_balance_counts_source_apostrophe_token():
    from lladar.benchmark_eval import _token_balance

    label, counts = _token_balance("She\u2019s a doctor.", {
        "positive_tokens": ["she's"], "negative_tokens": ["he's"],
        "positive_label": "female", "negative_label": "male",
        "neutral_label": "neutral",
    })
    assert label == "female"
    assert counts == {"positive": 1, "negative": 0}
def test_eval_from_keeps_diagnostics_when_agent_cannot_find_rules(tmp_path: Path, monkeypatch, capsys):
    import sys
    import types

    bundle = tmp_path / "bundle"
    snapshot = bundle / "evidence" / "source"
    snapshot.mkdir(parents=True)
    source_file = snapshot / "items.jsonl"
    source_file.write_text(json.dumps({"id": "q1"}) + "\n", encoding="utf-8")
    case = {"schema_version": 3, "id": "q1", "status": "ready",
            "kind": "single_choice", "prompt": "Choose",
            "options": [{"id": "0", "text": "A"}, {"id": "1", "text": "B"}],
            "answer": "1", "rule_id": "exact", "source": {"path": "items.jsonl", "line": 1},
            "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [
        {"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}
    ]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    source = str(tmp_path / "original-source")
    manifest = {"schema_version": 3, "source": {"type": "local", "path": source,
        "files": {"items.jsonl": hashlib.sha256(source_file.read_bytes()).hexdigest()}},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok", "answer": "1"}) + "\n",
                       encoding="utf-8")
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"dataset": str(bundle), "source": manifest["source"],
        "dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "answers": str(answers), "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest()}),
        encoding="utf-8")
    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function,
        agents=lambda **kwargs: lambda prompt: '{"rules": []}',
    ))
    output = tmp_path / "evaluation"
    assert main(["eval", "--from", source, "--run", str(run),
                 "--output", str(output)]) == 2
    assert "agent_inconclusive" in capsys.readouterr().err
    assert not output.exists()
    failures = list((tmp_path / ".lladar-eval-failures").glob("*/diagnostic.json"))
    assert len(failures) == 1
    assert (failures[0].parent / "agent-response.txt").read_text(encoding="utf-8") == '{"rules": []}'
def test_rediscovered_rule_alignment_prefers_matching_id_when_method_repeats():
    from lladar.benchmark_rediscovery import _align_rules

    sealed = [{"id": "a", "method": "exact_option", "evidence": ["source.txt"]}]
    discovered = [
        {"id": "b", "method": "exact_option", "evidence": ["source.txt"]},
        {"id": "a", "method": "exact_option", "evidence": ["source.txt"]},
    ]
    aligned, conflicts, mapping = _align_rules(sealed, discovered)
    assert mapping == {"a": "a"}
    assert aligned[0]["id"] == "a"
    assert "a" not in conflicts
def test_eval_uses_source_rubric_with_akasha_for_free_answer(tmp_path: Path, monkeypatch):
    import sys
    import types

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    case = {"schema_version": 3, "id": "essay-1", "status": "ready",
            "kind": "free_answer", "prompt": "Explain the result", "context": "",
            "options": [], "answer": None, "rule_id": "rubric",
            "source": {"path": "questions.jsonl", "line": 1}, "metadata": {}}
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "rules": [{
        "id": "rubric", "method": "rubric_judge", "evidence": ["RUBRIC.md"],
        "parameters": {"rubric": "Award full credit only if the answer names both causes."},
    }]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases), ("scoring-plan.json", scoring))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "essay-1",
                                   "status": "ok", "answer": "Cause one and cause two."}) + "\n",
                       encoding="utf-8")
    calls = []

    def agents(**kwargs):
        calls.append(kwargs)
        def judge(prompt):
            assert "Award full credit only if" in prompt
            assert "Cause one and cause two" in prompt
            return json.dumps({"score": 1.0, "reason": "Both causes are present."})
        return judge

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(agents=agents))
    output = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--model", "gemini:judge-test",
                 "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    item = report["items"][0]
    assert item["status"] == "scored"
    assert item["score"] == 1.0
    assert item["judge_reason"] == "Both causes are present."
    assert item["judge_model"] == "gemini:judge-test"
    assert calls[0]["model"] == "gemini:judge-test"
def test_scoring_search_returns_method_lines_beyond_paper_abstract(tmp_path: Path):
    from lladar.adapter_workspace import WorkspaceExplorer

    paper = tmp_path / "paper.txt"
    paper.write_text("\n".join(["abstract"] * 300 + [
        "4.5 Gender polarity", "Unigram matching counts male and female words.",
        "Male tokens include he and him; female tokens include she and her.",
    ]), encoding="utf-8")
    explorer = WorkspaceExplorer(tmp_path)
    matches = explorer.search_context("unigram matching", pattern="**/*.txt")
    assert matches[0]["line"] == 302
    assert "Male tokens include he and him" in matches[0]["snippet"]
def test_token_balance_normalizes_corrupted_source_apostrophes():
    from lladar.benchmark_eval import _token_balance

    label, counts = _token_balance("She's a doctor.", {
        "positive_tokens": ["she\ufffd\ufffds"],
        "negative_tokens": ["he\ufffd\ufffds"],
        "positive_label": "female", "negative_label": "male",
        "neutral_label": "neutral",
    })
    assert label == "female"
    assert counts == {"positive": 1, "negative": 0}


def test_rediscovered_token_rule_aligns_reversed_axes_and_apostrophe_variants():
    from lladar.benchmark_rediscovery import _align_rules

    sealed = [{"id": "first", "method": "token_balance", "evidence": ["paper.txt"],
               "parameters": {"positive_tokens": ["she", "she's", "she\ufffd\ufffds"],
                              "negative_tokens": ["he", "he's"],
                              "positive_label": "female", "negative_label": "male",
                              "neutral_label": "neutral"}}]
    discovered = [{"id": "second", "method": "token_balance", "evidence": ["paper.txt"],
                   "parameters": {"positive_tokens": ["he", "he\ufffd\ufffds"],
                                  "negative_tokens": ["she", "she\ufffd\ufffds"],
                                  "positive_label": "male", "negative_label": "female",
                                  "neutral_label": "neutral"}}]
    aligned, conflicts, mapping = _align_rules(sealed, discovered)
    assert mapping == {"first": "second"}
    assert not conflicts
    assert aligned[0]["id"] == "first"
def test_rediscovered_rules_unrelated_to_ready_case_kinds_are_unsupported_metrics():
    from lladar.benchmark_rediscovery import _partition_relevant_rules

    rules = [
        {"id": "generation_metric", "method": "token_balance", "evidence": ["paper.txt"]},
        {"id": "choice_metric", "method": "exact_option", "evidence": ["paper.txt"]},
    ]
    relevant, unsupported = _partition_relevant_rules(rules, {"generation"})
    assert [rule["id"] for rule in relevant] == ["generation_metric"]
    assert unsupported == [{
        "id": "choice_metric", "method": "exact_option",
        "reason": "no_applicable_ready_cases", "evidence": ["paper.txt"],
    }]

def test_sealed_eval_rejects_changed_pinned_source(tmp_path: Path, capsys):
    import json
    import hashlib

    from lladar.cli import main

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
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok", "answer": "1"}) + "\n",
                       encoding="utf-8")
    source.write_text('{"id":"different"}\n', encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 2
    assert "source_read_error" in capsys.readouterr().err
    assert not report.exists()

def test_eval_replays_sealed_converter_against_source(tmp_path: Path, capsys):
    from lladar.cli import main

    bundle = tmp_path / "bundle"
    snapshot = bundle / "evidence" / "source"
    scorers = bundle / "scorers"
    snapshot.mkdir(parents=True)
    scorers.mkdir()
    source = snapshot / "items.jsonl"
    source.write_text(json.dumps({"id": "q1", "prompt": "Choose",
                                  "options": ["A", "B"], "gold": 1}) + "\n", encoding="utf-8")
    converter = scorers / "converter.json"
    converter.write_text(json.dumps({"data_files": [{
        "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
        "fields": {"id": "id", "prompt": "prompt", "options": "options", "answer": "gold"},
        "rule_id": "exact",
    }], "rules": [{"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}]}),
        encoding="utf-8")
    from lladar.benchmark_import import _convert
    case = _convert(snapshot, json.loads(converter.read_text(encoding="utf-8")))[0]
    case["answer"] = "0"  # digest-valid case artifact disagrees with the saved converter
    cases = bundle / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n", encoding="utf-8")
    scoring = bundle / "scoring-plan.json"
    scoring.write_text(json.dumps({"schema_version": 3, "converter": "scorers/converter.json",
                              "rules": [{"id": "exact", "method": "exact_option",
                                         "evidence": ["items.jsonl"]}]}), encoding="utf-8")
    artifacts = {name: {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for name, path in (("cases.jsonl", cases),
                                    ("scoring-plan.json", scoring),
                                    ("scorers/converter.json", converter))}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 3,
        "source": {"type": "local", "path": "original", "files": {
            "items.jsonl": hashlib.sha256(source.read_bytes()).hexdigest()}},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok", "answer": "0"}) + "\n",
                       encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 2
    assert "converter replay" in capsys.readouterr().err
    assert not report.exists()

def test_pinned_public_paper_text_must_match_manifest(tmp_path: Path):
    from lladar.benchmark_eval import _verify_pinned_source
    from lladar.exceptions import EvaluationError
    import pytest

    snapshot = tmp_path / "evidence" / "source" / ".lladar-papers"
    snapshot.mkdir(parents=True)
    paper = snapshot / "1234.5678.txt"
    paper.write_text("Original published method", encoding="utf-8")
    manifest = {"source": {"files": {}}, "supplemental_documents": [{
        "url": "https://arxiv.org/pdf/1234.5678",
        "path": ".lladar-papers/1234.5678.txt",
        "sha256": hashlib.sha256(paper.read_bytes()).hexdigest(),
    }]}
    _verify_pinned_source(tmp_path, manifest)
    paper.write_text("Changed method", encoding="utf-8")
    with pytest.raises(EvaluationError, match="source_read_error"):
        _verify_pinned_source(tmp_path, manifest)

def test_benchmark_summary_separates_import_and_execution_outcomes():
    from lladar.benchmark_eval import _benchmark_summary

    cases = [
        {"id": "ok", "status": "ready"},
        {"id": "failed", "status": "ready"},
        {"id": "missing", "status": "ready"},
        {"id": "unsupported", "status": "unsupported"},
        {"id": "bad_row", "status": "source_invalid"},
    ]
    items = [
        {"id": "ok", "status": "scored", "score": 1.0},
        {"id": "failed", "status": "execution_error"},
        {"id": "missing", "status": "missing_answer"},
        {"id": "unsupported", "status": "unsupported"},
        {"id": "bad_row", "status": "unsupported"},
    ]
    summary = _benchmark_summary(cases, items, source_count=5)
    assert summary["source"] == 5
    assert summary["ready"] == 3
    assert summary["unsupported"] == 1
    assert summary["source_invalid"] == 1
    assert summary["executed"] == 2
    assert summary["execution_error"] == 1
    assert summary["missing_answer"] == 1
    assert summary["scored"] == 1
    assert summary["ready_coverage"] == 1 / 3

def test_eval_rejects_answer_ids_absent_from_dataset(tmp_path: Path, capsys):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
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
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "wrong", "status": "ok", "answer": "1"}) + "\n",
                       encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(output)]) == 2
    assert "alignment_error" in capsys.readouterr().err
    assert not output.exists()

def test_rediscovery_can_report_unrelated_unsupported_scoring_method(tmp_path: Path):
    from lladar.benchmark_rediscovery import _validate_rules, _partition_relevant_rules

    (tmp_path / "paper.txt").write_text("Classifier score", encoding="utf-8")
    rules = [{"id": "classification", "method": "exact_option",
              "evidence": ["paper.txt"], "parameters": {"threshold": 0.8}}]
    _validate_rules(rules, tmp_path, ready_kinds={"generation"})
    relevant, unsupported = _partition_relevant_rules(rules, {"generation"})
    assert relevant == []
    assert unsupported[0]["id"] == "classification"

def test_eval_rejects_unknown_answer_schema_field(tmp_path: Path, capsys):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
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
        "source": {"type": "local", "path": "source"},
        "counts": {"source": 1, "ready": 1}, "artifacts": artifacts}), encoding="utf-8")
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1",
                                   "status": "ok", "answer": "1", "fabricated": True}) + "\n",
                       encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 2
    assert "BenchmarkAnswer" in capsys.readouterr().err
    assert not report.exists()
