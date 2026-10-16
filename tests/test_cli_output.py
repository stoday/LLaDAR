from __future__ import annotations

import json
from pathlib import Path

import pytest

from lladar import cli
from test_situation import AuthoringAgent


WORKFLOWS = [
    (["create", "test-dataset", "--knowledge", "knowledge.md"], "create_test_dataset", None),
    (["create", "situation", "--instructions", "Observe", "--stop-criteria", "Done", "--max-turns", "1"], "create_situation", "situation.json"),
    (["run-agent", "dataset.jsonl"], "run_agent", "responses.jsonl"),
    (["run-agent", "--situation-config", "situation.json", "--num-scenarios", "1"], "run_situation", "responses.jsonl"),
    (["eval", "responses.jsonl"], "evaluate", "evaluation.json"),
    (["eval", "responses.jsonl", "--situation-config", "situation.json"], "evaluate_situation", "evaluation.json"),
    (["report", "evaluation.json"], "create_report", "report.md"),
]


@pytest.mark.parametrize("argv,operation,filename", WORKFLOWS)
@pytest.mark.parametrize("destination", [None, "nested/results", "existing.json", "nested/custom.jsonl"])
def test_output_reaches_each_workflow_as_a_file(tmp_path, monkeypatch, capsys, argv, operation, filename, destination):
    monkeypatch.chdir(tmp_path)
    # Existing directories take precedence even when their names have a suffix.
    Path("existing.json").mkdir()
    received = []

    def execute(*args, **kwargs):
        output = Path(kwargs["output"] if "output" in kwargs else args[1])
        received.append(output)
        if operation == "create_test_dataset":
            output.parent.mkdir(parents=True, exist_ok=True)
            Path(str(output) + ".generation.json").write_text(
                json.dumps({"status": "ready", "dataset": {"lines": []}}), encoding="utf-8"
            )
            return []
        if operation == "evaluate":
            return {"summary": {"evaluated": 0}}
        if operation == "evaluate_situation":
            return {"items": [], "summary": dict.fromkeys(
                ["valid_determinate", "judge_error", "execution_error", "invalid", "indeterminate"], 0)}
        return output if operation == "create_report" else 0

    monkeypatch.setattr(cli, operation, execute)
    flags = [] if destination is None else ["--output", destination]
    assert cli.main([*argv, *flags]) == 0
    output, = received
    if destination == "nested/custom.jsonl":
        assert output == Path(destination)
    else:
        directory = Path("runs") if destination is None else Path(destination)
        assert output.parent == directory
        if filename is None:
            assert output.name.startswith("test-dataset-") and output.suffix == ".jsonl"
        else:
            assert output.name == filename
    terminal = capsys.readouterr().out
    assert str(output) in terminal
    assert "Work summary" in terminal
    assert f"Output directory: {output.resolve().parent}" in terminal
    assert str(output.resolve()) in terminal
    assert "Next:" in terminal


def test_output_resolution_handles_explicit_files_and_directory_hints(tmp_path):
    existing_file = tmp_path / "extensionless"
    existing_file.touch()
    assert cli._resolve_output(existing_file, "report.md") == existing_file
    assert cli._resolve_output(tmp_path / "new.json", "report.md") == tmp_path / "new.json"
    for separator in ("/", "\\"):
        directory = tmp_path / "results.v1"
        assert cli._resolve_output(str(directory) + separator, "report.md") == directory / "report.md"
        assert not directory.exists()


def test_dataset_directory_collision_keeps_existing_file(tmp_path):
    output = cli._resolve_dataset_output(tmp_path)
    output.write_text("keep", encoding="utf-8")
    following = cli._resolve_dataset_output(tmp_path)
    assert following != output
    assert following.parent == tmp_path
    assert output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("destination", [None, "nested/results"])
def test_situation_writes_default_file_and_preserves_overwrite_guard(tmp_path, monkeypatch, destination):
    monkeypatch.chdir(tmp_path)
    argv = ["create", "situation", "--instructions", "Observe", "--stop-criteria", "Done", "--max-turns", "1"]
    if destination is not None:
        argv += ["--output", destination]
    output = Path(destination or "runs") / "situation.json"
    assert cli.main(argv, skill_agent_factory=AuthoringAgent) == 0
    original = output.read_bytes()
    assert json.loads(original)["stop"]["max_turns"] == 1
    assert cli.main(argv, skill_agent_factory=lambda **_: pytest.fail("must stop before model work")) == 2
    assert output.read_bytes() == original
    assert cli.main([*argv, "--force"], skill_agent_factory=AuthoringAgent) == 0


@pytest.mark.parametrize("graph_used", [False, True])
def test_dataset_summary_lists_only_present_sidecars(tmp_path, capsys, graph_used):
    output = tmp_path / "folder with spaces" / "dataset.jsonl"
    output.parent.mkdir()
    Path(str(output) + ".generation.json").touch()
    graph = Path(str(output) + ".graph.json")
    if graph_used:
        graph.touch()
    cli._show_work_summary("test-dataset", output)
    terminal = capsys.readouterr().out
    assert str(Path(str(output) + ".generation.json")) in terminal
    assert "source provenance" in terminal
    assert (str(graph) in terminal) is graph_used
    assert f'"{output}"' in terminal if cli.os.name == "nt" else cli.shlex.quote(str(output)) in terminal


