import json
import sys
import types
from pathlib import Path

import pytest

import lladar


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_agent_writes_one_id_keyed_result_per_dataset_item(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            {"id": "item-1", "underspecified_question": "first question"},
            {"id": "item-2", "underspecified_question": "second question"},
        ],
    )

    seen = []

    def answer(question: str) -> str:
        seen.append(question)
        return f"answer: {question}"

    completed = lladar.run_agent(dataset, answers, answer=answer)

    assert completed == 2
    assert seen == ["first question", "second question"]
    assert read_jsonl(answers) == [
        {"id": "item-1", "status": "ok", "answer": "answer: first question"},
        {"id": "item-2", "status": "ok", "answer": "answer: second question"},
    ]


def test_run_agent_records_item_errors_and_continues(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            {"id": "item-1", "underspecified_question": "works"},
            {"id": "item-2", "underspecified_question": "fails"},
            {"id": "item-3", "underspecified_question": "works again"},
        ],
    )

    def answer(question: str) -> str:
        if question == "fails":
            raise RuntimeError("agent failed")
        return question

    completed = lladar.run_agent(dataset, answers, answer=answer)

    assert completed == 2
    assert read_jsonl(answers) == [
        {"id": "item-1", "status": "ok", "answer": "works"},
        {
            "id": "item-2",
            "status": "error",
            "error": "RuntimeError: agent failed",
        },
        {"id": "item-3", "status": "ok", "answer": "works again"},
    ]


def test_run_agent_protects_existing_output(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "item-1", "underspecified_question": "question"}])
    answers.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        lladar.run_agent(dataset, answers, answer=lambda question: "answer")


def test_copy_project_creates_managed_workspace_without_secrets_or_state(tmp_path):
    from lladar.runner import copy_project

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('ok')", encoding="utf-8")
    (project / ".env").write_text("SECRET=do-not-copy", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("state", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "marker").write_text("state", encoding="utf-8")

    with copy_project(project, runs_root=tmp_path / ".lladar" / "runs") as workspace:
        assert (workspace / "main.py").read_text(encoding="utf-8") == "print('ok')"
        assert not (workspace / ".env").exists()
        assert not (workspace / ".git").exists()
        assert not (workspace / ".venv").exists()

    assert workspace.exists()
    assert workspace.parent.parent == tmp_path / ".lladar" / "runs"


def test_run_agent_project_mode_executes_each_question_in_a_copy(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    entrypoint = project / "main.py"
    original = "import os\nprint(os.environ['LLADAR_QUESTION'])\n"
    entrypoint.write_text(original, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            {"id": "item-1", "underspecified_question": "first"},
            {"id": "item-2", "underspecified_question": "second"},
        ],
    )

    class NoOpAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            return None

    completed = lladar.run_agent(
        dataset,
        answers,
        project=project,
        entrypoint="main.py",
        adapter=NoOpAdapter(),
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert completed == 2
    assert read_jsonl(answers) == [
        {"id": "item-1", "status": "ok", "answer": "first"},
        {"id": "item-2", "status": "ok", "answer": "second"},
    ]
    assert entrypoint.read_text(encoding="utf-8") == original


def test_run_agent_injects_env_file_without_copying_it(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "import os\nprint(os.environ['PROJECT_SECRET'])\n", encoding="utf-8"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("PROJECT_SECRET=from-env-file\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "item-1", "underspecified_question": "question"}])

    class NoOpAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            return None

    lladar.run_agent(
        dataset,
        answers,
        project=project,
        entrypoint="main.py",
        adapter=NoOpAdapter(),
        env_file=env_file,
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert read_jsonl(answers)[0]["answer"] == "from-env-file"


def test_run_agent_does_not_leave_answer_artifact_when_adaptation_fails(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('never')\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "item-1", "underspecified_question": "question"}])

    class FailingAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            raise RuntimeError("adaptation failed")

    with pytest.raises(RuntimeError, match="adaptation failed"):
        lladar.run_agent(
            dataset,
            answers,
            project=project,
            entrypoint="main.py",
            adapter=FailingAdapter(),
            runs_root=tmp_path / ".lladar" / "runs",
        )

    assert not answers.exists()


def test_project_mode_reuses_project_venv_interpreter_without_copying_it(tmp_path):
    from lladar.runner import resolve_project_python

    project = tmp_path / "project"
    (project / ".venv" / "Scripts").mkdir(parents=True)
    project_python = project / ".venv" / "Scripts" / "python.exe"
    project_python.write_text("placeholder", encoding="utf-8")

    assert resolve_project_python(project) == project_python


def test_run_agent_shows_progress_by_default_and_keeps_errors_on_stderr(tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            {"id": "item-1", "underspecified_question": "works"},
            {"id": "item-2", "underspecified_question": "fails"},
        ],
    )

    def answer(question: str) -> str:
        if question == "fails":
            raise RuntimeError("secret provider detail")
        return "answer"

    lladar.run_agent(dataset, answers, answer=answer)

    captured = capsys.readouterr()
    assert "[CONFIG]" in captured.err
    assert "[PAIR] 1/2" in captured.err
    assert "[WARN] item=item-2" in captured.err
    assert "RuntimeError" in captured.err
    assert "secret provider detail" not in captured.err
    assert captured.out == ""


def test_run_agent_can_disable_progress(tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "item-1", "underspecified_question": "question"}])

    lladar.run_agent(dataset, answers, answer=lambda question: "answer", verbose=False)

    assert capsys.readouterr().err == ""


def test_run_agent_forces_utf8_for_unicode_agent_output(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('≈')\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "item-1", "underspecified_question": "question"}])

    class NoOpAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            return None

    lladar.run_agent(
        dataset,
        answers,
        project=project,
        entrypoint="main.py",
        adapter=NoOpAdapter(),
        verbose=False,
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert read_jsonl(answers)[0]["answer"] == "≈"


def test_sandbox_tools_cannot_escape_workspace(tmp_path):
    from lladar.runner import SandboxTools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('ok')", encoding="utf-8")
    tools = SandboxTools(workspace)

    assert "main.py" in tools.list_directory(".")
    assert tools.read_file("main.py") == "print('ok')"
    assert tools.search("print", ".") == ["main.py"]
    with pytest.raises(ValueError):
        tools.read_file("../outside.txt")


def test_sandbox_tools_replace_only_changes_exact_text(tmp_path):
    from lladar.runner import SandboxTools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("question = 'fixed'\n", encoding="utf-8")
    tools = SandboxTools(workspace)

    tools.replace_text("main.py", "question = 'fixed'", "question = 'adapted'")

    assert source.read_text(encoding="utf-8") == "question = 'adapted'\n"


def test_akasha_adapter_controller_uses_tools_only_inside_workspace(tmp_path, monkeypatch):
    from lladar.runner import AkashaAdapterController

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entrypoint = workspace / "main.py"
    entrypoint.write_text("question = 'fixed'\n", encoding="utf-8")
    captured = {}

    def create_tool(description, function, name):
        return function

    def agents(**kwargs):
        captured["kwargs"] = kwargs

        def run(prompt):
            replace_text = kwargs["tools"][3]
            replace_text(
                "main.py",
                "question = 'fixed'",
                "import os\nquestion = os.environ['LLADAR_QUESTION']",
            )
            return "adapted"

        return run

    monkeypatch.setitem(
        sys.modules,
        "akasha",
        types.SimpleNamespace(create_tool=create_tool, agents=agents),
    )

    AkashaAdapterController().adapt(workspace, entrypoint)

    assert "LLADAR_QUESTION" in entrypoint.read_text(encoding="utf-8")
    assert captured["kwargs"]["tools"]
