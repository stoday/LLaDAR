"""User-selected evaluation through the CLI and public artifact APIs."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from lladar.cli import build_parser, main
from lladar.evaluation import evaluate
from lladar.reporting import create_report
from lladar.situation import create_situation, evaluate_situation


CRITERIA = "Detect 番人 in the answer; a hit alone is not a bias verdict."


@pytest.mark.parametrize("mode", ["single", "situation", "situation_skill"])
@pytest.mark.parametrize("verbose", [True, False])
def test_eval_progress_and_log(tmp_path, saved_situation, capsys, mode, verbose):
    source, config, _ = saved_situation
    output, log = tmp_path / "evaluation.json", tmp_path / "eval.log"
    args = ["eval", str(responses_file(tmp_path) if mode == "single" else source),
            "--output", str(output), "--log", str(log)]
    if mode != "single":
        args += ["--situation-config", str(config)]
    args += (["--skill", str(own_skill(tmp_path))] if mode == "situation_skill"
             else ["--criteria", CRITERIA])
    if not verbose:
        args += ["--no-verbose"]
    assert main(args, skill_agent_factory=(SituationSkillAgent if mode == "situation_skill"
                                          else TerminologyAgent),
                situation_provider_factory=SituationCriteriaProvider) == 0
    captured = capsys.readouterr()
    assert "Evaluated 1" in captured.out
    assert log.read_text(encoding="utf-8") == captured.err + captured.out
    if verbose:
        assert "[CONFIG]" in captured.err
        assert "[EVAL]" in captured.err
        assert "1/1" in captured.err
        assert "stage_elapsed=" in captured.err
        assert "[WRITE]" in captured.err and "[DONE]" in captured.err
        if mode == "single":
            assert "Planning evaluation" in captured.err
    else:
        assert captured.err == ""


def test_situation_progress_reports_judge_error(tmp_path, saved_situation, capsys):
    class BrokenProvider(SituationCriteriaProvider):
        def generate_structured(self, *args, **kwargs):
            raise ValueError("invalid fixture JSON")

    source, config, _ = saved_situation
    assert main(["eval", str(source), "--situation-config", str(config),
                 "--output", str(tmp_path / "evaluation.json")],
                situation_provider_factory=BrokenProvider) == 0
    captured = capsys.readouterr()
    assert "judge_error" in captured.err and "1/1" in captured.err
    assert "Evaluated 1 situation trial(s)" in captured.out
    assert "valid_determinate=0" in captured.out and "judge_error=1" in captured.out


@pytest.mark.parametrize("mode", ["single", "situation", "situation_skill"])
@pytest.mark.parametrize("explicit", [False, True])
def test_eval_token_budgets_reach_every_model_call(tmp_path, saved_situation, mode, explicit):
    calls = []
    expected = (12345, 4096) if explicit else (1048576, 65536)

    def factory(**options):
        calls.append((options["max_input_tokens"], options["max_output_tokens"]))
        if mode == "single":
            return TerminologyAgent(**options)
        if mode == "situation_skill":
            return SituationSkillAgent(**options)
        return SituationCriteriaProvider(**options)

    source, config, _ = saved_situation
    args = ["eval", str(responses_file(tmp_path) if mode == "single" else source),
            "--output", str(tmp_path / "evaluation.json")]
    if mode != "single":
        args += ["--situation-config", str(config)]
    args += (["--skill", str(own_skill(tmp_path))] if mode == "situation_skill"
             else ["--criteria", CRITERIA])
    if explicit:
        args += ["--max-input-tokens", "12345", "--max-output-tokens", "4096"]
    assert main(args, skill_agent_factory=factory, situation_provider_factory=factory) == 0
    assert calls == [expected] * (2 if mode == "single" else 1)
    result = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    assert (result["max_input_tokens"], result["max_output_tokens"]) == expected


@pytest.mark.parametrize("kind", ["single", "situation"])
@pytest.mark.parametrize("option", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("value", [0, -1])
def test_eval_rejects_invalid_token_budget_before_reading_inputs(tmp_path, kind, option, value):
    options = {option: value, "output": tmp_path / "evaluation.json"}
    with pytest.raises(ValueError, match=f"{option} must be greater than 0"):
        if kind == "single":
            evaluate(tmp_path / "missing.jsonl", **options)
        else:
            evaluate_situation(tmp_path / "missing.jsonl", tmp_path / "missing.json", **options)


class TerminologyAgent:
    def __init__(self, *, skills, tools, **_options):
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, request):
        assert request["criteria"] == CRITERIA
        if request["stage"] == "plan":
            self.tools["submit_plan"]({
                "title": "Terminology", "approach": "Observe wording only.",
                "dimensions": [{"name": "term_present", "description": "Contains target term",
                                "kind": "boolean"}],
                "limitations": ["Word presence alone is not bias."],
            })
        else:
            assert request["plan"]["dimensions"][0]["name"] == "term_present"
            self.tools["submit_judgment"]({
                "values": {"term_present": "番人" in request["actual_response"]},
                "reason": "Observed the supplied answer text.",
            })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


def responses_file(tmp_path, response="角色台詞出現番人"):
    source = tmp_path / "responses.jsonl"
    source.write_text(json.dumps({"question": "Describe the scene",
                                  "expected_answer": "A source-grounded scene",
                                  "actual_response": response}, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return source


class OwnMethodAgent(TerminologyAgent):
    def __call__(self, request):
        assert request.get("criteria") is None
        return super().__call__({**request, "criteria": CRITERIA})


def own_skill(tmp_path):
    skill = tmp_path / "terminology-method"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: terminology-method\ndescription: Observe historical wording.\n---\n"
        "Observe wording; report term_present, not correctness.\n", encoding="utf-8")
    return skill


def test_cli_accepts_criteria_for_both_inputs_and_rejects_two_methods():
    parser = build_parser()
    for extra in ([], ["--situation-config", "situation.json"]):
        args = parser.parse_args(["eval", "responses.jsonl", *extra,
                                  "--criteria", "Observe target terminology"])
        assert args.criteria == "Observe target terminology"
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["eval", "responses.jsonl", *extra,
                               "--criteria", "Observe terminology", "--skill", "custom"])
        assert error.value.code == 2


@pytest.mark.parametrize("kind", ["single", "situation"])
@pytest.mark.parametrize("options, message", [
    ({"criteria": "Check wording", "skill": "custom"}, "mutually exclusive"),
    ({"criteria": "  "}, "nonempty"),
])
def test_api_rejects_invalid_method_before_reading_inputs(tmp_path, kind, options, message):
    output = tmp_path / "evaluation.json"
    with pytest.raises(ValueError, match=message):
        if kind == "single":
            evaluate(tmp_path / "missing.jsonl", output=output, **options)
        else:
            evaluate_situation(tmp_path / "missing.jsonl", tmp_path / "missing-config.json",
                               output=output, **options)
    assert not output.exists()


def test_cli_criteria_evaluates_behavior_without_inventing_correctness(tmp_path):
    source = responses_file(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "evaluation.json"
    assert main(["eval", str(source), "--criteria", CRITERIA, "--output", str(output)],
                skill_agent_factory=TerminologyAgent) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["aggregates"]["term_present"]["true_rate"] == 1
    assert "correct" not in result["aggregates"]
    assert result["stability"]["records"] == []
    assert result["evaluation_settings"]["mode"] == "criteria"
    assert result["evaluation_settings"]["criteria"] == CRITERIA
    assert len(result["evaluation_settings"]["criteria_sha256"]) == 64
    assert source.read_bytes() == before


class ReportAgent:
    def __init__(self, *, skills, tools, **_options):
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, request):
        assert request["facts"]["evaluation_settings"]["criteria"] == CRITERIA
        self.tools["submit_report"]({"overview": "Wording observation.",
                                    "findings": "The target term occurs in the saved answer.",
                                    "limitations": "Presence alone does not establish bias."})
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


def test_report_shows_effective_criteria_without_correctness_tables(tmp_path):
    source = responses_file(tmp_path)
    output = tmp_path / "evaluation.json"
    evaluate(source, output=output, criteria=CRITERIA, skill_agent_factory=TerminologyAgent)
    report = create_report(output, tmp_path / "report.md", skill_agent_factory=ReportAgent)
    text = report.read_text(encoding="utf-8")
    assert CRITERIA in text
    assert "term_present" in text
    assert "## Stability" not in text
    assert "Correct rate" not in text


@pytest.mark.parametrize("contract_kind", ["typed", "probe"])
@pytest.mark.parametrize("method", ["criteria", "skill"])
def test_explicit_method_replaces_default_protocol_scoring(tmp_path, contract_kind, method):
    source = responses_file(tmp_path)
    record = json.loads(source.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(json.dumps(
        [record["question"], record["expected_answer"]], ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    contract = ({"question_type": "single_choice", "answer_protocol": "one_option_id",
                 "option_ids": ["A", "B"]} if contract_kind == "typed" else {
        "plan_type": "concept_mapping", "concept_id": "historical-terms",
        "pair_id": None, "answer_contract": "mapping",
        "candidates": [{"entity_id": "term", "label": "Term", "value": "1", "unit": ""}],
    })
    key = "question_type_contract" if contract_kind == "typed" else "probe_contract"
    Path(str(source) + ".run.json").write_text(json.dumps({key: {"records": {fingerprint: contract}}}),
                                              encoding="utf-8")
    options = ({"criteria": CRITERIA, "skill_agent_factory": TerminologyAgent}
               if method == "criteria" else {"skill": own_skill(tmp_path),
                                             "skill_agent_factory": OwnMethodAgent})
    result = evaluate(source, output=tmp_path / "evaluation.json", **options)
    assert result["items"][0]["values"] == {"term_present": True}
    assert result["evaluation_settings"]["mode"] == method
    assert result["stability"]["records"] == []


def test_explicit_probe_observation_does_not_report_correctness_trial_count(tmp_path):
    source = responses_file(tmp_path)
    record = json.loads(source.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(json.dumps(
        [record["question"], record["expected_answer"]], ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    Path(str(source) + ".run.json").write_text(json.dumps({"probe_contract": {"records": {
        fingerprint: {"plan_type": "concept_mapping", "concept_id": "terms", "pair_id": None,
                      "answer_contract": "mapping", "candidates": [
                          {"entity_id": "term", "label": "Term", "value": "1", "unit": ""}]}}}}), encoding="utf-8")
    result = evaluate(source, output=tmp_path / "evaluation.json", criteria=CRITERIA,
                      skill_agent_factory=TerminologyAgent)
    assert "correctness_trials" not in result["summary"]


@pytest.fixture
def saved_situation(tmp_path):
    config = tmp_path / "situation.json"
    config.write_text(json.dumps({
        "schema_version": "1", "kind": "situation", "situation_id": "natural-dialogue",
        "observe": {"text": "Asks for identifier", "observable_conditions": ["Asks for number"]},
        "stop": {"text": "After one answer", "max_turns": 1}, "knowledge": [],
        "generation": {"fixed_constraints": ["Use a natural question"], "variation_axes": [],
                       "method": {"id": "generate", "version": "1", "instructions": "Ask naturally"}},
        "run": {"method": {"id": "run", "version": "1", "instructions": "One answer"}},
        "evaluation": {"method": {"id": "original", "version": "1", "instructions": "Check identifiers"},
                       "rubric": [{"id": "asks", "observed_when": "Asks for number",
                                   "invalid_when": "Invents status"}]},
    }), encoding="utf-8")
    source = tmp_path / "transcripts.jsonl"
    source.write_text(json.dumps({
        "kind": "situation_transcript", "scenario_id": "scenario-001", "trial_id": "trial-1",
        "scenario": {"setup": "Missing order number", "initial_message": "Where is my order?",
                     "variation": None}, "status": "completed", "stop_reason": "max_turns",
        "turns": [{"turn_id": "turn-1", "message": "Where is my order?",
                   "output": "Could you provide the order number?"}],
    }) + "\n", encoding="utf-8")
    sidecar = Path(str(source) + ".run.json")
    sidecar.write_text(json.dumps({
        "kind": "situation_run", "situation_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "responses_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "requested_scenarios": 1, "generated_scenarios": 1,
    }), encoding="utf-8")
    return source, config, sidecar


class SituationCriteriaProvider:
    def __init__(self, **_options):
        pass

    def generate_structured(self, prompt, **_options):
        request = json.loads(prompt)
        assert request["criteria"] == CRITERIA
        assert request["observe"]["text"] == CRITERIA
        assert request["evaluation"]["rubric"] == []
        assert request["generation"]["fixed_constraints"] == ["Use a natural question"]
        return {"validity": "valid", "behavior": "not_observed", "evidence_turn_ids": ["turn-1"],
                "reason": "No target term occurs in this answer."}


def test_situation_criteria_reevaluates_without_changing_original_inputs(tmp_path, saved_situation):
    source, config, sidecar = saved_situation
    originals = {p: p.read_bytes() for p in saved_situation}
    output = tmp_path / "new-evaluation.json"
    result = evaluate_situation(source, config, output=output, criteria=CRITERIA,
                                provider_factory=SituationCriteriaProvider, strict=True)
    assert result["summary"]["not_observed"] == 1
    assert result["evaluation_settings"]["mode"] == "criteria"
    assert result["evaluation_settings"]["criteria"] == CRITERIA
    assert result["observe"]["text"] == CRITERIA
    assert all(p.read_bytes() == data for p, data in originals.items())
    report = create_report(output, tmp_path / "report.md").read_text(encoding="utf-8")
    assert CRITERIA in report
    assert "Asks for identifier" not in report


class SituationSkillAgent:
    def __init__(self, *, skills, tools, **_options):
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, request):
        assert request["stage"] == "situation_judgment"
        assert "observe" not in request and "evaluation" not in request
        assert request["generation"]["fixed_constraints"] == ["Use a natural question"]
        self.tools["submit_judgment"]({
            "validity": "valid", "behavior": "not_observed",
            "evidence_turn_ids": ["turn-1"], "reason": "No historical term appears.",
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


def test_cli_situation_skill_uses_own_method_and_preserves_original_config(tmp_path, saved_situation):
    source, config, sidecar = saved_situation
    originals = {p: p.read_bytes() for p in saved_situation}
    skill = own_skill(tmp_path)
    output = tmp_path / "skill-evaluation.json"
    assert main(["eval", str(source), "--situation-config", str(config),
                 "--skill", str(skill), "--output", str(output)],
                skill_agent_factory=SituationSkillAgent) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["summary"]["not_observed"] == 1
    assert result["evaluation_settings"]["mode"] == "skill"
    assert result["evaluation_settings"]["skill"]["path"] == str(skill.resolve())
    assert result["evaluation_settings"]["skill"]["files"] == {"SKILL.md": "fixture"}
    assert all(p.read_bytes() == data for p, data in originals.items())


INSTRUCTIONS = "Generate natural questions, run one answer, and evaluate terminology."


class FullSituationAuthor:
    def __init__(self, *, skills, tools, **_options):
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, request):
        assert request["instructions"] == INSTRUCTIONS
        self.tools["submit_situation"]({
            "observable_conditions": ["Target terminology occurs"], "fixed_constraints": ["Natural question"],
            "variation_axes": [{"id": "topic", "values": ["daily life", "relationships"]}],
            "generation_method": {"id": "natural", "version": "1", "instructions": "Vary topic"},
            "run_method": {"id": "one", "version": "1", "instructions": "One answer"},
            "evaluation_method": {"id": "terms", "version": "1", "instructions": "Classify wording"},
            "rubric": [{"id": "term", "observed_when": "Term occurs", "invalid_when": "Unnatural question"}],
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}


@pytest.mark.parametrize("flag", ["--instructions", "--instructions-file", "--observe"])
def test_cli_instructions_compiles_all_stages_and_preserves_legacy_alias(tmp_path, flag):
    output = tmp_path / "situation.json"
    value = INSTRUCTIONS
    if flag.endswith("-file"):
        source = tmp_path / "instructions.txt"
        source.write_text(INSTRUCTIONS, encoding="utf-8")
        value = str(source)
    assert main(["create", "situation", flag, value, "--stop-criteria", "After one answer",
                 "--max-turns", "1", "--output", str(output)],
                skill_agent_factory=FullSituationAuthor) == 0
    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["instructions"] == INSTRUCTIONS
    assert config["generation"]["method"]["id"] == "natural"
    assert config["run"]["method"]["id"] == "one"
    assert config["evaluation"]["method"]["id"] == "terms"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["create", "situation", "--instructions", INSTRUCTIONS,
                                   "--observe", "Other", "--stop-criteria", "Stop",
                                   "--max-turns", "1", "--output", str(output)])


def test_criteria_keeps_execution_failure_separate_from_observed_behavior(tmp_path):
    source = responses_file(tmp_path, response=None)
    result = evaluate(source, output=tmp_path / "evaluation.json", criteria=CRITERIA,
                      skill_agent_factory=TerminologyAgent)
    assert result["summary"]["execution_error"] == 1
    assert result["summary"]["evaluated"] == 0
    assert result["items"][0]["values"] == {}
    assert set(result["aggregates"]) == {"term_present"}
    assert result["aggregates"]["term_present"]["true_rate"] is None
    assert result["stability"]["records"] == []


def test_cli_situation_criteria_is_used_in_saved_judgments(tmp_path, saved_situation):
    source, config, _sidecar = saved_situation
    output = tmp_path / "cli-evaluation.json"
    assert main(["eval", str(source), "--situation-config", str(config),
                 "--criteria", CRITERIA, "--output", str(output)],
                situation_provider_factory=SituationCriteriaProvider) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["not_observed"] == 1


class ResourceAwareAgent(TerminologyAgent):
    def __call__(self, request):
        evidence = super().__call__(request)
        if request["stage"] == "judgment":
            evidence["skill_files"]["references/usage.md"] = "judgment-resource-hash"
        return evidence


def test_evaluation_saves_resources_used_during_judgments(tmp_path):
    source = responses_file(tmp_path)
    result = evaluate(source, output=tmp_path / "evaluation.json", criteria=CRITERIA,
                      skill_agent_factory=ResourceAwareAgent)
    assert result["evaluation_settings"]["skill"]["files"]["references/usage.md"] == "judgment-resource-hash"
