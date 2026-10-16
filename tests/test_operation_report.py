from pathlib import Path
import json

import pytest

from lladar import cli
from lladar.exceptions import ProviderError
from lladar.interfaces import NeedsConfirmation
from lladar.skill_templates import create_skill_template
from test_cli_output import WORKFLOWS


def reports(root):
    return sorted(root.rglob("*.operation*.md"))


@pytest.mark.parametrize("argv,operation,filename", WORKFLOWS)
def test_every_workflow_reports_real_artifacts(tmp_path, monkeypatch, argv, operation, filename):
    monkeypatch.chdir(tmp_path)
    def execute(*args, **kwargs):
        output = Path(kwargs.get("output", args[1] if len(args) > 1 else "unused"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("private answer", encoding="utf-8")
        if operation == "create_test_dataset":
            Path(str(output) + ".generation.json").write_text(json.dumps({"status": "ready", "dataset": {"lines": []}}))
            return []
        if operation == "evaluate":
            return {"summary": {"evaluated": 1, "execution_error": 0, "judge_error": 0}}
        if operation == "evaluate_situation":
            return {"items": [], "summary": dict.fromkeys(["valid_determinate", "judge_error", "execution_error", "invalid", "indeterminate"], 0)}
        return output if operation == "create_report" else 1
    monkeypatch.setattr(cli, operation, execute)
    flags = [] if operation == "create_situation" else ["--no-verbose"]
    assert cli.main([*argv, "--output", "results", *flags]) == 0
    report, = reports(tmp_path)
    text = report.read_text(encoding="utf-8")
    assert report.parent == tmp_path / "results"
    assert "Status: Completed\n" in text
    assert "Exit code: 0" in text
    assert "— New" in text
    assert "private answer" not in text
    assert "Next stage:" in text or "Open the output files" in text


@pytest.mark.parametrize("stage", ["test-dataset", "situation", "run-agent", "eval", "report"])
def test_templates_report_outside_managed_directory(tmp_path, monkeypatch, stage):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["create", stage + "-skill"]) == 0
    report, = reports(tmp_path)
    assert report == tmp_path / "lladar-skills" / (stage + ".operation.md")
    assert {p.name for p in (tmp_path / "lladar-skills" / stage).iterdir()} == {"SKILL.md", "AUTHORING.md", "language.json"}


@pytest.mark.parametrize("error,code,status", [(ProviderError("secret-API-key"), 2, "Failed"), (KeyboardInterrupt(), 130, "Cancelled"), (NeedsConfirmation(Path("evidence")), 3, "Needs confirmation"), (ValueError("secret-criteria"), 2, "Failed")])
def test_failures_preserve_exit_code_old_files_and_private_diagnostics(tmp_path, monkeypatch, error, code, status):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "evaluation.json"
    output.write_text("old private output")
    error.work_session_hint = {"path": str(output) + ".work", "persistence_failed": False, "output": str(output)}
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, "evaluate", fail)
    assert cli.main(["eval", "responses.jsonl", "--output", str(output), "--criteria", "secret prompt", "--model", "https://secret:model@host", "--force"]) == code
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert f"Status: {status}" in text
    assert f"Exit code: {code}" in text
    assert "— Existing" in text
    assert "--resume SESSION_PATH" in text
    for private in ["secret-API-key", "secret-criteria", "secret prompt", "https://secret:model@host", "old private output"]:
        assert private not in text
    assert output.read_text(encoding="utf-8") == "old private output"


def test_unexpected_exception_is_reported_and_propagated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def fail(*args, **kwargs):
        raise TypeError("private traceback detail")
    monkeypatch.setattr(cli, "evaluate", fail)
    with pytest.raises(TypeError, match="private traceback detail"):
        cli.main(["eval", "responses.jsonl"])
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "Status: Failed" in text
    assert "private traceback detail" not in text


@pytest.mark.parametrize("argv,stage", [(["eval"], "eval"), (["eval", "input", "--unknown"], "eval"), (["create", "situation"], "situation"), (["run-agent"], "run-agent"), (["eval", "input", "--lang", "invalid code"], "eval")])
def test_argument_errors_report_identified_stage(tmp_path, monkeypatch, argv, stage):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    report, = reports(tmp_path)
    assert f"Stage: `{stage}`" in report.read_text(encoding="utf-8")
    assert "Failed" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("argv", [["--help"], ["--version"], ["create", "situation", "--help"], ["unknown-command"], ["--log", "eval"]])
def test_help_version_unidentified_command_do_not_report(tmp_path, monkeypatch, argv):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert reports(tmp_path) == []


