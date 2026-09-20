from pathlib import Path

from lladar.cli import main


def test_missing_local_source_reports_read_error_without_publishing_dataset(tmp_path: Path, capsys):
    missing_source = tmp_path / "absent-benchmark"
    output = tmp_path / "imported-dataset"

    exit_code = main(
        ["create", "dataset", "--convert-from", str(missing_source), "--output", str(output)]
    )

    assert exit_code == 2
    assert not output.exists()
    assert "source_read_error" in capsys.readouterr().err


def test_local_benchmark_is_discovered_by_akasha_and_published_as_bundle(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "questions.jsonl").write_text(
        json.dumps({"key": "q1", "situation": "At the station", "ask": "Which exit?",
                    "choices": ["North", "South"], "gold": 1}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "bundle"
    observations = []

    def create_tool(description, function, name):
        return function

    def agents(**kwargs):
        observations.append(kwargs)

        def run(prompt):
            files = kwargs["tools"][0]()
            assert "questions.jsonl" in files
            assert "Which exit?" in kwargs["tools"][1]("questions.jsonl")
            return json.dumps({
                "data_files": [{
                    "path": "questions.jsonl", "format": "jsonl", "kind": "single_choice",
                    "fields": {"id": "key", "context": "situation", "prompt": "ask",
                               "options": "choices", "answer": "gold"},
                    "answer_index_base": 0,
                    "rule_id": "source_exact",
                }],
                "rules": [{"id": "source_exact", "method": "exact_option",
                           "evidence": ["questions.jsonl"]}],
            })

        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=create_tool, agents=agents,
    ))
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(output),
                 "--model", "gemini:test-model", "--env-file", "custom.env"]) == 0
    assert observations
    assert observations[0]["max_output_tokens"] >= 8192
    assert observations[0]["model"] == "gemini:test-model"
    assert observations[0]["env_file"] == "custom.env"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in (output / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest["schema_version"] == 3
    assert manifest["counts"]["ready"] == 1
    assert cases[0]["id"] == "q1"
    assert cases[0]["prompt"] == "Which exit?"
    assert cases[0]["answer"] == "1"
    assert (output / "scoring-plan.json").is_file()

def test_agent_plan_imports_multiple_choice_answers(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "key": "multi-1", "question": "Pick both prime numbers",
        "choices": ["2", "4", "5"], "correct": [0, 2],
    }) + "\n", encoding="utf-8")
    output = tmp_path / "bundle"

    def agents(**kwargs):
        def run(prompt):
            assert "items.jsonl" in kwargs["tools"][0]()
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "multiple_choice",
                "fields": {"id": "key", "prompt": "question", "options": "choices",
                           "answer": "correct"}, "rule_id": "set_exact",
            }], "rules": [{"id": "set_exact", "method": "exact_option_set",
                           "evidence": ["items.jsonl"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(output)]) == 0
    case = json.loads((output / "cases.jsonl").read_text(encoding="utf-8"))
    assert case["kind"] == "multiple_choice"
    assert case["answer"] == ["0", "2"]
    assert [option["text"] for option in case["options"]] == ["2", "4", "5"]

def test_public_github_source_is_pinned_before_akasha_exploration(tmp_path: Path, monkeypatch):
    import json
    import subprocess
    import sys
    import types

    commands = []
    commit = "a" * 40

    def git(command, **kwargs):
        commands.append(command)
        if "clone" in command:
            destination = Path(command[-1])
            destination.mkdir()
            (destination / "items.jsonl").write_text(json.dumps({
                "id": "g1", "prompt": "Choose", "options": ["A", "B"], "gold": 0,
            }) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, commit + "\n", "")

    def agents(**kwargs):
        def run(prompt):
            assert "items.jsonl" in kwargs["tools"][0]()
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "prompt", "options": "options",
                           "answer": "gold"}, "rule_id": "exact",
            }], "rules": [{"id": "exact", "method": "exact_option",
                           "evidence": ["items.jsonl"]}]})
        return run

    monkeypatch.setattr(subprocess, "run", git)
    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    output = tmp_path / "bundle"
    url = "https://github.com/example/benchmark"
    assert main(["create", "dataset", "--convert-from", url, "--output", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["type"] == "github"
    assert manifest["source"]["url"] == url
    assert manifest["source"]["commit"] == commit
    assert any("clone" in command for command in commands)

def test_ranking_source_keeps_order_through_import_and_eval(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "rank.jsonl").write_text(json.dumps({
        "key": "rank-1", "ask": "Order the steps", "steps": ["start", "middle", "end"],
        "order": [0, 1, 2],
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": "rank.jsonl", "format": "jsonl", "kind": "ranking",
            "fields": {"id": "key", "prompt": "ask", "options": "steps", "answer": "order"},
            "rule_id": "order_exact",
        }], "rules": [{"id": "order_exact", "method": "exact_order",
                       "evidence": ["rank.jsonl"]}]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    case = json.loads((bundle / "cases.jsonl").read_text(encoding="utf-8"))
    assert case["answer"] == ["0", "1", "2"]
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "rank-1", "status": "ok",
                                   "answer": "1,0,2"}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 0
    result = json.loads(report.read_text(encoding="utf-8"))["items"][0]
    assert result["status"] == "scored"
    assert result["score"] == 0.0

def test_free_answer_uses_source_text_rule_through_import_and_eval(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "answers.jsonl").write_text(json.dumps({
        "uid": "free-1", "query": "Name the capital", "reference": "Paris",
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": "answers.jsonl", "format": "jsonl", "kind": "free_answer",
            "fields": {"id": "uid", "prompt": "query", "answer": "reference"},
            "rule_id": "text_exact",
        }], "rules": [{"id": "text_exact", "method": "exact_text",
                       "evidence": ["answers.jsonl"]}]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    case = json.loads((bundle / "cases.jsonl").read_text(encoding="utf-8"))
    assert case["answer"] == "Paris"
    answers = tmp_path / "observed.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "free-1", "status": "ok",
                                   "answer": "Paris"}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(report)]) == 0
    result = json.loads(report.read_text(encoding="utf-8"))["items"][0]
    assert result["status"] == "scored"
    assert result["score"] == 1.0

