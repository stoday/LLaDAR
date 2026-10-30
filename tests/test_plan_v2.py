import json

import pytest

from lladar.plan import run_plan


def document_plan(tmp_path):
    knowledge = tmp_path / "退款 '規則' $.md"
    knowledge.write_text("七天內可退款。", encoding="utf-8")
    project = tmp_path / "客服專案"
    project.mkdir()
    return {
        "schema_version": 2, "workflow": "document_qa", "status": "draft",
        "user_goal": "確認客服依退款規則回答", "observable_behavior": "回答符合退款文件",
        "inputs": {"knowledge": [str(knowledge)]},
        "target": {"kind": "project", "path": str(project)},
        "method": {"kind": "source_grounded_qa", "count": 10},
        "evaluation": {"kind": "expected_answer"},
        "stages": ["create", "run-agent", "eval", "report"],
    }


def test_document_plan_compiles_consistent_package_without_running_target(tmp_path):
    spec = document_plan(tmp_path)
    destination = run_plan(
        requirement="依退款文件測客服", output=tmp_path / "plan", model="fixture:model",
        env_file=".env", agent_factory=lambda **_: lambda _: {"kind": "complete", "plan": spec},
        output_fn=lambda _: None,
    )
    payload = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["plan"]["status"] == "ready"
    commands = payload["plan"]["commands"]
    assert [command[1] for command in commands] == ["create", "run-agent", "eval", "report"]
    assert commands[0][commands[0].index("--knowledge") + 1] == spec["inputs"]["knowledge"][0]
    assert commands[1][2] == commands[0][commands[0].index("--output") + 1]
    assert "''規則''" in (destination / "commands.ps1").read_text(encoding="utf-8")
    assert not (destination / "dataset.jsonl").exists()
    assert (destination / "plan.log").is_file()
    events = [json.loads(line) for line in (destination / "plan-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["stage"] == "compile" and event["event_type"] == "success" for event in events)
    assert events[-1]["event_type"] == "finished"


def execute(tmp_path, responses, *, answers=(), debug=False, **options):
    replies = iter(responses)
    inputs = iter(answers)
    calls, console = [], []

    def factory(**kwargs):
        def answer(prompt):
            calls.append(prompt)
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value
        return answer

    destination = run_plan(requirement="測試客服", output=tmp_path / "plan", model="fixture:model",
                           env_file=".env", agent_factory=factory, input_fn=lambda _: next(inputs),
                           output_fn=console.append, debug=debug, **options)
    payload = json.loads((destination / "plan.json").read_text(encoding="utf-8"))
    return destination, payload, calls, console


def complete(spec):
    return {"kind": "complete", "plan": spec}


def test_missing_target_is_blocked_with_field_and_next_action(tmp_path):
    spec = document_plan(tmp_path)
    spec["target"] = {"kind": "project"}
    spec["open_questions"] = ["尚無受測專案路徑"]
    destination, payload, calls, console = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "blocked"
    diagnostic = next(item for item in payload["plan"]["diagnostics"] if item["field_path"] == ["target", "path"])
    assert diagnostic["code"] == "PLAN_REQUIRED_FIELD_MISSING"
    assert diagnostic["next_action"]
    assert diagnostic["trace_id"] in "\n".join(console)
    assert not (destination / "commands.ps1").exists()
    assert (destination / "diagnostics.json").is_file()
    assert len(calls) == 1


def test_creation_only_behavior_plan_requires_no_target_or_documents(tmp_path):
    spec = {"schema_version": 2, "status": "draft", "workflow": "behavior_situation",
            "user_goal": "資訊不足時追問", "observable_behavior": "問缺少的資訊",
            "inputs": {}, "method": {"kind": "behavior_situation", "instructions": "觀察追問",
                                       "stop_criteria": "回答一次後結束", "max_turns": 1}, "stages": ["create"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "ready"
    command, = payload["plan"]["commands"]
    assert command[:3] == ["lladar", "create", "situation"]
    assert command[command.index("--max-turns") + 1] == "1"


@pytest.mark.parametrize("filename,content", [("missing.jsonl", None), ("bad.jsonl", '{"wrong":"record"}\n')])
def test_invalid_existing_responses_are_blocked_instead_of_program_failure(tmp_path, filename, content):
    path = tmp_path / filename
    if content is not None:
        path.write_text(content, encoding="utf-8")
    spec = {"schema_version": 2, "status": "draft", "workflow": "evaluate_existing_responses",
            "user_goal": "重新評估", "observable_behavior": "符合規則", "inputs": {"responses": str(path)},
            "method": {"kind": "existing_responses"}, "evaluation": {"kind": "expected_answer"}, "stages": ["eval"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "blocked"
    assert payload["plan"]["diagnostics"][0]["code"] == "PLAN_EXISTING_INPUT_MISSING"


def question(field, known=None, message="請提供受測專案路徑", **kwargs):
    return {"kind": "question", "field": field, "message": message, "known": known or {}, **kwargs}


def test_conversation_rejects_repeated_and_irrelevant_questions_then_finishes(tmp_path):
    spec = document_plan(tmp_path)
    known = {key: value for key, value in spec.items() if key != "target"}
    responses = [question("target.kind", known, options=[{"label": "專案"}, {"label": "網站"}]),
                 question("target.kind", known),
                 question("method.max_turns", known),
                 complete(spec)]
    _, payload, calls, _ = execute(tmp_path, responses, answers=["1"], debug=True)
    assert payload["plan"]["status"] == "ready"
    assert len(calls) == 4
    assert "answered_fields" in calls[2]
    events = (tmp_path / "plan" / "plan-events.jsonl").read_text(encoding="utf-8")
    assert "repeats known/answered" in events
    assert "not needed by this profile" in events


@pytest.mark.parametrize("mutation", [
    lambda spec: spec.update(status="ready"),
    lambda spec: spec.update(commands=[["lladar", "eval", "x", "--made-up"]]),
    lambda spec: spec["method"].update(count=True),
    lambda spec: spec.update(schema_version=1),
])
def test_untrusted_model_schema_has_bounded_retries_and_never_creates_commands(tmp_path, mutation):
    spec = document_plan(tmp_path)
    mutation(spec)
    destination, payload, calls, _ = execute(tmp_path, [complete(spec)] * 3)
    assert payload["plan"]["status"] == "failed"
    assert len(calls) == 3
    expected = "PLAN_COMPILATION_ERROR" if "commands" in spec else "PLAN_MODEL_INVALID_RESPONSE"
    assert payload["plan"]["diagnostics"][0]["code"] == expected
    assert not (destination / "commands.ps1").exists()


@pytest.mark.parametrize("failure", ["", "not json", "[]", TimeoutError("model timeout")])
def test_invalid_model_and_timeout_save_failed_diagnostics_and_log(tmp_path, failure):
    _, payload, _, console = execute(tmp_path, [failure] * 3)
    assert payload["plan"]["status"] == "failed"
    diagnostic = payload["plan"]["diagnostics"][0]
    assert diagnostic["code"] in {"PLAN_MODEL_INVALID_RESPONSE", "PLAN_UNEXPECTED_EXCEPTION"}
    log = (tmp_path / "plan" / "plan.log").read_text(encoding="utf-8")
    assert diagnostic["trace_id"] in log and diagnostic["trace_id"] in "\n".join(console)
    assert "Traceback" in log


def test_external_state_verification_is_blocked_even_when_files_exist(tmp_path):
    spec = document_plan(tmp_path)
    spec["observation"] = {"kind": "external_state"}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "blocked"
    assert any(item["code"] == "PLAN_EVIDENCE_INSUFFICIENT" for item in payload["plan"]["diagnostics"])


def test_existing_responses_skip_creation_and_target(tmp_path):
    responses = tmp_path / "responses.jsonl"
    responses.write_text(json.dumps({"question": "Q", "expected_answer": "A", "actual_response": "A"}) + "\n", encoding="utf-8")
    spec = {"schema_version": 2, "status": "draft", "workflow": "evaluate_existing_responses",
            "user_goal": "重評估", "observable_behavior": "符合規則", "inputs": {"responses": str(responses)},
            "method": {"kind": "existing_responses"}, "evaluation": {"kind": "criteria", "criteria": "引用回答判斷"},
            "stages": ["eval", "report"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "ready"
    assert [cmd[1] for cmd in payload["plan"]["commands"]] == ["eval", "report"]


def test_existing_evaluation_needs_only_report(tmp_path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({"source": "responses.jsonl", "trials_source": None, "skill": None,
                                     "evaluator_model": "fixture", "plan": {}, "summary": {}, "aggregates": {},
                                     "stability": {}, "items": []}), encoding="utf-8")
    spec = {"schema_version": 2, "status": "draft", "workflow": "report_existing_evaluation",
            "user_goal": "報告", "observable_behavior": "展示既有結果", "inputs": {"evaluation": str(evaluation)},
            "method": {"kind": "existing_evaluation"}, "stages": ["report"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "ready"
    assert [cmd[1] for cmd in payload["plan"]["commands"]] == ["report"]


def test_log_write_failure_preserves_original_exception_and_has_no_script(tmp_path):
    locked = tmp_path / "existing.log"
    locked.write_text("preserve", encoding="utf-8")
    destination, payload, _, console = execute(tmp_path, [TimeoutError("original model failure")], log=locked)
    assert payload["plan"]["status"] == "failed"
    assert locked.read_text(encoding="utf-8") == "preserve"
    assert not (destination / "commands.ps1").exists()
    assert any(item["code"] == "PLAN_ARTIFACT_WRITE_ERROR" for item in payload["plan"]["diagnostics"])


def test_debug_logs_and_artifacts_redact_secrets_consistently(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "fixture-environment-secret")
    spec = document_plan(tmp_path)
    q = question("target.kind", {}, message="API_KEY=fixture-conversation-secret")
    destination, payload, _, console = execute(tmp_path, [q, complete(spec)], answers=["ordinary answer"], debug=True)
    output = "\n".join(console) + "\n".join(path.read_text(encoding="utf-8") for path in destination.iterdir() if path.is_file())
    assert "fixture-conversation-secret" not in output
    assert "fixture-environment-secret" not in output
    assert "[REDACTED]" in output
    assert "[REDACTED]]" not in output
    assert payload["plan"]["status"] == "ready"


def test_model_exception_and_log_failure_are_both_visible(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.open
    writes = []

    def fail_after_first_log_write(path, *args, **kwargs):
        if path.name == "plan.log" and args and args[0] in {"a", "x"}:
            writes.append(path)
            if len(writes) > 1:
                raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_after_first_log_write)
    _, payload, _, console = execute(tmp_path, [RuntimeError("original model exception")])
    assert payload["plan"]["status"] == "failed"
    assert {item["code"] for item in payload["plan"]["diagnostics"]} == {"PLAN_UNEXPECTED_EXCEPTION", "PLAN_ARTIFACT_WRITE_ERROR"}
    assert "original model exception" in "\n".join(console)
    assert "disk full" in "\n".join(console)


def test_goal_change_discards_old_target_and_rejects_stale_completion(tmp_path):
    spec = document_plan(tmp_path)
    first = question("target.kind", {"user_goal": "old goal", "workflow": "document_qa", "stages": ["create", "run-agent"]})
    changed = question("inputs.knowledge", {"user_goal": "new goal: only create questions", "workflow": "document_qa", "stages": ["create"]}, message="新的文件路徑？")
    spec["user_goal"] = "old goal"
    spec["stages"] = ["create", "run-agent"]
    destination, payload, _, _ = execute(tmp_path, [first, changed, complete(spec), complete(spec), complete(spec)], answers=["project", "new.md"])
    assert payload["plan"]["status"] == "failed"
    assert not (destination / "commands.ps1").exists()
    events = (destination / "plan-events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "reset"' in events


@pytest.mark.parametrize("max_turns,status", [(1, "ready"), (2, "blocked")])
def test_website_situations_enforce_single_turn(tmp_path, max_turns, status):
    spec = {"schema_version": 2, "status": "draft", "workflow": "behavior_situation",
            "user_goal": "觀察追問", "observable_behavior": "是否追問缺少資訊", "inputs": {},
            "target": {"kind": "page", "url": "https://example.test/chat"},
            "method": {"kind": "behavior_situation", "instructions": "資訊不足時觀察回答", "stop_criteria": "回答後停止",
                       "max_turns": max_turns, "num_scenarios": 3}, "evaluation": {"kind": "situation"},
            "stages": ["create", "run-agent", "eval", "report"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == status


def test_cli_plan_owns_custom_log_and_returns_nonzero_for_failed_plan(tmp_path):
    from lladar.cli import main
    spec = document_plan(tmp_path)
    log = tmp_path / "custom.log"
    destination = tmp_path / "cli-plan"
    assert main(["plan", "依文件測客服", "--output", str(destination), "--log", str(log), "--debug", "--lang", "zh-TW"],
                plan_agent_factory=lambda **_: lambda _: complete(spec)) == 0
    assert "Compiled stage" in log.read_text(encoding="utf-8")
    assert "測試目標" in (destination / "plan.md").read_text(encoding="utf-8")
    assert not (destination / "plan.log").exists()
    failure = tmp_path / "failed-cli"
    assert main(["plan", "test", "--output", str(failure)],
                plan_agent_factory=lambda **_: lambda _: "invalid json") == 2
    assert (failure / "diagnostics.json").is_file()


def test_debug_changes_console_detail_but_always_logs_lifecycle(tmp_path):
    spec = document_plan(tmp_path)
    for debug in (False, True):
        root = tmp_path / str(debug)
        root.mkdir()
        destination, payload, _, console = execute(root, [complete(spec)], debug=debug)
        assert any("Requesting planning model" in line for line in console) is debug
        assert "Requesting planning model" in (destination / "plan.log").read_text(encoding="utf-8")
        assert payload["plan"]["status"] == "ready"


def test_incomplete_candidate_is_clarified_before_finalizing(tmp_path):
    spec = document_plan(tmp_path)
    from copy import deepcopy
    incomplete = deepcopy(spec)
    incomplete.pop("evaluation")
    _, payload, calls, _ = execute(tmp_path, [complete(incomplete), complete(spec)])
    assert payload["plan"]["status"] == "ready"
    assert len(calls) == 2
    assert "missing_fields" in calls[1]


def test_last_log_write_failure_cannot_leave_a_ready_script(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.open

    class FailingLog:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, text):
            if "[stage=plan] FINISHED" in text:
                raise OSError("disk full at finish")
            return self.stream.write(text)

    def open_log(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return FailingLog(stream) if path.name == "plan.log" and args and args[0] in {"a", "x"} else stream

    monkeypatch.setattr(Path, "open", open_log)
    destination, payload, _, _ = execute(tmp_path, [complete(document_plan(tmp_path))])
    assert payload["plan"]["status"] == "failed"
    assert not (destination / "commands.ps1").exists()


def test_failed_draft_keeps_latest_decisions_and_conversation(tmp_path):
    first = question("target.kind", {"user_goal": "canonical goal", "workflow": "document_qa", "stages": ["create", "run-agent"]})
    _, payload, _, _ = execute(tmp_path, [first, RuntimeError("model unavailable")], answers=["project"])
    assert payload["plan"]["status"] == "failed"
    assert payload["plan"]["user_goal"] == "canonical goal"
    assert payload["plan"]["workflow"] == "document_qa"
    assert payload["conversation"][-1]["content"] == "project"


def test_existing_situation_responses_compile_with_frozen_config(tmp_path):
    from hashlib import sha256
    from lladar.situation import create_situation
    from test_situation import AuthoringAgent
    config = tmp_path / "situation.json"
    create_situation(instructions="Ask for missing details", stop_criteria="One response", max_turns=1,
                     knowledge=[], output=config, skill_agent_factory=AuthoringAgent)
    responses = tmp_path / "responses.jsonl"
    responses.write_text(json.dumps({"kind": "situation_transcript", "turns": [], "scenario": {}, "trial_id": "t1",
                                     "scenario_id": "s1", "status": "execution_error", "stop_reason": "failed"}) + "\n", encoding="utf-8")
    responses.with_name(responses.name + ".run.json").write_text(json.dumps({
        "kind": "situation_run", "situation_sha256": sha256(config.read_bytes()).hexdigest(),
        "responses_sha256": sha256(responses.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    spec = {"schema_version": 2, "status": "draft", "workflow": "evaluate_existing_responses",
            "user_goal": "重評估情境", "observable_behavior": "依既有判準觀察追問",
            "inputs": {"responses": str(responses), "situation_config": str(config)},
            "method": {"kind": "existing_responses"}, "evaluation": {"kind": "situation"}, "stages": ["eval", "report"]}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "ready"
    assert "--situation-config" in payload["plan"]["commands"][0]


def test_model_usage_counts_are_logged_as_numbers_without_provider_trace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = document_plan(tmp_path)
    options = []

    class Provider:
        def __init__(self, **kwargs):
            options.append(kwargs)

        def generate_message(self, prompt, **kwargs):
            return SimpleNamespace(content=json.dumps(complete(spec)), usage_metadata={"input_tokens": 123, "output_tokens": 45})

    monkeypatch.setattr("lladar.plan.AkashaProvider", Provider)
    destination = run_plan(requirement="test", output=tmp_path / "plan", model="fixture:model", env_file=".env",
                           input_fn=lambda _: "answer", output_fn=lambda _: None, debug=True)
    events = [json.loads(line) for line in (destination / "plan-events.jsonl").read_text(encoding="utf-8").splitlines()]
    usage = next(event for event in events if event["event_type"] == "usage")
    assert usage["input_tokens"] == 123
    assert usage["output_tokens"] == 45
    assert options[0]["verbose"] is False


def test_secret_url_and_cookie_headers_never_appear_in_diagnostics(tmp_path):
    error = RuntimeError("request https://user:password@example.test/?key=fixture-key&sig=fixture-signature\nCookie: a=first-cookie; b=second-cookie")
    destination, payload, _, console = execute(tmp_path, [error], debug=True)
    material = "\n".join(console) + "\n".join(path.read_text(encoding="utf-8") for path in destination.iterdir() if path.is_file())
    for secret in ("user:password", "fixture-key", "fixture-signature", "first-cookie", "second-cookie"):
        assert secret not in material
    assert payload["plan"]["status"] == "failed"


def test_provider_rate_limit_is_actionable_and_is_not_retried(tmp_path):
    from lladar.exceptions import ProviderError
    error = ProviderError("Akasha generation failed")
    error.__cause__ = RuntimeError("429 RESOURCE_EXHAUSTED")
    _, payload, calls, _ = execute(tmp_path, [error])
    assert len(calls) == 1
    diagnostic = payload["plan"]["diagnostics"][0]
    assert diagnostic["code"] == "PLAN_MODEL_RATE_LIMITED"
    assert diagnostic["recoverable"] is True
    assert "quota" in diagnostic["next_action"]


def test_documented_api_target_compiles_service_url_without_contacting_it(tmp_path):
    spec = document_plan(tmp_path)
    guide = tmp_path / "api.md"
    guide.write_text("POST /answer accepts a JSON question and returns a JSON answer.", encoding="utf-8")
    spec["target"] = {"kind": "api", "api_guide": str(guide), "service_url": "http://127.0.0.1:1"}
    _, payload, _, _ = execute(tmp_path, [complete(spec)])
    assert payload["plan"]["status"] == "ready"
    argv = payload["plan"]["commands"][1]
    assert argv[argv.index("--service-url") + 1] == "http://127.0.0.1:1"
    assert argv[argv.index("--api-guide") + 1] == str(guide.resolve())


def test_failed_plan_json_write_preserves_other_diagnostics_and_removes_commands(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.write_text
    spec = document_plan(tmp_path)
    console = []

    def cannot_write_plan(path, *args, **kwargs):
        if path.name == "plan.json":
            raise OSError("plan JSON disk failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", cannot_write_plan)
    destination = run_plan(requirement="test", output=tmp_path / "plan", model="fixture:model", env_file=".env",
                           agent_factory=lambda **_: lambda _: complete(spec), output_fn=console.append)
    assert not (destination / "commands.ps1").exists()
    diagnostics = json.loads((destination / "diagnostics.json").read_text(encoding="utf-8"))
    assert any(item["code"] == "PLAN_ARTIFACT_WRITE_ERROR" for item in diagnostics)
    assert "plan JSON disk failure" in "\n".join(console)
    assert "failed" in (destination / "plan.md").read_text(encoding="utf-8")


def test_model_failure_after_incomplete_candidate_is_failed_not_blocked(tmp_path):
    spec = document_plan(tmp_path)
    spec.pop("evaluation")
    destination, payload, _, _ = execute(tmp_path, [complete(spec), "invalid json", "invalid json", "invalid json"])
    assert payload["plan"]["status"] == "failed"
    assert any(item["code"] == "PLAN_MODEL_INVALID_RESPONSE" for item in payload["plan"]["diagnostics"])
    assert not (destination / "commands.ps1").exists()
