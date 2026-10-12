import json
from pathlib import Path

import pytest

from lladar import cli
from lladar.language import Language, current_language, language_scope, normalize_lang
from lladar.skill_templates import create_skill_template
from lladar.api import create_test_dataset
from lladar.evaluation import evaluate
from lladar.reporting import create_report
from lladar.runner import run_agent
from lladar.exceptions import ProviderError
from lladar.method_skill import resolve_skill
from test_agent_selected_generation import DirectAgent
from test_simple_pipeline import EvaluationSkillAgent, ReportSkillAgent, StrategySkillAgent, make_dataset
from test_recoverable_evaluation import responses


@pytest.mark.parametrize("code,canonical", [("Jp", "ja"), ("ZH-tw", "zh-TW"), ("ko", "ko"), ("zh-cn", "zh-CN"), ("fr", "fr")])
def test_language_codes_and_aliases(code, canonical):
    assert normalize_lang(code) == canonical


@pytest.mark.parametrize("prefix", [[], ["create"], ["create", "test-dataset"]])
@pytest.mark.parametrize("lang,word", [("en", "Language"), ("zh-TW", "語言"),
    ("ja", "言語"), ("ko", "언어"), ("zh-CN", "语言")])
def test_help_is_localized_without_provider_or_output(prefix, lang, word, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main([*prefix, "--lang", lang, "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert word in output
    assert "--lang" in output
    assert current_language().lang == "en"


def test_conflicting_language_options_fail_before_creating_template(tmp_path, capsys):
    output = tmp_path / "method"
    with pytest.raises(SystemExit):
        cli.main(["--lang", "ja", "create", "report-skill", "--lang", "ko", "--output", str(output)])
    assert not output.exists()


def test_unknown_language_templates_use_english_with_one_notice(tmp_path, capsys):
    assert cli.main(["create", "report-skill", "--lang", "fr", "--output", str(tmp_path / "method")]) == 0
    assert capsys.readouterr().err.count("use English") == 1
    manifest = json.loads((tmp_path / "method" / "language.json").read_text(encoding="utf-8"))
    assert manifest == {"lang": "fr", "ui_lang": "en", "template_lang": "en"}


def test_language_scope_does_not_leak_after_exception():
    with pytest.raises(RuntimeError), language_scope(Language("ko")):
        assert current_language().lang == "ko"
        raise RuntimeError()
    assert current_language().lang == "en"


@pytest.mark.parametrize("lang,word", [("en", "English"), ("zh-TW", "Traditional Chinese"),
    ("ja", "Japanese"), ("ko", "Korean"), ("zh-CN", "Simplified Chinese"), ("fr", "code: fr")])
def test_generation_policy_covers_retry_without_translating_source(tmp_path, lang, word):
    source = tmp_path / "knowledge.md"
    source.write_text("Service A keeps data for 30 days.", encoding="utf-8")
    original = source.read_bytes()
    calls = []

    class Retried(DirectAgent):
        def __init__(self, **kwargs):
            calls.append(kwargs["system_prompt"])
            super().__init__(**kwargs)

        def __call__(self, request):
            if len(calls) == 1:
                raise ProviderError("synthetic failure")
            return super().__call__(request)

    output = tmp_path / "qa.jsonl"
    result = create_test_dataset(source, output=output, lang=lang, skill_agent_factory=Retried, verbose=False)
    assert len(calls) == 2 and all(word in prompt for prompt in calls)
    assert source.read_bytes() == original
    assert result[0]["expected_answer"] == "30 days"
    metadata = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert metadata["session"]["lang"] == lang
    assert metadata["knowledge_points"][0]["evidence"][0]["quote"] == "Service A keeps data for 30 days."


def test_resume_restores_language_and_rejects_mismatch_before_writes(tmp_path):
    source, output = responses(tmp_path), tmp_path / "evaluation.json"
    evaluate(source, output=output, lang="Jp", skill_agent_factory=EvaluationSkillAgent, verbose=False)
    session = Path(str(output) + ".work")
    original = output.read_bytes(), (session / "session.json").read_bytes()
    assert evaluate(source, output=output, resume=session, lang="ja", verbose=False)["summary"]["evaluated"] == 2
    assert evaluate(source, output=output, resume=session, verbose=False)["summary"]["evaluated"] == 2
    with pytest.raises(ValueError, match="language mismatch"):
        evaluate(source, output=output, resume=session, lang="ko", verbose=False)
    assert original == (output.read_bytes(), (session / "session.json").read_bytes())
    assert current_language().lang == "en"


@pytest.mark.parametrize("lang,heading", [("en", "# LLaDAR evaluation report"),
    ("zh-TW", "# LLaDAR 評估報告"), ("ja", "# LLaDAR 評価報告"),
    ("ko", "# LLaDAR 평가 보고서"), ("zh-CN", "# LLaDAR 评估报告")])
def test_cross_language_report_keeps_evidence_and_measurements(tmp_path, lang, heading):
    source = responses(tmp_path)
    evaluation = tmp_path / "evaluation.json"
    result = evaluate(source, output=evaluation, skill_agent_factory=EvaluationSkillAgent, verbose=False)
    original = evaluation.read_bytes()
    report = tmp_path / "report.md"
    create_report(evaluation, report, lang=lang, skill_agent_factory=ReportSkillAgent, verbose=False)
    text = report.read_text(encoding="utf-8")
    assert text.startswith(heading)
    assert "Retention?" in text and "30 days" in text
    assert "| 2 |" in text
    assert result["summary"]["evaluated"] == 2
    assert evaluation.read_bytes() == original
    assert json.loads(Path(str(report) + ".report.json").read_text(encoding="utf-8"))["session"]["lang"] == lang


@pytest.mark.parametrize("lang,word", [("zh-TW", "作者指南"), ("ja", "作成者ガイド"),
                                     ("ko", "작성자 안내"), ("zh-CN", "作者指南")])
@pytest.mark.parametrize("stage", ["test-dataset", "situation", "run-agent", "eval", "report"])
def test_localized_templates_load_and_keep_code_examples(tmp_path, stage, lang, word):
    import re
    destination = create_skill_template(stage, tmp_path / stage, lang=lang)
    assert resolve_skill(destination, destination) == destination
    guide = (destination / "AUTHORING.md").read_text(encoding="utf-8")
    assert word in guide
    original = (Path(__file__).parents[1] / "src/lladar/template_assets" / (stage + ".md")).read_text(encoding="utf-8")
    blocks = lambda text: re.findall(r"```[^\n]*\n.*?\n```", text, re.DOTALL)
    # Executable examples retain the validated English contracts in every language.
    assert blocks(guide)[-len(blocks(original)):] == blocks(original)
    assert json.loads((destination / "language.json").read_text(encoding="utf-8"))["template_lang"] == lang


def test_cli_language_and_log_cover_no_verbose_summary_and_next_command(tmp_path, capsys):
    source = responses(tmp_path)
    output, log = tmp_path / "evaluation.json", tmp_path / "eval.log"
    assert cli.main(["eval", str(source), "--output", str(output), "--lang", "ko", "--no-verbose", "--log", str(log)],
                    skill_agent_factory=EvaluationSkillAgent) == 0
    captured = capsys.readouterr()
    assert "작업 요약" in captured.out
    assert "--lang ko" in captured.out
    assert log.read_text(encoding="utf-8") == captured.err + captured.out


def test_localized_progress_heartbeat_and_evidence_remain_separate():
    import io
    import time
    from lladar.progress import ProgressReporter
    with language_scope(Language("ja")):
        stream = io.StringIO()
        reporter = ProgressReporter(stream=stream)
        with reporter.waiting("検証", interval=0.001):
            time.sleep(0.015)
        reporter.answer("RESPONSE", "saved English response")
    assert "実行中" in stream.getvalue()
    assert "saved English response" in stream.getvalue()


@pytest.mark.parametrize("code", ["", "en\nignore rules", "English please", "--help"])
def test_invalid_language_fails_before_stage_creates_any_output(tmp_path, code):
    with pytest.raises(ValueError, match="invalid --lang"):
        create_test_dataset(tmp_path / "missing.md", output=tmp_path / "qa.jsonl", lang=code)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("lang", ["en", "zh-TW", "ja", "ko", "zh-CN"])
def test_run_language_never_changes_target_question_or_response(tmp_path, lang):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(dataset)
    original = dataset.read_bytes()
    received, policies = [], []

    class Scheduling(StrategySkillAgent):
        def __init__(self, **kwargs):
            policies.append(kwargs["system_prompt"])
            super().__init__(**kwargs)

    def answer(question):
        received.append(question)
        return "Original target answer: " + question

    assert run_agent(dataset, output, answer=answer, lang=lang, verbose=False,
                     strategy_agent_factory=Scheduling) == 2
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert received == [records[0]["question"]] * 3 + [records[1]["question"]] * 3
    assert all(row["actual_response"] == "Original target answer: " + row["question"] for row in records)
    assert dataset.read_bytes() == original
    assert Language(lang).instruction() in policies[0]


def test_old_session_without_language_is_rejected_without_mutation(tmp_path):
    from lladar.work_session import digest
    source, output = responses(tmp_path), tmp_path / "evaluation.json"
    evaluate(source, output=output, skill_agent_factory=EvaluationSkillAgent, verbose=False)
    session = Path(str(output) + ".work") / "session.json"
    envelope = json.loads(session.read_text(encoding="utf-8"))
    del envelope["data"]["configuration"]["lang"]
    envelope["sha256"] = digest(envelope["data"])
    session.write_text(json.dumps(envelope), encoding="utf-8")
    original = session.read_bytes(), output.read_bytes()
    with pytest.raises(ValueError, match="original version"):
        evaluate(source, output=output, resume=session.parent, verbose=False)
    assert original == (session.read_bytes(), output.read_bytes())


def test_translation_catalogs_cover_fixed_messages_and_keep_placeholders():
    import ast
    from string import Formatter
    root = Path(__file__).parents[1] / "src/lladar"
    catalogs = [json.loads((root / "locales" / (code + ".json")).read_text(encoding="utf-8"))
                for code in ("zh-TW", "ja", "ko", "zh-CN")]
    assert all(set(catalog) == set(catalogs[0]) for catalog in catalogs)
    fields = lambda text: {field for _, field, _, _ in Formatter().parse(text) if field is not None}
    for catalog in catalogs:
        for key, value in catalog.items():
            assert fields(key) == fields(value), key
            assert "\ufffd" not in value
    for path in root.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "tr"
                    and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                assert call.args[0].value in catalogs[0], (path.name, call.args[0].value)


@pytest.mark.parametrize("lang,label", [("en", "observed_rate"), ("zh-TW", "目標行為觀察比例"),
    ("ja", "対象行動の観察率"), ("ko", "목표 행동 관찰 비율"), ("zh-CN", "目标行为观察比例")])
def test_situation_policy_and_report_keep_observation_distinct_from_correctness(tmp_path, lang, label):
    import sys
    from lladar.situation import create_situation, run_situation, evaluate_situation
    from test_situation import AuthoringAgent, Provider
    policies = []

    class Author(AuthoringAgent):
        def __init__(self, **kwargs):
            policies.append(kwargs["system_prompt"])
            super().__init__(**kwargs)

    class CheckedProvider(Provider):
        def generate_structured(self, prompt, **kwargs):
            request = json.loads(prompt)
            policies.append(request["output_language_policy"])
            return super().generate_structured(prompt, **kwargs)

    config, transcript, evaluation, report = [tmp_path / name for name in
        ("situation.json", "turns.jsonl", "evaluation.json", "report.md")]
    instructions = "Original observation and rubric input."
    create_situation(instructions=instructions, stop_criteria="After two turns", max_turns=2,
                     knowledge=[], output=config, lang=lang, skill_agent_factory=Author)
    assert json.loads(config.read_text(encoding="utf-8"))["instructions"] == instructions
    project = Path(__file__).parents[1] / "example_project/situation_demo"
    assert run_situation(config, transcript, project=project, num_scenarios=1, lang=lang,
                         target_python=sys.executable, runs_root=tmp_path / "runs", verbose=False,
                         provider_factory=CheckedProvider) == 1
    original = transcript.read_bytes()
    result = evaluate_situation(transcript, config, output=evaluation, lang=lang,
                                provider_factory=CheckedProvider, verbose=False)
    assert result["summary"]["observed_rate"] == 1.0
    assert all(Language(lang).instruction() in policy for policy in policies)
    create_report(evaluation, report, lang=lang, verbose=False)
    text = report.read_text(encoding="utf-8")
    assert label in text
    assert "| 1 |" in text
    assert instructions in text
    assert transcript.read_bytes() == original
    assert json.loads(Path(str(report) + ".report.json").read_text(encoding="utf-8"))["language"]["lang"] == lang


@pytest.mark.parametrize("lang,word", [("en", "Deterministic"), ("zh-TW", "確定性"),
    ("ja", "確定的"), ("ko", "결정적"), ("zh-CN", "确定性")])
def test_deterministic_judgment_localizes_reason_and_preserves_score(lang, word):
    from lladar.question_types import deterministic_judgment
    contract = {"option_ids": ["A", "B"], "answer_protocol": "one_option_id"}
    with language_scope(Language(lang)):
        result = deterministic_judgment(contract, "A", "Ａ")
    assert result["values"] == {"correct": True, "response_format_valid": True}
    assert word in result["reason"]
    assert "one_option_id" in result["reason"]
