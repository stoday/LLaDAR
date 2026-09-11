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


def ready_group(group_id: str = "group-1") -> dict:
    return {
        "schema_version": 2,
        "id": group_id,
        "status": "ready",
        "source": {
            "file": "knowledge.md",
            "chunk_id": "chunk-1",
            "text": "Plan A applies at age 65 or older. Plan B applies below age 65.",
        },
        "key_information": {"dimension": "age", "text": "70-year-old", "value": "70"},
        "original": {"question": "Which plan applies to a 70-year-old?", "answer": "Plan A."},
        "variants": [
            {
                "id": f"{group_id}-omission",
                "kind": "information_omission",
                "question": "Which plan applies to this person?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            {
                "id": f"{group_id}-grandmother",
                "kind": "peer_cue_addition",
                "question": "Which plan applies to my grandmother?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": ["my grandmother"]},
                "cue": {
                    "policy_id": "general-social-context",
                    "policy_version": 1,
                    "dimension": "kinship_role",
                    "value": "grandmother",
                    "set_id": f"{group_id}-kinship",
                    "tags": ["social_context"],
                },
            },
            {
                "id": f"{group_id}-grandfather",
                "kind": "peer_cue_addition",
                "question": "Which plan applies to my grandfather?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": ["my grandfather"]},
                "cue": {
                    "policy_id": "general-social-context",
                    "policy_version": 1,
                    "dimension": "kinship_role",
                    "value": "grandfather",
                    "set_id": f"{group_id}-kinship",
                    "tags": ["social_context"],
                },
            },
        ],
    }


def test_run_agent_writes_one_stable_result_per_schema_v2_case(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])

    seen = []

    def answer(question: str) -> str:
        seen.append(question)
        return f"answer: {question}"

    completed = lladar.run_agent(dataset, answers, answer=answer)

    assert completed == 4
    assert seen == [
        "Which plan applies to a 70-year-old?",
        "Which plan applies to this person?",
        "Which plan applies to my grandmother?",
        "Which plan applies to my grandfather?",
    ]
    assert read_jsonl(answers) == [
        {
            "schema_version": 2,
            "id": "group-1",
            "group_id": "group-1",
            "kind": "original",
            "question": "Which plan applies to a 70-year-old?",
            "status": "ok",
            "answer": "answer: Which plan applies to a 70-year-old?",
        },
        {
            "schema_version": 2,
            "id": "group-1-omission",
            "group_id": "group-1",
            "kind": "information_omission",
            "question": "Which plan applies to this person?",
            "status": "ok",
            "answer": "answer: Which plan applies to this person?",
        },
        {
            "schema_version": 2,
            "id": "group-1-grandmother",
            "group_id": "group-1",
            "kind": "peer_cue_addition",
            "question": "Which plan applies to my grandmother?",
            "status": "ok",
            "answer": "answer: Which plan applies to my grandmother?",
        },
        {
            "schema_version": 2,
            "id": "group-1-grandfather",
            "group_id": "group-1",
            "kind": "peer_cue_addition",
            "question": "Which plan applies to my grandfather?",
            "status": "ok",
            "answer": "answer: Which plan applies to my grandfather?",
        },
    ]


def test_run_agent_rejects_legacy_dataset_before_running_agent(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [{"id": "legacy", "underspecified_question": "question"}])
    called = False

    def answer(question: str) -> str:
        nonlocal called
        called = True
        return question

    with pytest.raises(lladar.DatasetValidationError, match="schema_version.*regenerate"):
        lladar.run_agent(dataset, answers, answer=answer)

    assert called is False
    assert not answers.exists()