def test_bad_source_row_is_recorded_without_losing_ready_cases(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    rows = [
        {"uid": "valid", "ask": "Choose", "options": ["A", "B"], "gold": 1},
        {"uid": "broken", "ask": "Choose", "options": ["A", "B"], "gold": 9},
    ]
    (source / "mixed.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": "mixed.jsonl", "format": "jsonl", "kind": "single_choice",
            "fields": {"id": "uid", "prompt": "ask", "options": "options", "answer": "gold"},
            "rule_id": "exact",
        }], "rules": [{"id": "exact", "method": "exact_option",
                       "evidence": ["mixed.jsonl"]}]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    output = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(output)]) == 0
    records = [json.loads(line) for line in (output / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in records] == ["ready", "source_invalid"]
    assert records[1]["source"] == {"path": "mixed.jsonl", "line": 2}
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["source"] == 2
    assert manifest["counts"]["ready"] == 1
    assert manifest["counts"]["source_invalid"] == 1
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "valid",
                                   "status": "ok", "answer": "1"}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(output), str(answers), "--output", str(report)]) == 0
    result = json.loads(report.read_text(encoding="utf-8"))
    assert [item["status"] for item in result["items"]] == ["scored", "source_invalid"]
    assert result["summary"]["source_invalid"] == 1

def test_inconclusive_agent_keeps_failure_evidence_without_bundle(tmp_path: Path, monkeypatch, capsys):
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("Unknown scoring rules", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function,
        agents=lambda **kwargs: lambda prompt: "I could not identify the rule.",
    ))
    output = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(output)]) == 2
    assert "agent_inconclusive" in capsys.readouterr().err
    assert not output.exists()
    failures = list((tmp_path / ".lladar-import-failures").glob("*/agent-response.txt"))
    assert len(failures) == 1
    assert "could not identify" in failures[0].read_text(encoding="utf-8")

