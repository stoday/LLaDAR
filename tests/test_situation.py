from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lladar.cli import main
from lladar.reporting import create_report
from lladar.situation import create_situation, evaluate_situation, run_situation


class AuthoringAgent:
    def __init__(self, *, skills, tools, **_kwargs):
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, _request):
        self.tools["submit_situation"]({
            "observable_conditions": ["Target asks for an identifier."],
            "fixed_constraints": [],
            "variation_axes": [{"id": "tone", "values": ["calm", "urgent"]}],
            "generation_method": {"id": "generate", "version": "1", "instructions": "Vary tone."},
            "run_method": {"id": "follow-up", "version": "1", "instructions": "Ask one follow-up."},
            "evaluation_method": {"id": "judge", "version": "1", "instructions": "Cite turns."},
            "rubric": [{"id": "asks", "observed_when": "Asks for number.",
                        "invalid_when": "Invents a status."}],
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


class Provider:
    def __init__(self, **_kwargs):
        self.next_calls = 0

    def generate_structured(self, prompt, **_kwargs):
        request = json.loads(prompt)
        if request["task"].startswith("Generate"):
            return {"setup": "Missing order number.", "initial_message": "Where is my order?",
                    "variation": request["selected_variation"]}
        if request["task"].startswith("Choose"):
            self.next_calls += 1
            previous = request["turns"][-1]["output"]
            message = ("I still need help after: " + previous
                       if self.next_calls == 1 else
                       "I cannot provide the number. What should I do?")
            return {"stop": False, "reason": "Need another turn", "message": message}
        return {"validity": "valid", "behavior": "observed",
                "evidence_turn_ids": [request["turns"][0]["turn_id"]],
                "reason": "The app requested the order number."}


@pytest.mark.parametrize("model,expected_input,expected_output", [
    ("gemini:gemini-3.7-flash", 1_048_576, 65_536),
    ("other:model", 16_384, 8_192),
])
def test_situation_authoring_uses_lladar_model_budget(tmp_path, model, expected_input, expected_output):
    received = {}

    def factory(**options):
        received.update(options)
        return AuthoringAgent(**options)

    destination = tmp_path / "situation.json"
    create_situation(instructions="觀察回答是否出現指定詞彙", stop_criteria="出現指定詞彙",
                     max_turns=1, knowledge=[], output=destination,
                     model=model, skill_agent_factory=factory)

    # Missing limits make Akasha default to 1,024 output tokens, which caused
    # the live model to end with MALFORMED_FUNCTION_CALL without a proposal.
    assert received.get("max_output_tokens") == expected_output
    assert received.get("max_input_tokens") == expected_input
    assert json.loads(destination.read_text(encoding="utf-8"))["stop"]["max_turns"] == 1


@pytest.mark.parametrize("flags,expected_input,expected_output", [
    (["--max-input-tokens", "16384", "--max-output-tokens", "8192"], 16384, 8192),
    (["--max-input-tokens", "16384"], 16384, 65536),
    (["--max-output-tokens", "8192"], 1048576, 8192),
])
def test_situation_cli_overrides_model_budget(tmp_path, flags, expected_input, expected_output):
    received = {}

    def factory(**options):
        received.update(options)
        return AuthoringAgent(**options)

    destination = tmp_path / "situation.json"
    assert main(["create", "situation", "--instructions", "觀察指定詞彙",
                 "--stop-criteria", "出現指定詞彙", "--max-turns", "1",
                 "--model", "gemini:gemini-3.7-flash", "--output", str(destination),
                 *flags], skill_agent_factory=factory) == 0
    assert received["max_input_tokens"] == expected_input
    assert received["max_output_tokens"] == expected_output
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["created_with"]["max_input_tokens"] == expected_input
    assert saved["created_with"]["max_output_tokens"] == expected_output


@pytest.mark.parametrize("flag", ["--max-input-tokens", "--max-output-tokens"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_situation_cli_rejects_nonpositive_budget_before_model_work(tmp_path, capsys, flag, value):
    def forbidden_factory(**options):
        pytest.fail("Invalid budget must be rejected before model work")

    destination = tmp_path / "situation.json"
    assert main(["create", "situation", "--instructions", "Observe terms",
                 "--stop-criteria", "Term appears", "--max-turns", "1",
                 "--output", str(destination), flag, value],
                skill_agent_factory=forbidden_factory) == 2
    assert "must be greater than 0" in capsys.readouterr().err
    assert not destination.exists()


def test_situation_end_to_end_with_three_target_turns(tmp_path):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After three turns",
                     max_turns=3, knowledge=[], output=config,
                     skill_agent_factory=AuthoringAgent)
    transcript = tmp_path / "responses.jsonl"
    project = Path(__file__).parents[1] / "example_project" / "situation_demo"
    assert run_situation(config, transcript, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "runs",
                         provider_factory=Provider) == 1
    calibration = json.loads(Path(str(transcript) + ".calibration.json").read_text())
    assert calibration["status"] == "passed"
    assert calibration["same_session_recalled"] is True
    assert calibration["fresh_session_leaked"] is False
    trial = json.loads(transcript.read_text().splitlines()[0])
    assert len(trial["turns"]) == 3
    assert trial["turns"][0]["turn_id"] != trial["turns"][1]["turn_id"]
    result = evaluate_situation(transcript, config, output=tmp_path / "evaluation.json",
                                provider_factory=Provider)
    assert result["summary"]["observed"] == 1
    report = tmp_path / "report.md"
    create_report(tmp_path / "evaluation.json", report)
    assert "Trial evidence" in report.read_text()


class SessionCodingFactory:
    def __init__(self, *, fail_initial=False):
        self.calls = []
        self.fail_initial = fail_initial

    def __call__(self, **options):
        self.calls.append(options)
        tools = {tool.name: tool for tool in options["tools"]}
        turn = len(self.calls)

        def respond(prompt):
            tools["list_files"].invoke({"pattern": "**/*"})
            tools["read_file"].invoke({"path": "app.py"})
            if turn == 1:
                result = {"candidates": [{
                    "id": "public", "label": "Order assistant", "entrypoint": "app.py:OrderAssistant.chat",
                    "public_boundary": True, "rationale": "Public conversation interface",
                    "flow": ["OrderAssistant", "chat"], "output": "returned answer", "transport": "python",
                    "service": None, "evidence": [{"path": "app.py", "line": 7, "quote": "class OrderAssistant:"}],
                }], "unresolved": [], "summary": "Public chat"}
            elif turn == 2 and self.fail_initial:
                return iter([{"type": "answer", "data": "invalid proposal"}])
            else:
                assert "SESSION ADAPTER RUNTIME PROTOCOL" in prompt
                assert "reads exactly one JSON object" not in prompt
                source = Path(__file__).parents[1] / "example_project" / "situation_demo" / "lladar_session.py"
                path = tools["write_harness"].invoke({"filename": "adapter.py", "content": source.read_text(encoding="utf-8")})
                tools["run_harness"].invoke({"path": path, "message": "Hello. Please respond briefly."})
                result = {"harness": path, "explanation": "Real application session", "blockers": []}
            return iter([{"type": "answer", "data": json.dumps(result)}])
        return respond


def test_candidate_session_adapter_is_calibrated_before_use(tmp_path):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After one turn",
                     max_turns=1, knowledge=[], output=config,
                     skill_agent_factory=AuthoringAgent)
    project = tmp_path / "app"
    project.mkdir()
    source = Path(__file__).parents[1] / "example_project" / "situation_demo" / "app.py"
    (project / "app.py").write_bytes(source.read_bytes())
    transcript = tmp_path / "responses.jsonl"
    assert run_situation(config, transcript, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "runs",
                         provider_factory=Provider, adapter_agent_factory=SessionCodingFactory(),
                         graphify=False, interactive=False) == 1
    calibration = json.loads(Path(str(transcript) + ".calibration.json").read_text())
    assert calibration["generated"] is True
    assert calibration["status"] == "passed"
    run = json.loads(Path(str(transcript) + ".run.json").read_text())
    assert run["adapter_generated"] is True
    assert not (project / "lladar_session.py").exists()


def test_selected_service_url_is_passed_to_session_driver(tmp_path):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After one turn",
                     max_turns=1, knowledge=[], output=config,
                     skill_agent_factory=AuthoringAgent)
    source = Path(__file__).parents[1] / "example_project" / "situation_demo"
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_bytes((source / "app.py").read_bytes())
    driver = (source / "lladar_session.py").read_text(encoding="utf-8")
    driver = ("import os\nassert os.environ.get('LLADAR_SERVICE_URL') == "
              "'http://127.0.0.1:8765'\n" + driver.replace(
                  "from __future__ import annotations", "", 1))
    (project / "lladar_session.py").write_text(driver, encoding="utf-8")
    output = tmp_path / "responses.jsonl"
    assert run_situation(config, output, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "runs",
                         service_url="http://127.0.0.1:8765",
                         provider_factory=Provider) == 1
    run = json.loads(Path(str(output) + ".run.json").read_text())
    assert run["service_url"] == "http://127.0.0.1:8765"


class ReportAgent:
    def __init__(self, *, skills, tools, **_kwargs):
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, _request):
        self.tools["submit_report"]({
            "overview": "The saved trial evidence supports this report.",
            "findings": "The target requested the missing identifier.",
            "limitations": "The result depends on the selected scenarios.",
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


def test_situation_report_skill_changes_prose_only(tmp_path):
    source = tmp_path / "evaluation.json"
    source.write_text(json.dumps({
        "kind": "situation_evaluation", "source": "responses.jsonl",
        "situation_config": "situation.json", "situation_sha256": "hash",
        "evaluator_model": "fixture", "method": {"id": "judge"},
        "observe": {"text": "Ask for an identifier"},
        "summary": {"observed": 1, "valid_determinate": 1},
        "items": [{"scenario_id": "scenario-001", "status": "completed",
                   "turns": [{"turn_id": "turn-1"}], "validity": "valid",
                   "behavior": "observed", "evidence_turn_ids": ["turn-1"],
                   "reason": "Target asked for an ID."}],
    }), encoding="utf-8")
    report = tmp_path / "report.md"
    skill = Path(__file__).parents[1] / "src" / "lladar" / "skill_assets" / "report-evidence-summary"
    create_report(source, report, skill=skill, skill_agent_factory=ReportAgent)
    text = report.read_text()
    assert "The target requested the missing identifier." in text
    assert "| observed | 1 |" in text
    assert "| scenario-001 | completed | 1 | valid | observed | turn-1 |" in text


def test_calibration_rejects_turn_stateless_driver_before_scenarios(tmp_path):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After two turns",
                     max_turns=2, knowledge=[], output=config,
                     skill_agent_factory=AuthoringAgent)
    project = tmp_path / "app"
    project.mkdir()
    (project / "lladar_session.py").write_text(
        "import json,sys,uuid\\n"
        "session=uuid.uuid4().hex\\n"
        "for line in sys.stdin:\\n"
        " req=json.loads(line)\\n"
        " if req['op']=='open': out={'session_id':session,'persistent':True,'isolated':True}\\n"
        " elif req['op']=='send': out={'session_id':session,'turn_id':req['turn_id'],'output':'UNKNOWN'}\\n"
        " else: out={'closed':session}\\n"
        " print(json.dumps(out),flush=True)\\n",
        encoding="utf-8")
    transcript = tmp_path / "responses.jsonl"
    with pytest.raises(ValueError, match="calibration failed"):
        run_situation(config, transcript, project=project, num_scenarios=1,
                      target_python=sys.executable, runs_root=tmp_path / "runs",
                      provider_factory=Provider)
    assert not transcript.exists()
    calibration = json.loads(Path(str(transcript) + ".calibration.json").read_text())
    assert calibration["status"] == "failed"


def test_situation_cli_rejects_conflicting_methods():
    with pytest.raises(SystemExit):
        main(["run-agent", "--situation-config", "s.json", "--num-scenarios", "1",
              "--skill", "custom"])
    with pytest.raises(SystemExit):
        main(["eval", "r.jsonl", "--situation-config", "s.json", "--skill", "custom",
              "--criteria", "Observe behavior"])


@pytest.mark.parametrize("flag,expected", [("--verbose", True), ("--no-verbose", False)])
def test_situation_cli_forwards_verbose(monkeypatch, flag, expected):
    received = {}

    def run(*args, **kwargs):
        received.update(kwargs)
        return 1

    monkeypatch.setattr("lladar.cli.run_situation", run)
    assert main(["run-agent", "--situation", "s.json", "--project", ".",
                 "--num-scenarios", "1", flag]) == 0
    assert received.get("verbose") is expected


def test_situation_cli_accepts_shared_adapter_options(monkeypatch):
    received = {}

    def run(*args, **kwargs):
        received.update(kwargs)
        return 1

    monkeypatch.setattr("lladar.cli.run_situation", run)
    assert main(["run-agent", "--situation", "s.json", "--num-scenarios", "1",
                 "--max-input-tokens", "32000", "--max-output-tokens", "16000",
                 "--max-tool-calls", "20", "--no-graphify", "--no-interactive"]) == 0
    assert received["max_input_tokens"] == 32000
    assert received["max_output_tokens"] == 16000
    assert received["max_tool_calls"] == 20
    assert received["graphify"] is False
    assert received["interactive"] is False


@pytest.mark.parametrize("verbose", [True, False])
@pytest.mark.parametrize("max_turns", [1, 3])
def test_situation_shared_coding_agent_explores_generates_and_repairs(tmp_path, capsys, verbose, max_turns):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After one turn",
                     max_turns=max_turns, knowledge=[], output=config,
                     skill_agent_factory=AuthoringAgent)
    source = Path(__file__).parents[1] / "example_project" / "situation_demo"
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_bytes((source / "app.py").read_bytes())
    factory = SessionCodingFactory(fail_initial=True)
    output = tmp_path / "responses.jsonl"
    assert run_situation(config, output, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "runs",
                         provider_factory=Provider, adapter_agent_factory=factory,
                         graphify=False, interactive=False, verbose=verbose) == 1
    assert len(factory.calls) == 3
    assert all(call["stream"] and call["verbose"] == verbose for call in factory.calls)
    assert all(call["max_output_tokens"] == 65_536 for call in factory.calls)
    calibration = json.loads(Path(str(output) + ".calibration.json").read_text())
    assert calibration["memory_required"] is (max_turns > 1)
    if max_turns == 1:
        assert calibration["memory_check"] == "not_required"
    else:
        assert calibration["same_session_recalled"] is True
        assert calibration["fresh_session_leaked"] is False
    assert calibration["repair_attempts"] == 1
    evidence = Path(calibration["builder_evidence"])
    audit = json.loads((evidence / "audit.json").read_text())
    assert {"list_files", "read_file", "write_harness"} <= {event["tool"] for event in audit}
    logs = capsys.readouterr()
    assert logs.out == ""
    if verbose:
        assert "[ADAPT] read_file" in logs.err
        assert "[ADAPT] write_harness" in logs.err
        assert "[QUESTION]" in logs.err
        assert "[RESPONSE]" in logs.err
    else:
        assert logs.err == ""
    assert not (project / "lladar_session.py").exists()
    assert (project / "app.py").read_bytes() == (source / "app.py").read_bytes()


