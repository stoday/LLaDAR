import json

from lladar.cli import main


def test_skill_install_list_and_uninstall_for_project_targets(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["skill", "install", "--target", "all"]) == 0
    for directory in (".codex", ".claude", ".agents"):
        skill = tmp_path / directory / "skills" / "lladar-agent-evaluation"
        assert (skill / "SKILL.md").is_file()
        assert (skill / ".lladar-skill-install.json").is_file()

    assert main(["skill", "list"]) == 0
    assert "codex:" in capsys.readouterr().out

    assert main(["skill", "uninstall", "--target", "all"]) == 0
    assert not (tmp_path / ".codex" / "skills" / "lladar-agent-evaluation").exists()


def test_skill_install_protects_modified_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["skill", "install", "--target", "codex"]) == 0
    skill = tmp_path / ".codex" / "skills" / "lladar-agent-evaluation"
    (skill / "SKILL.md").write_text("custom", encoding="utf-8")

    assert main(["skill", "update", "--target", "codex"]) == 2
    assert "modified files" in capsys.readouterr().err


def test_skill_manifest_has_package_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["skill", "install", "--target", "codex"]) == 0
    manifest = json.loads(
        (tmp_path / ".codex" / "skills" / "lladar-agent-evaluation" / ".lladar-skill-install.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["skill"] == "lladar-agent-evaluation"
    assert manifest["files"]
