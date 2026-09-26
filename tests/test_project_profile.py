from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lladar.project_profile import describe_project


def test_project_profile_uses_source_evidence_and_rejects_secret_read(tmp_path: Path, monkeypatch):
    (tmp_path / "README.md").write_text("A question answering agent", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    tools = {}
    def create_tool(_description, func, name):
        tools[name] = func
        return func
    def agents(**_kwargs):
        def answer(_prompt):
            assert "question answering" in tools["read_file"]("README.md")
            with pytest.raises(ValueError, match="credential"):
                tools["read_file"](".env")
            return json.dumps({
                "overview": "問答專案", "project_name": "Example",
                "agent_name": "Example Agent", "model_name": None,
                "identity_status": "inferred", "candidates": ["model-a"],
                "evidence": ["README.md"],
            })
        return answer
    monkeypatch.setitem(sys.modules, "akasha",
                        SimpleNamespace(create_tool=create_tool, agents=agents))
    result = describe_project(tmp_path, model="fake:model", env_file=".env")
    assert result["overview"] == "問答專案"
    assert result["identity_status"] == "inferred"
    assert result["evidence"] == ["README.md"]
    assert len(result["evidence_sha256"]["README.md"]) == 64