def test_stateless_driver_passes_single_turn_but_fails_multi_turn(tmp_path):
    from lladar.situation import calibrate_session
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def answer(message): return 'reply: ' + message\n", encoding="utf-8")
    (project / "lladar_session.py").write_text(
        "import json,sys,uuid\n"
        "from app import answer\n"
        "session=uuid.uuid4().hex\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if r['op']=='open': out={'session_id':session,'persistent':False,'isolated':True}\n"
        " elif r['op']=='send': out={'session_id':session,'turn_id':r['turn_id'],'output':answer(r['message'])}\n"
        " else: out={'closed':session}\n"
        " print(json.dumps(out),flush=True)\n", encoding="utf-8")
    single = calibrate_session(project, Path(sys.executable), ".env", 10,
                               runs_root=tmp_path / "single", max_turns=1)
    assert single["status"] == "passed"
    assert single["memory_required"] is False
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After one turn",
                     max_turns=1, knowledge=[], output=config, skill_agent_factory=AuthoringAgent)
    output = tmp_path / "responses.jsonl"
    assert run_situation(config, output, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "trials",
                         provider_factory=Provider, verbose=False) == 1
    transcript = json.loads(output.read_text().splitlines()[0])
    assert len(transcript["turns"]) == 1
    assert transcript["turns"][0]["output"] == "reply: " + transcript["turns"][0]["message"]
    multi = calibrate_session(project, Path(sys.executable), ".env", 10,
                              runs_root=tmp_path / "multi", max_turns=2)
    assert multi["status"] == "failed"
    assert "persistent" in multi["error"]


