import os
from pathlib import Path

from scripts import cleanup_generated


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