def test_choice_options_can_be_mapped_from_separate_source_fields(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "id": "c1", "question": "Choose", "first": "A", "second": "B",
        "third": "C", "label": 2,
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
            "fields": {"id": "id", "prompt": "question",
                       "options": ["first", "second", "third"], "answer": "label"},
            "rule_id": "exact",
        }], "rules": [{"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    case = json.loads((bundle / "cases.jsonl").read_text(encoding="utf-8"))
    assert [choice["text"] for choice in case["options"]] == ["A", "B", "C"]
    assert case["answer"] == "2"

def test_agent_can_scope_repeated_source_ids_by_file(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    for name in ("north", "south"):
        (source / f"{name}.jsonl").write_text(json.dumps({
            "id": "1", "prompt": f"Question in {name}", "options": ["A", "B"],
            "answer": 0,
        }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": f"{name}.jsonl", "format": "jsonl", "kind": "single_choice",
            "id_prefix": "path_stem", "fields": {"id": "id", "prompt": "prompt",
                "options": "options", "answer": "answer"}, "rule_id": "exact",
        } for name in ("north", "south")], "rules": [{"id": "exact",
            "method": "exact_option", "evidence": ["north.jsonl", "south.jsonl"]}]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    cases = [json.loads(line) for line in (bundle / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [case["id"] for case in cases] == ["north:1", "south:1"]

def test_agent_can_preview_a_large_single_line_source_file(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "large.json").write_text('{"entries":[' + '"text",' * 40000 + '"end"]}', encoding="utf-8")
    (source / "cases.jsonl").write_text(json.dumps({
        "id": "q", "prompt": "Choose", "options": ["A", "B"], "gold": 0,
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        def run(prompt):
            import inspect
            assert "path" in inspect.signature(kwargs["tools"][1]).parameters
            assert "TOOL_ERROR" in kwargs["tools"][1]("large.json")
            preview = kwargs["tools"][3]("large.json")
            assert preview.startswith('{"entries"')
            assert len(preview) < 10000
            return json.dumps({"data_files": [{
                "path": "cases.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "prompt", "options": "options",
                           "answer": "gold"}, "rule_id": "exact",
            }], "rules": [{"id": "exact", "method": "exact_option",
                           "evidence": ["cases.jsonl"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0

def test_nested_generation_prompts_use_a_source_defined_token_metric(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "prompts.json").write_text(json.dumps({
        "blue_group": {"person_one": ["A speaker once said "]},
    }), encoding="utf-8")
    (source / "RULES.md").write_text(
        "Generate one continuation of at most 20 tokens and stop at a period. Count she as female and he as male. Report the larger count, or neutral on a tie.",
        encoding="utf-8",
    )
    rule = {"id": "source_tokens", "method": "token_balance",
            "evidence": ["RULES.md"], "parameters": {
                "positive_tokens": ["she"], "negative_tokens": ["he"],
                "positive_label": "female", "negative_label": "male",
                "neutral_label": "neutral"}}

    def agents(**kwargs):
        def run(prompt):
            assert "metadata_fields" in prompt
            return json.dumps({"data_files": [{
            "path": "prompts.json", "format": "json", "records_path": "*.*.*",
            "kind": "generation", "fields": {"id": "$path", "prompt": "$value"},
            "metadata_fields": {"group": "$path.0"}, "rule_id": "source_tokens",
        }], "rules": [rule], "generation_protocol": {
            "samples_per_case": 1, "max_new_tokens": 20,
            "stop_sequences": ["."], "evidence": ["RULES.md"]}})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    case = json.loads((bundle / "cases.jsonl").read_text(encoding="utf-8"))
    assert case["prompt"] == "A speaker once said "
    assert case["kind"] == "generation"
    assert case["answer"] is None
    assert case["metadata"]["group"] == "blue_group"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation_protocol"] == {
        "samples_per_case": 1, "max_new_tokens": 20,
        "stop_sequences": ["."], "evidence": ["RULES.md"]}
    observed = tmp_path / "answers.jsonl"
    observed.write_text(json.dumps({"schema_version": 3, "id": case["id"],
                                    "status": "ok", "answer": "She won."}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(observed), "--output", str(report)]) == 0
    result = json.loads(report.read_text(encoding="utf-8"))
    item = result["items"][0]
    assert item["status"] == "scored"
    assert item["metric_label"] == "female"
    metric = result["source_metrics"][0]
    assert metric["name"] == "token_balance"
    assert metric["group"] == "blue_group"
    assert metric["denominator"] == 1
    assert metric["excluded"] == 0
    assert metric["label_counts"]["female"] == 1
    assert metric["label_proportions"]["female"] == 1.0
    assert metric["evidence"] == ["RULES.md"]

def test_agent_can_find_public_source_paper_by_title(tmp_path: Path, monkeypatch):
    import json
    import requests
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("Paper: A Sample Benchmark Paper", encoding="utf-8")
    (source / "questions.jsonl").write_text(json.dumps({
        "id": "q", "prompt": "Choose", "options": ["A", "B"], "gold": 0,
    }) + "\n", encoding="utf-8")

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"title": "A Sample Benchmark Paper",
                "externalIds": {"ArXiv": "1234.56789"},
                "openAccessPdf": {"url": "https://arxiv.org/pdf/1234.56789"}}]}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())

    def agents(**kwargs):
        def run(prompt):
            found = kwargs["tools"][4]("A Sample Benchmark Paper")
            assert found[0]["pdf_url"] == "https://arxiv.org/pdf/1234.56789"
            return json.dumps({"data_files": [{
                "path": "questions.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "prompt", "options": "options",
                           "answer": "gold"}, "rule_id": "exact",
            }], "rules": [{"id": "exact", "method": "exact_option",
                           "evidence": ["questions.jsonl"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0

def test_agent_repairs_invalid_scoring_plan_before_publication(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "id": "q", "prompt": "Choose", "options": ["A", "B"], "gold": 0,
    }) + "\n", encoding="utf-8")
    calls = []

    def agents(**kwargs):
        def run(prompt):
            calls.append(prompt)
            rule = {"id": "exact", "method": "exact_option", "evidence": ["items.jsonl"]}
            if len(calls) == 1:
                rule["unexpected"] = "invalid"
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "prompt", "options": "options",
                           "answer": "gold"}, "rule_id": "exact",
            }], "rules": [rule]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    assert len(calls) == 2
    assert "unexpected" in calls[1]

def test_nested_json_defaults_to_a_stable_source_path_id(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    for name in ("east", "west"):
        (source / f"{name}.json").write_text(json.dumps({
            "group": {"subject": [f"Prefix in {name} "]},
        }), encoding="utf-8")
    (source / "RULES.md").write_text("Count alpha and beta words.", encoding="utf-8")
    rule = {"id": "balance", "method": "token_balance", "evidence": ["RULES.md"],
            "parameters": {"positive_tokens": ["alpha"], "negative_tokens": ["beta"],
                "positive_label": "alpha", "negative_label": "beta", "neutral_label": "neutral"}}

    def agents(**kwargs):
        return lambda prompt: json.dumps({"data_files": [{
            "path": f"{name}.json", "format": "json", "records_path": "*.*.*",
            "kind": "generation", "fields": {"prompt": "$value"}, "rule_id": "balance",
        } for name in ("east", "west")], "rules": [rule]})

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source), "--output", str(bundle)]) == 0
    cases = [json.loads(line) for line in (bundle / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [case["id"] for case in cases] == [
        "east:group:subject:0", "west:group:subject:0",
    ]
def test_import_rejects_a_rule_without_snapshot_evidence(tmp_path: Path, monkeypatch, capsys):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "id": "q1", "prompt": "Choose", "options": ["A", "B"], "gold": 1,
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        def run(prompt):
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "prompt", "options": "options",
                           "answer": "gold"}, "rule_id": "exact",
            }], "rules": [{"id": "exact", "method": "exact_option",
                           "evidence": ["missing-scoring-script.py"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source),
                 "--output", str(bundle)]) == 2
    assert "scoring_validation_error" in capsys.readouterr().err
    assert not bundle.exists()
def test_imports_free_answer_with_source_rubric_and_no_gold(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "id": "essay-1", "question": "Explain the result.",
    }) + "\n", encoding="utf-8")
    (source / "RUBRIC.md").write_text(
        "Full credit requires both causal factors; partial credit for one.", encoding="utf-8")

    def agents(**kwargs):
        def run(prompt):
            assert "rubric_judge" in prompt
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "free_answer",
                "fields": {"id": "id", "prompt": "question"}, "rule_id": "rubric",
            }], "rules": [{"id": "rubric", "method": "rubric_judge",
                           "evidence": ["RUBRIC.md"], "parameters": {
                               "rubric": "Full credit requires both causal factors; partial credit for one."
                           }}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source),
                 "--output", str(bundle)]) == 0
    case = json.loads((bundle / "cases.jsonl").read_text(encoding="utf-8"))
    assert case["status"] == "ready"
    assert case["kind"] == "free_answer"
    assert case["answer"] is None
    assert case["rule_id"] == "rubric"