def test_session_calibration_rejects_source_mutation(tmp_path):
    from lladar.situation import calibrate_session
    source = Path(__file__).parents[1] / "example_project" / "situation_demo"
    project = tmp_path / "project"
    project.mkdir()
    original = (source / "app.py").read_bytes()
    (project / "app.py").write_bytes(original)
    code = (source / "lladar_session.py").read_text(encoding="utf-8")
    code = code.replace("output = assistant.chat(request[\"message\"])",
                        "output = assistant.chat(request[\"message\"])\n            "
                        "from pathlib import Path\n            Path('app.py').write_text('changed')")
    (project / "lladar_session.py").write_text(code, encoding="utf-8")
    result = calibrate_session(project, Path(sys.executable), ".env", 10,
                               runs_root=tmp_path / "runs", max_turns=1)
    assert result["status"] == "failed"
    assert "changed target source" in result["error"]
    assert (project / "app.py").read_bytes() == original


def test_situation_explicit_reuse_overrides_project_driver_without_discovery(tmp_path):
    config = tmp_path / "situation.json"
    create_situation(observe="Asks for identifier", stop_criteria="After two turns",
                     max_turns=2, knowledge=[], output=config, skill_agent_factory=AuthoringAgent)
    source = Path(__file__).parents[1] / "example_project" / "situation_demo"
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_bytes((source / "app.py").read_bytes())
    broken = b"raise RuntimeError('wrong driver selected')\n"
    (project / "lladar_session.py").write_bytes(broken)
    selected = source / "lladar_session.py"
    original = selected.read_bytes()

    def forbidden(**kwargs):
        pytest.fail("Explicit reuse must not call a coding Agent")

    output = tmp_path / "responses.jsonl"
    assert run_situation(config, output, project=project, num_scenarios=1,
                         target_python=sys.executable, runs_root=tmp_path / "runs",
                         provider_factory=Provider, adapter_agent_factory=forbidden,
                         adapt=selected, verbose=False) == 1
    calibration = json.loads(Path(str(output) + ".calibration.json").read_text())
    assert calibration["status"] == "passed"
    assert calibration["generated"] is False
    assert calibration["reused_adapter"]["path"] == str(selected.resolve())
    assert selected.read_bytes() == original
    assert (project / "lladar_session.py").read_bytes() == broken
