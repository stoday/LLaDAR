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


class AutoProvider(Provider):
    def generate_structured(self, prompt, **kwargs):
        request = json.loads(prompt)
        if request["task"].startswith("Write a standalone"):
            driver = Path(__file__).parents[1] / "example_project" / "situation_demo" / "lladar_session.py"
            return {"code": driver.read_text(encoding="utf-8")}
        return super().generate_structured(prompt, **kwargs)


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
                         provider_factory=AutoProvider) == 1
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
        main(["eval", "r.jsonl", "--situation-config", "s.json", "--skill", "custom"])