def test_run_agent_accepts_structurally_valid_custom_policy_dataset(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    group = ready_group()
    for variant, value in zip(group["variants"][1:], ("established", "new")):
        variant["cue"].update(
            policy_id="restaurant-context",
            dimension="restaurant_tenure",
            value=value,
            tags=[],
        )
    write_jsonl(dataset, [group])

    completed = lladar.run_agent(dataset, answers, answer=lambda question: question)

    assert completed == 4


def test_run_agent_rejects_duplicate_case_ids_before_running_agent(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    first = ready_group("group-1")
    second = ready_group("group-2")
    second["variants"][0]["id"] = first["variants"][0]["id"]
    write_jsonl(dataset, [first, second])

    with pytest.raises(lladar.DatasetValidationError, match="duplicate case id"):
        lladar.run_agent(dataset, answers, answer=lambda question: question)

    assert not answers.exists()


def test_run_agent_skips_dataset_items_marked_skipped(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(
        dataset,
        [
            ready_group("item-ready"),
            {
                "schema_version": 2,
                "id": "item-skipped",
                "status": "skipped",
                "source": {"file": "knowledge.md", "chunk_id": "chunk-2", "text": "x"},
                "reason_code": "no_key_information",
                "reason": "not suitable",
                "attempts": 1,
            },
        ],
    )

    seen = []

    def answer(question: str) -> str:
        seen.append(question)
        return "answer"

    completed = lladar.run_agent(dataset, answers, answer=answer)

    assert completed == 4
    assert len(seen) == 4
    assert {record["group_id"] for record in read_jsonl(answers)} == {"item-ready"}


def test_run_agent_records_item_errors_and_continues(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    group = ready_group()
    group["variants"][0]["question"] = "fails"
    write_jsonl(dataset, [group])

    def answer(question: str) -> str:
        if question == "fails":
            raise RuntimeError("agent failed")
        return question

    completed = lladar.run_agent(dataset, answers, answer=answer)

    assert completed == 3
    results = read_jsonl(answers)
    assert [result["status"] for result in results] == [
        "ok", "execution_error", "ok", "ok"
    ]
    assert results[1]["error"] == "RuntimeError: agent failed"


def test_run_agent_protects_existing_output(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])
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
    write_jsonl(dataset, [ready_group()])

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

    assert completed == 4
    assert [record["answer"] for record in read_jsonl(answers)] == [
        "Which plan applies to a 70-year-old?",
        "Which plan applies to this person?",
        "Which plan applies to my grandmother?",
        "Which plan applies to my grandfather?",
    ]
    assert entrypoint.read_text(encoding="utf-8") == original


def test_run_agent_normalizes_an_entrypoint_path_inside_the_original_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    original_entrypoint = project / "main.py"
    original_entrypoint.write_text("print('fixed')\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])

    class CopyCheckingAdapter:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            assert entrypoint == workspace / "main.py"
            assert entrypoint != original_entrypoint
            entrypoint.write_text(
                "import os\nprint(os.environ['LLADAR_QUESTION'])\n", encoding="utf-8"
            )

    completed = lladar.run_agent(
        dataset,
        answers,
        project=project,
        entrypoint=original_entrypoint,
        adapter=CopyCheckingAdapter(),
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert completed == 4


def test_run_agent_accepts_a_cwd_relative_project_prefixed_entrypoint(
    tmp_path, monkeypatch
):
    project = tmp_path / "example_project"
    project.mkdir()
    (project / "main.py").write_text(
        "import os\nprint(os.environ['LLADAR_QUESTION'])\n", encoding="utf-8"
    )
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])
    monkeypatch.chdir(tmp_path)

    class MustNotAdapt:
        def adapt(self, workspace: Path, entrypoint: Path) -> None:
            raise AssertionError("an entrypoint with LLADAR_QUESTION is already compatible")

    completed = lladar.run_agent(
        "dataset.jsonl",
        "answers.jsonl",
        project="example_project",
        entrypoint="example_project/main.py",
        adapter=MustNotAdapt(),
        runs_root=tmp_path / ".lladar" / "runs",
    )

    assert completed == 4
    assert len(read_jsonl(answers)) == 4


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
    write_jsonl(dataset, [ready_group()])

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

    assert {record["answer"] for record in read_jsonl(answers)} == {"from-env-file"}


def test_run_agent_does_not_leave_answer_artifact_when_adaptation_fails(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('never')\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])

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
    group = ready_group()
    group["variants"][0]["question"] = "fails"
    write_jsonl(dataset, [group])

    def answer(question: str) -> str:
        if question == "fails":
            raise RuntimeError("secret provider detail")
        return "answer"

    lladar.run_agent(dataset, answers, answer=answer)

    captured = capsys.readouterr()
    assert "[CONFIG]" in captured.err
    assert "[SESSION] 1/4" in captured.err
    assert "[WARN] item=group-1-omission" in captured.err
    assert "RuntimeError" in captured.err
    assert "secret provider detail" not in captured.err
    assert captured.out == ""


def test_run_agent_can_disable_progress(tmp_path, capsys):
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])

    lladar.run_agent(dataset, answers, answer=lambda question: "answer", verbose=False)

    assert capsys.readouterr().err == ""


def test_run_agent_forces_utf8_for_unicode_agent_output(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('≈')\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    answers = tmp_path / "answers.jsonl"
    write_jsonl(dataset, [ready_group()])

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