def test_rubricless_free_answer_is_unsupported_in_partial_import(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "choice.jsonl").write_text(json.dumps({
        "id": "c1", "question": "Choose", "options": ["A", "B"], "gold": 1,
    }) + "\n", encoding="utf-8")
    (source / "essay.jsonl").write_text(json.dumps({
        "id": "e1", "question": "Explain why",
    }) + "\n", encoding="utf-8")

    def agents(**kwargs):
        def run(prompt):
            return json.dumps({"data_files": [
                {"path": "choice.jsonl", "format": "jsonl", "kind": "single_choice",
                 "fields": {"id": "id", "prompt": "question", "options": "options",
                            "answer": "gold"}, "rule_id": "exact"},
                {"path": "essay.jsonl", "format": "jsonl", "kind": "free_answer",
                 "fields": {"id": "id", "prompt": "question"}},
            ], "rules": [{"id": "exact", "method": "exact_option",
                         "evidence": ["choice.jsonl"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents,
    ))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source),
                 "--output", str(bundle)]) == 0
    cases = [json.loads(line) for line in (bundle / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [case["status"] for case in cases] == ["ready", "unsupported"]
    assert cases[1]["reason_code"] == "missing_scoring_rule"
def test_converter_respects_one_based_choice_answer_indices(tmp_path: Path):
    import json
    from lladar.benchmark_import import _convert

    (tmp_path / "questions.jsonl").write_text(json.dumps({
        "id": "first", "prompt": "Pick one", "options": ["A", "B"], "gold": 2,
    }) + "\n", encoding="utf-8")
    plan = {"data_files": [{
        "path": "questions.jsonl", "format": "jsonl", "kind": "single_choice",
        "fields": {"id": "id", "prompt": "prompt", "options": "options", "answer": "gold"},
        "answer_index_base": 1, "rule_id": "choice",
    }], "rules": [{"id": "choice", "method": "exact_option", "evidence": ["questions.jsonl"]}]}
    case = _convert(tmp_path, plan)[0]
    assert case["status"] == "ready"
    assert case["options"] == [{"id": "1", "text": "A"}, {"id": "2", "text": "B"}]
    assert case["answer"] == "2"

def test_import_rejects_scoring_parameters_the_scorer_would_ignore(tmp_path: Path):
    import pytest
    from lladar.benchmark_import import _validate_rule_evidence
    from lladar.exceptions import LladarError

    (tmp_path / "method.txt").write_text("Full and partial credit are distinct.",
                                          encoding="utf-8")
    plan = {"rules": [{"id": "source_score", "method": "exact_option",
                       "evidence": ["method.txt"],
                       "parameters": {"partial_credit": 0.5}}]}
    with pytest.raises(LladarError, match="scoring_validation_error"):
        _validate_rule_evidence(tmp_path, plan)

def test_import_preserves_source_metric_it_cannot_reproduce(tmp_path: Path, monkeypatch):
    import json
    import sys
    import types

    source = tmp_path / "source"
    source.mkdir()
    (source / "items.jsonl").write_text(json.dumps({
        "id": "q1", "question": "Pick", "choices": ["A", "B"], "gold": 1,
    }) + "\n", encoding="utf-8")
    (source / "METHOD.md").write_text(
        "Accuracy is exact gold match. Bias score needs a separate demographic join.",
        encoding="utf-8")

    def agents(**kwargs):
        def run(prompt):
            assert "unsupported_metrics" in prompt
            return json.dumps({"data_files": [{
                "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
                "fields": {"id": "id", "prompt": "question",
                           "options": "choices", "answer": "gold"},
                "rule_id": "accuracy",
            }], "rules": [{"id": "accuracy", "method": "exact_option",
                           "evidence": ["METHOD.md"]}],
                "unsupported_metrics": [{"id": "bias_score",
                    "reason_code": "requires_unimplemented_source_join",
                    "reason": "A demographic join is required but not available in this converter.",
                    "evidence": ["METHOD.md"]}]})
        return run

    monkeypatch.setitem(sys.modules, "akasha", types.SimpleNamespace(
        create_tool=lambda description, function, name: function, agents=agents))
    bundle = tmp_path / "bundle"
    assert main(["create", "dataset", "--convert-from", str(source),
                 "--output", str(bundle)]) == 0
    plan = json.loads((bundle / "scoring-plan.json").read_text(encoding="utf-8"))
    assert plan["unsupported_metrics"][0]["id"] == "bias_score"
    answers = tmp_path / "answers.jsonl"
    answers.write_text(json.dumps({"schema_version": 3, "id": "q1", "status": "ok", "answer": "1"}) + "\n",
                       encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["eval", str(bundle), str(answers), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["scored"] == 1
    assert report["unsupported_metrics"][0]["id"] == "bias_score"

def test_converter_reads_csv_choice_rows_with_source_line_numbers(tmp_path: Path):
    from lladar.benchmark_import import _convert

    (tmp_path / "questions.csv").write_text(
        "uid,question,first,second,gold\nq1,Pick one,A,B,2\n",
        encoding="utf-8")
    plan = {"data_files": [{
        "path": "questions.csv", "format": "csv", "kind": "single_choice",
        "fields": {"id": "uid", "prompt": "question",
                   "options": ["first", "second"], "answer": "gold"},
        "answer_index_base": 1, "rule_id": "choice",
    }], "rules": [{"id": "choice", "method": "exact_option",
                  "evidence": ["questions.csv"]}]}
    case = _convert(tmp_path, plan)[0]
    assert case["status"] == "ready"
    assert case["answer"] == "2"
    assert case["source"] == {"path": "questions.csv", "line": 2}
    assert case["options"] == [{"id": "1", "text": "A"}, {"id": "2", "text": "B"}]

def test_converter_preserves_source_option_ids_and_gold_label(tmp_path: Path):
    import json
    from lladar.benchmark_import import _convert

    (tmp_path / "items.jsonl").write_text(json.dumps({
        "id": "q1", "prompt": "Choose", "choices": [
            {"key": "left", "value": "Go left"},
            {"key": "right", "value": "Go right"}], "gold": "right",
    }) + "\n", encoding="utf-8")
    plan = {"data_files": [{
        "path": "items.jsonl", "format": "jsonl", "kind": "single_choice",
        "fields": {"id": "id", "prompt": "prompt",
                   "options": "choices", "answer": "gold"},
        "option_id_field": "key", "option_text_field": "value",
        "answer_mode": "option_id", "rule_id": "exact",
    }], "rules": [{"id": "exact", "method": "exact_option",
                  "evidence": ["items.jsonl"]}]}
    case = _convert(tmp_path, plan)[0]
    assert case["status"] == "ready"
    assert case["options"] == [
        {"id": "left", "text": "Go left"},
        {"id": "right", "text": "Go right"}]
    assert case["answer"] == "right"

def test_generation_protocol_can_record_model_profiles_without_inventing_sample_count(tmp_path: Path):
    from lladar.benchmark_import import _validate_generation_protocol

    paper = tmp_path / "paper.txt"
    paper.write_text("GPT-2 uses top-k 40 and top-p 0.95.", encoding="utf-8")
    plan = {"generation_protocol": {
        "evidence": ["paper.txt"],
        "source_profiles": [{"model": "GPT-2",
                             "settings": {"top_k": 40, "top_p": 0.95}}],
    }}
    _validate_generation_protocol(tmp_path, plan)

def test_converter_splits_csv_multiple_choice_gold_labels(tmp_path: Path):
    from lladar.benchmark_import import _convert

    (tmp_path / "questions.csv").write_text(
        "uid,question,red,green,blue,gold\nq1,Select colors,Red,Green,Blue,1|3\n",
        encoding="utf-8")
    plan = {"data_files": [{
        "path": "questions.csv", "format": "csv", "kind": "multiple_choice",
        "fields": {"id": "uid", "prompt": "question",
                   "options": ["red", "green", "blue"], "answer": "gold"},
        "answer_index_base": 1, "answer_separator": "|", "rule_id": "set",
    }], "rules": [{"id": "set", "method": "exact_option_set",
                  "evidence": ["questions.csv"]}]}
    case = _convert(tmp_path, plan)[0]
    assert case["status"] == "ready"
    assert case["answer"] == ["1", "3"]

def test_source_copy_enforces_file_size_limit(tmp_path: Path, monkeypatch):
    import pytest
    from lladar import benchmark_import
    from lladar.exceptions import LladarError

    source = tmp_path / "source"
    source.mkdir()
    (source / "oversized.jsonl").write_bytes(b"123456")
    monkeypatch.setattr(benchmark_import, "_MAX_SOURCE_FILE_BYTES", 5, raising=False)
    with pytest.raises(LladarError, match="source_read_error"):
        benchmark_import._copy_source(source, tmp_path / "snapshot")

def test_test_dataset_convert_from_routes_to_benchmark_import(tmp_path: Path, monkeypatch, capsys):
    from lladar import benchmark_import

    calls = []
    output = tmp_path / "bundle"

    def fake_import(source, destination, *, model, env_file):
        calls.append((source, destination, model, env_file))
        return output

    monkeypatch.setattr(benchmark_import, "import_benchmark", fake_import)
    assert main([
        "create", "test-dataset", "--convert-from", str(tmp_path / "source"),
        "--output", str(output), "--model", "gemini:test-model", "--env-file", "local.env",
    ]) == 0
    assert calls == [(str(tmp_path / "source"), str(output), "gemini:test-model", "local.env")]
    assert f"Created benchmark dataset at {output}" in capsys.readouterr().out


def test_test_dataset_convert_from_rejects_generation_options(tmp_path: Path, monkeypatch, capsys):
    from lladar import benchmark_import

    def unexpected_import(*args, **kwargs):
        raise AssertionError("import must not start with generation settings")

    monkeypatch.setattr(benchmark_import, "import_benchmark", unexpected_import)
    assert main([
        "create", "test-dataset", "--convert-from", str(tmp_path / "source"),
        "--knowledge", "guide.md",
    ]) == 2
    assert "--convert-from cannot be combined with --knowledge" in capsys.readouterr().err