def test_situation_summary_is_logged(tmp_path, capsys):
    output, log = tmp_path / "results", tmp_path / "work.log"
    assert cli.main([
        "create", "situation", "--instructions", "Observe", "--stop-criteria", "Done",
        "--max-turns", "1", "--output", str(output), "--log", str(log),
    ], skill_agent_factory=AuthoringAgent) == 0
    terminal = capsys.readouterr().out
    assert "Work summary" in terminal
    assert str(output / "situation.json") in terminal
    assert "stopping rules" in terminal
    assert "Work summary" in log.read_text(encoding="utf-8")


def test_failed_stage_does_not_display_a_success_summary(tmp_path, capsys):
    assert cli.main(["eval", str(tmp_path / "missing.jsonl"), "--no-verbose"]) == 2
    assert "Work summary" not in capsys.readouterr().out


def test_interrupted_generation_prints_executable_resume_command_and_locations(tmp_path, monkeypatch, capsys):
    import shlex
    from test_agent_selected_generation import DirectAgent
    monkeypatch.chdir(tmp_path)
    knowledge=tmp_path/'source notes.md'
    knowledge.write_text('Retention is 30 days.',encoding='utf-8')
    class Interrupted(DirectAgent):
        def __call__(self, request):
            raise KeyboardInterrupt()
    argv=['create','test-dataset','--knowledge',str(knowledge),'--output','results folder',
          '--count','1','--seed','7','--no-verbose']
    assert cli.main(argv,skill_agent_factory=Interrupted)==130
    captured=capsys.readouterr()
    command=next((line.strip() for line in captured.err.splitlines() if line.strip().startswith('lladar ')),None)
    assert command is not None
    tokens=shlex.split(command)
    assert tokens[tokens.index('--resume')+1].endswith('.work')
    assert Path(tokens[tokens.index('--resume')+1]).is_dir()
    assert Path(tokens[tokens.index('--output')+1]).is_absolute()
    assert str(tmp_path) in captured.err and str(knowledge) in captured.err
    assert '--seed' in tokens and tokens[tokens.index('--seed')+1]=='7'
    assert cli.main(tokens[1:],skill_agent_factory=DirectAgent)==0


def test_provider_interruption_prints_resume_without_force_and_reuses_original_overwrite_authorization(tmp_path, monkeypatch, capsys):
    import shlex
    from lladar.exceptions import ProviderError
    from test_simple_pipeline import EvaluationSkillAgent
    from test_recoverable_evaluation import responses
    monkeypatch.chdir(tmp_path)
    source=responses(tmp_path)
    output=tmp_path/'evaluation.json'
    output.write_text('previous result',encoding='utf-8')
    Path(str(output)+'.work').mkdir()
    class Unavailable(EvaluationSkillAgent):
        def __call__(self, request):
            if request['stage']=='judgment':
                raise ProviderError('fixture unavailable')
            return super().__call__(request)
    assert cli.main(['eval',str(source),'--output',str(output),'--force','--no-verbose'],skill_agent_factory=Unavailable)==2
    message=capsys.readouterr().err
    tokens=shlex.split(next(line.strip() for line in message.splitlines() if line.strip().startswith('lladar ')))
    assert '--force' not in tokens
    session=Path(tokens[tokens.index('--resume')+1])
    assert session.name.startswith('evaluation.json.work-')
    assert output.read_text(encoding='utf-8')=='previous result'
    # An omitted output must restore the checkpoint's location, even when it
    # predates the CLI default changing to runs/.
    index=tokens.index('--output')
    del tokens[index:index+2]
    assert cli.main(tokens[1:],skill_agent_factory=EvaluationSkillAgent)==0
    assert json.loads(output.read_text(encoding='utf-8'))['summary']['evaluated']==2


def test_browser_interruption_resume_hint_keeps_private_url_out_of_output(tmp_path,capsys):
    from test_simple_pipeline import make_dataset
    from fixture_extraction import browser_cli_terminal
    source=tmp_path/'dataset.jsonl'
    make_dataset(source,count=1)
    def interrupted(**options):
        raise KeyboardInterrupt()
    with browser_cli_terminal():
        assert cli.main(['run-agent',str(source),'--page-url','https://example.test/private-path?token=private-token',
                         '--output',str(tmp_path/'responses.jsonl'),'--no-verbose'],browser_target_factory=interrupted)==130
    message=capsys.readouterr().err
    assert 'private-path' not in message and 'private-token' not in message
    assert 'Enter the same --page-url' in message
    assert '--resume' in message


def test_interrupted_report_resume_command_keeps_input_and_model_budgets(tmp_path,monkeypatch,capsys):
    import shlex
    from lladar.evaluation import evaluate
    from test_simple_pipeline import EvaluationSkillAgent, ReportSkillAgent
    from test_recoverable_evaluation import responses
    monkeypatch.chdir(tmp_path)
    evaluation=tmp_path/'evaluation file.json'
    evaluate(responses(tmp_path),output=evaluation,skill_agent_factory=EvaluationSkillAgent,verbose=False)
    class Interrupted(ReportSkillAgent):
        def __call__(self, request):
            raise KeyboardInterrupt()
    assert cli.main(['report',str(evaluation),'--output','report file.md','--max-output-tokens','2000',
                     '--no-verbose'],skill_agent_factory=Interrupted)==130
    message=capsys.readouterr().err
    assert str(evaluation) in message
    tokens=shlex.split(next(line.strip() for line in message.splitlines() if line.strip().startswith('lladar ')))
    assert tokens[tokens.index('--max-output-tokens')+1]=='2000'
    assert cli.main(tokens[1:],skill_agent_factory=ReportSkillAgent)==0
    assert (tmp_path/'report file.md').exists()
