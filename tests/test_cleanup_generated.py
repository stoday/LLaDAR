import os
import shutil
from pathlib import Path

import pytest

from scripts import cleanup_generated


def test_all_generated_selects_runs_legacy_outputs_and_test_state_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    selected = ["runs", "responses.jsonl", "responses.jsonl.work", "response.jsonl.run.json",
                "responses.jsonl.trials.jsonl", "responses.jsonl.api-adapter",
                "evaluation.json.work-1234abcd", ".pytest_old", ".tmp/pytest", ".tmp/pytest-cache"]
    directories = {"runs", "responses.jsonl.work", "responses.jsonl.api-adapter",
                   "evaluation.json.work-1234abcd", ".pytest_old", ".tmp/pytest", ".tmp/pytest-cache"}
    for name in selected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name in directories:
            path.mkdir()
            (path / ".env").write_text("synthetic fixture")
        else:
            path.write_text("synthetic fixture")
    for name in [".env", "responses.jsonl.py", "response.jsonl.notes", "custom.jsonl"]:
        (tmp_path / name).write_text("keep")
    (tmp_path / ".lladar/browser-profiles").mkdir(parents=True)
    metadata = tmp_path / "src/lladar.egg-info"
    metadata.mkdir(parents=True)
    (metadata / "PKG-INFO").write_text("editable install metadata")
    paths = cleanup_generated.candidates(all_generated=True)
    assert set(paths) == {tmp_path / name for name in selected}
    for path in paths:
        assert cleanup_generated.classify(path, set(), False, all_generated=True) is None
    assert cleanup_generated.classify(
        tmp_path / "runs", {"runs/important.txt"}, False, all_generated=True,
    ) == "contains tracked files"
    assert cleanup_generated.classify(tmp_path / ".env", set(), False, all_generated=True) is not None


@pytest.mark.parametrize("apply,confirmation,removed", [(False, None, False), (True, "no", False), (True, "DELETE", True)])
def test_all_generated_preview_confirmation_and_deletion(tmp_path, monkeypatch, capsys, apply, confirmation, removed):
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    monkeypatch.setattr(cleanup_generated, "tracked_files", lambda: set())
    run = tmp_path / "runs"
    run.mkdir()
    (run / "responses.jsonl").write_text("synthetic answer")
    secret = tmp_path / ".env"
    secret.write_text("keep")
    profile = tmp_path / ".lladar/browser-profiles"
    profile.mkdir(parents=True)
    args = ["cleanup_generated.py", "--all-generated"] + (["--apply"] if apply else [])
    monkeypatch.setattr("sys.argv", args)
    def confirm(prompt):
        assert confirmation is not None, "Preview must not prompt"
        return confirmation
    monkeypatch.setattr("builtins.input", confirm)
    cleanup_generated.main()
    assert run.exists() is not removed
    assert secret.read_text() == "keep"
    assert profile.is_dir()
    output = capsys.readouterr().out
    assert "resume checkpoints" in output
    if not apply:
        assert "PREVIEW ONLY: nothing was deleted" in output
        assert "--all-generated --apply" in output
        assert "Mode: PREVIEW ONLY" in output and "SELECT runs" in output


def test_all_generated_does_not_follow_links_or_delete_outside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    run = tmp_path / "runs"
    run.mkdir()
    try:
        (run / "external").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    assert cleanup_generated.classify(run, set(), False, all_generated=True) is None
    shutil.rmtree(cleanup_generated.deletion_path(run))
    assert marker.read_text() == "keep"
    assert cleanup_generated.classify(outside, set(), False, all_generated=True) == "outside project"


def test_clear_lladar_selects_only_its_contents_and_allows_sensitive_state(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    lladar = tmp_path / ".lladar"
    lladar.mkdir()
    profile = lladar / "browser-profiles"
    profile.mkdir()
    (profile / "auth-state.json").write_text("example")
    backup = lladar / "backup.zip"
    backup.write_text("example")
    (tmp_path / "dist").mkdir()

    assert cleanup_generated.candidates(clear_lladar=True) == [backup, profile]
    assert cleanup_generated.classify(profile, set(), False) is not None
    assert cleanup_generated.classify(profile, set(), False, True) is None
    assert cleanup_generated.classify(backup, set(), False, True) is None
    assert (
        cleanup_generated.classify(
            profile, {".lladar/browser-profiles/auth-state.json"}, False, True
        )
        == "contains tracked files"
    )


def test_deletion_path_uses_windows_long_path_form(tmp_path: Path) -> None:
    target = cleanup_generated.deletion_path(tmp_path / "nested")
    if os.name == "nt":
        assert target.startswith("\\\\?\\")
    else:
        assert target == str(tmp_path / "nested")


def test_clear_tmp_selects_directory_but_preserves_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    temporary = tmp_path / ".tmp"
    temporary.mkdir()
    report = temporary / "report.json"
    report.write_text("example")
    secret = temporary / ".env"
    secret.write_text("example")
    (tmp_path / "dist").mkdir()

    assert cleanup_generated.candidates(clear_tmp=True) == [temporary]
    assert (
        cleanup_generated.classify(temporary, set(), False, clear_tmp=True)
        == "contains possible credentials or browser state"
    )
    secret.unlink()
    assert cleanup_generated.classify(temporary, set(), False, clear_tmp=True) is None


def test_clean_root_selects_only_fixed_artifacts_and_requires_explicit_state_flag(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    temporary = tmp_path / ".tmp"
    temporary.mkdir()
    (temporary / "auth-state.json").write_text("example")
    work = tmp_path / "evaluation.json.work-1234abcd"
    work.mkdir()
    report = tmp_path / "responses.jsonl.operation-7.md"
    report.write_text("example")
    unrelated = tmp_path / "questions.jsonl.operation.md"
    unrelated.write_text("keep")
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".env").write_text("keep")
    (tmp_path / "logs").mkdir()

    assert cleanup_generated.candidates(clean_root=True) == sorted(
        [temporary, work, report], key=str
    )
    assert cleanup_generated.classify(temporary, set(), False, clean_root=True) == (
        "contains possible credentials or browser state"
    )
    assert cleanup_generated.classify(
        temporary, set(), False, clean_root=True, include_captured_state=True
    ) is None
    assert cleanup_generated.classify(
        work, {"evaluation.json.work-1234abcd/answer.json"}, False,
        clean_root=True, include_captured_state=True,
    ) == "contains tracked files"
    assert cleanup_generated.classify(
        source, set(), False, clean_root=True, include_captured_state=True
    ) == "not a selected root artifact"
    assert cleanup_generated.classify(
        tmp_path / ".env", set(), False, clean_root=True,
        include_captured_state=True,
    ) == "not a selected root artifact"


def test_linked_linux_venv_cleanup_does_not_follow_directory_link(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(cleanup_generated, "ROOT", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "preserve.txt"
    marker.write_text("keep")
    venv = tmp_path / "linux-venv"
    venv.mkdir()
    try:
        (venv / "external-link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    assert cleanup_generated.classify(venv, set(), False, clean_root=True) == (
        "contains a link"
    )
    assert cleanup_generated.classify(
        venv, set(), False, clean_root=True, include_captured_state=True
    ) is None
    shutil.rmtree(cleanup_generated.deletion_path(venv))
    assert marker.read_text() == "keep"
