"""Project fingerprinting must prune ignored environments before traversing them."""
import os
from pathlib import Path

import pytest

from lladar.work_session import file_identity, file_sha


@pytest.mark.parametrize("ignored_name", [".git", ".venv", "__pycache__", "node_modules", ".env"])
def test_project_identity_does_not_scan_ignored_subtrees(tmp_path, monkeypatch, ignored_name):
    project = tmp_path / "project"
    source = project / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('source')", encoding="utf-8")
    ignored = source.parent / ignored_name
    ignored.mkdir()
    (ignored / "dependency.py").write_text("excluded", encoding="utf-8")
    expected = {str(source.relative_to(project)): file_sha(source)}
    scandir = os.scandir

    def guarded_scandir(path):
        assert not Path(path).is_relative_to(ignored), "Traversed an excluded environment before session startup"
        return scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    assert file_identity(project) == expected


def test_project_identity_preserves_existing_fingerprints(tmp_path):
    project = tmp_path / "project"
    for name in ["README.md", "src/app.py", "src/.env", "src/.env.example", "src/.venv/library.py"]:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    # Compare with the existing contract so saved sessions remain resumable.
    expected = {str(path.relative_to(project)): file_sha(path)
                for path in sorted(project.rglob("*")) if path.is_file() and not any(
                    part in {".git", ".venv", "__pycache__", "node_modules", ".env"}
                    for part in path.relative_to(project).parts)}
    assert file_identity(project) == expected
    assert file_identity(project / "README.md") == {"name": "README.md", "sha256": file_sha(project / "README.md")}