def test_collision_even_force_preserves_history_and_log_contains_location(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = ["create", "eval-skill", "--output", "methods", "--lang", "zh-TW"]
    assert cli.main(args) == 0
    original = (tmp_path / "methods.operation.md").read_bytes()
    capsys.readouterr()
    assert cli.main([*args, "--force", "--log", "run.log"]) == 0
    captured = capsys.readouterr()
    assert (tmp_path / "methods.operation.md").read_bytes() == original
    assert (tmp_path / "methods.operation-2.md").is_file()
    assert "作業結果報告：" in captured.out
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == captured.err + captured.out


@pytest.mark.parametrize("lang,title", [("en", "Operation result report"), ("zh-TW", "作業結果報告"), ("Jp", "作業結果レポート"), ("ko", "작업 결과 보고서"), ("zh-CN", "作业结果报告"), ("fr", "Operation result report")])
def test_report_language(tmp_path, monkeypatch, lang, title):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["create", "report-skill", "--output", "methods", "--lang", lang]) == 0
    assert reports(tmp_path)[0].read_text(encoding="utf-8").startswith("# " + title)


def test_completion_with_execution_errors_not_wrong_answers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "evaluate", lambda *args, **kwargs: {"summary": {"evaluated": 2, "execution_error": 1, "judge_error": 0, "incorrect": 1}})
    assert cli.main(["eval", "input"]) == 0
    assert "Completed with issues" in reports(tmp_path)[0].read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "evaluate", lambda *args, **kwargs: {"summary": {"evaluated": 2, "incorrect": 2}})
    assert cli.main(["eval", "input"]) == 0
    assert "Status: Completed\n" in (tmp_path / "runs/evaluation.json.operation-2.md").read_text(encoding="utf-8")


def test_run_metadata_issues_and_raw_errors_are_not_copied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def run(*args, **kwargs):
        Path(str(args[1]) + ".run.json").write_text(json.dumps({"failed": 1, "errors": ["secret target answer"]}))
        return 0
    monkeypatch.setattr(cli, "run_agent", run)
    assert cli.main(["run-agent", "input", "--output", "responses.jsonl"]) == 0
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "Completed with issues" in text
    assert "`failed`: 1" in text
    assert "secret target answer" not in text


@pytest.mark.parametrize("all_fail", [False, True])
def test_write_failure_falls_back_or_warns_without_changing_result(tmp_path, monkeypatch, capsys, all_fail):
    monkeypatch.chdir(tmp_path)
    original_open = Path.open
    def open_file(path, *args, **kwargs):
        if ".operation" in path.name and (all_fail or ".lladar" not in path.parts):
            raise PermissionError("private write detail")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    assert cli.main(["create", "report-skill", "--output", "methods"]) == 0
    captured = capsys.readouterr()
    if all_fail:
        assert reports(tmp_path) == []
        assert "Warning: could not write" in captured.err
    else:
        report, = reports(tmp_path)
        assert ".lladar" in report.parts
    assert "private write detail" not in captured.out + captured.err


def test_log_open_error_still_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run.log").write_text("existing")
    assert cli.main(["create", "eval-skill", "--log", "run.log"]) == 2
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "Status: Failed" in text
    assert "— Missing" in text
    assert Path("run.log").read_text() == "existing"


def test_python_api_does_not_report(tmp_path):
    create_skill_template("eval", tmp_path / "methods")
    assert reports(tmp_path) == []


def test_situation_errors_are_counted_without_copying_details(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def run(*args, **kwargs):
        Path(str(args[1]) + ".run.json").write_text(json.dumps({"completed": 1, "errors": [{"error": "secret error"}]}))
        return 1
    monkeypatch.setattr(cli, "run_situation", run)
    assert cli.main(["run-agent", "--situation-config", "situation.json", "--num-scenarios", "2", "--output", "responses.jsonl"]) == 0
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "Completed with issues" in text and "`failed`: 1" in text
    assert "secret error" not in text


def test_failed_run_does_not_reuse_old_metadata_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("responses.jsonl.run.json").write_text(json.dumps({"failed": 99}))
    def fail(*args, **kwargs):
        raise ProviderError("private")
    monkeypatch.setattr(cli, "run_agent", fail)
    assert cli.main(["run-agent", "input", "--output", "responses.jsonl"]) == 2
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "`failed`: 99" not in text and "— Existing" in text


def test_overwritten_output_is_marked_modified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("evaluation.json").write_text("old")
    def evaluate(*args, **kwargs):
        Path(kwargs["output"]).write_text("updated output")
        return {"summary": {"evaluated": 1}}
    monkeypatch.setattr(cli, "evaluate", evaluate)
    assert cli.main(["eval", "input", "--output", "evaluation.json", "--force"]) == 0
    assert "— Modified" in reports(tmp_path)[0].read_text(encoding="utf-8")


def test_persistence_failure_does_not_suggest_unconditional_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    error = ProviderError("private")
    error.work_session_hint = {"path": "evaluation.json.work", "output": "evaluation.json", "persistence_failed": True}
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, "evaluate", fail)
    assert cli.main(["eval", "input"]) == 2
    text = reports(tmp_path)[0].read_text(encoding="utf-8")
    assert "Checkpoint persistence failed" in text
    assert "Continue with --resume" not in text
