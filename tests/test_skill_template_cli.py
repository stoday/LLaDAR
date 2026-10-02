"""Public CLI contract for editable LLaDAR method skills."""

from pathlib import Path

import pytest

from lladar.cli import main
from lladar.method_skill import resolve_skill


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


def test_printed_use_command_quotes_a_skill_path_with_spaces(tmp_path, capsys):
    skill = tmp_path / "folder with spaces" / "custom-eval"

    assert main(["create", "eval-skill", "--output", str(skill)]) == 0

    assert f'--skill "{skill.resolve()}"' in capsys.readouterr().out


def test_existing_skill_directory_keeps_user_edits(tmp_path, capsys):
    skill = tmp_path / "custom-eval"
    skill.mkdir()
    edited = skill / "SKILL.md"
    edited.write_text("my edits\n", encoding="utf-8")

    assert main(["create", "eval-skill", "--output", str(skill)]) == 2

    assert edited.read_text(encoding="utf-8") == "my edits\n"
    assert not (skill / "AUTHORING.md").exists()
    assert "already exists" in capsys.readouterr().err


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
