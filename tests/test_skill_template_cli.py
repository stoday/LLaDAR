"""Public CLI contract for editable LLaDAR method skills."""

from pathlib import Path
import json
import re
import shlex
from types import SimpleNamespace

import pytest

from lladar.cli import main
from lladar.method_skill import resolve_skill
from lladar.skill_templates import create_skill_template


def guide_blocks(tmp_path, stage, language):
    skill = create_skill_template(stage, tmp_path / f"example-{stage}")
    guide = (skill / "AUTHORING.md").read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)\n```", guide, re.DOTALL)


@pytest.mark.parametrize("case_count", [0, 2, 8])
def test_generated_scheduling_examples_run_in_the_real_strategy_environment(tmp_path, case_count):
    from lladar.run_strategy import StrategyWorkspace

    examples = guide_blocks(tmp_path, "run-agent", "python")
    records = [{"question": f"Question {index}", "expected_answer": "Answer"}
               for index in range(case_count)]
    schedules = []
    for index, source in enumerate(examples):
        workspace = StrategyWorkspace(records, tmp_path / str(index), seed=42)
        workspace.read_dataset()
        workspace.write_strategy(source)
        schedules.append(workspace.resolve())
    full, sampled, first_five = schedules
    assert [(item.case.record_index, item.repeats) for item in full] == [
        (index, 3) for index in range(1, case_count + 1)]
    assert len(sampled) == min(5, case_count)
    assert len({item.case.record_index for item in sampled}) == len(sampled)
    assert all(item.repeats == 1 for item in sampled)
    assert [(item.case.record_index, item.repeats) for item in first_five] == [
        (index, 2) for index in range(1, min(5, case_count) + 1)]


def test_generated_eval_and_report_examples_are_accepted_by_real_tools(tmp_path):
    from lladar.evaluation import EvaluationWorkspace
    from lladar.reporting import ReportWorkspace

    plan, judgment = [json.loads(block) for block in guide_blocks(tmp_path, "eval", "json")]
    evaluator = EvaluationWorkspace()
    assert evaluator.submit_plan(plan) == {"accepted": True}
    assert evaluator.submit_judgment(judgment) == {"accepted": True}
    narrative, = [json.loads(block) for block in guide_blocks(tmp_path, "report", "json")]
    assert ReportWorkspace().submit_report(narrative) == {"accepted": True}


def test_generated_situation_example_matches_the_proposal_contract(tmp_path):
    from lladar.situation import _proposal

    proposal, = [json.loads(block) for block in guide_blocks(tmp_path, "situation", "json")]
    assert _proposal(proposal) == proposal


def test_generated_dataset_examples_are_accepted_in_their_stages(tmp_path):
    from lladar.skill_generation import GenerationWorkspace

    points, graph, plans, qa = [json.loads(block) for block in guide_blocks(tmp_path, "test-dataset", "json")]
    workspace = GenerationWorkspace([(tmp_path / "knowledge.md", "Service A keeps data for 30 days.")])
    workspace.current_stage, workspace.current_source = "knowledge_points", "source_001"
    workspace.read_source("source_001")
    assert workspace.submit_knowledge_points(points) == {"accepted": ["kp_000001"], "rejected": []}
    workspace.current_stage = "semantic_graph"
    assert workspace.submit_semantic_graph(graph)["accepted"] is True
    workspace.current_stage = "test_plans"
    assert workspace.submit_test_plans(plans) == {"accepted": 1, "pairs": 0}
    workspace.current_stage, workspace.current_point = "qa", "kp_000001"
    assert workspace.submit_qa(qa) == {"accepted": True}


def test_user_can_create_an_eval_skill_at_the_default_location(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["create", "eval-skill"]) == 0

    skill = tmp_path / "lladar-skills" / "eval"
    assert resolve_skill(skill, Path("unused")) == skill.resolve()
    assert (skill / "SKILL.md").read_text(encoding="utf-8").startswith(
        "---\nname: eval\n"
    )
    guide = (skill / "AUTHORING.md").read_text(encoding="utf-8")
    assert "submit_plan" in guide
    assert "submit_judgment" in guide
    assert "lladar eval" in capsys.readouterr().out


@pytest.mark.parametrize(("stage", "tool", "command"), [
    ("test-dataset", "submit_test_plans", "lladar create test-dataset"),
    ("situation", "submit_situation", "lladar create situation"),
    ("run-agent", "write_strategy", "lladar run-agent"),
    ("report", "submit_report", "lladar report"),
])
def test_user_can_create_each_other_skill_at_a_custom_location(
    tmp_path, capsys, stage, tool, command,
):
    skill = tmp_path / f"my-{stage}-method"

    assert main(["create", f"{stage}-skill", "--output", str(skill)]) == 0

    assert resolve_skill(skill, Path("unused")) == skill.resolve()
    assert (skill / "SKILL.md").read_text(encoding="utf-8").startswith(
        f"---\nname: {skill.name}\n"
    )
    guide = (skill / "AUTHORING.md").read_text(encoding="utf-8")
    assert tool in guide
    assert command in capsys.readouterr().out


def test_failed_write_does_not_leave_a_partial_skill(tmp_path, monkeypatch, capsys):
    skill = tmp_path / "my-eval"
    original_write = Path.write_text

    def fail_guide_write(path, content, *args, **kwargs):
        if path.name == "AUTHORING.md":
            raise OSError("disk write failed")
        return original_write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_guide_write)

    assert main(["create", "eval-skill", "--output", str(skill)]) == 2
    assert not skill.exists()
    assert "disk write failed" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["nt", "posix"])
@pytest.mark.parametrize("folder", ["folder with spaces", "folder's files"])
def test_printed_use_command_quotes_a_skill_path_with_spaces(
    tmp_path, capsys, monkeypatch, platform, folder,
):
    import lladar.cli as cli

    # Select only the CLI's quoting branch; pathlib must retain the host OS.
    monkeypatch.setattr(cli, "os", SimpleNamespace(name=platform))
    skill = tmp_path / folder / "custom-eval"

    assert main(["create", "eval-skill", "--output", str(skill)]) == 0

    output = capsys.readouterr().out
    use_command, = [line.removeprefix("Use: ") for line in output.splitlines()
                   if line.startswith("Use: ")]
    if platform == "nt":
        assert f'--skill "{skill.resolve()}"' in use_command
    else:
        assert shlex.split(use_command) == [
            "lladar", "eval", "RESPONSES", "--skill", str(skill.resolve()),
            "--output", "evaluation.json",
        ]


def test_existing_skill_directory_keeps_user_edits(tmp_path, capsys):
    skill = tmp_path / "custom-eval"
    skill.mkdir()
    edited = skill / "SKILL.md"
    edited.write_text("my edits\n", encoding="utf-8")

    assert main(["create", "eval-skill", "--output", str(skill)]) == 2

    assert edited.read_text(encoding="utf-8") == "my edits\n"
    assert not (skill / "AUTHORING.md").exists()
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["test-dataset", "situation", "run-agent", "eval", "report"])
def test_force_overwrites_template_files_but_keeps_other_files(tmp_path, stage):
    skill = create_skill_template(stage, tmp_path / f"my-{stage}")
    expected = {name: (skill / name).read_bytes() for name in ("SKILL.md", "AUTHORING.md")}
    for name in expected:
        (skill / name).write_text("user edits", encoding="utf-8")
    (skill / "strategy.py").write_text("user strategy", encoding="utf-8")

    assert main(["create", f"{stage}-skill", "--output", str(skill), "--force"]) == 0

    assert {name: (skill / name).read_bytes() for name in expected} == expected
    assert (skill / "strategy.py").read_text(encoding="utf-8") == "user strategy"


def test_failed_force_write_restores_existing_files(tmp_path, monkeypatch):
    skill = create_skill_template("eval", tmp_path / "my-eval")
    originals = {"SKILL.md": b"custom skill\r\n", "AUTHORING.md": b"custom guide\r\n"}
    for name, content in originals.items():
        (skill / name).write_bytes(content)
    original_write = Path.write_text

    def fail_guide_write(path, content, *args, **kwargs):
        if path.name == "AUTHORING.md":
            path.write_bytes(b"partial write")
            raise OSError("disk write failed")
        return original_write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_guide_write)
    assert main(["create", "eval-skill", "--output", str(skill), "--force"]) == 2
    assert {name: (skill / name).read_bytes() for name in originals} == originals


def test_force_does_not_replace_a_file_with_a_directory(tmp_path):
    target = tmp_path / "my-eval"
    target.write_text("keep", encoding="utf-8")
    assert main(["create", "eval-skill", "--output", str(target), "--force"]) == 2
    assert target.read_text(encoding="utf-8") == "keep"


def test_invalid_skill_directory_name_is_rejected_before_writing(tmp_path, capsys):
    skill = tmp_path / "Invalid Name"

    assert main(["create", "report-skill", "--output", str(skill)]) == 2

    assert not skill.exists()
    assert "lowercase letters" in capsys.readouterr().err


def test_create_help_names_editable_skill_templates(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["create", "--help"])

    assert exit_info.value.code == 0
    assert "editable skill" in capsys.readouterr().out.lower()
