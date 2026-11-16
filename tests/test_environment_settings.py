"""Credential diagnostics must reflect loading rules without disclosing values."""
import json
import os
import sys

import pytest

from lladar import cli
from lladar.environment_settings import environment_settings
from lladar.language import Language, language_scope
from lladar.target_environment import target_environment


def test_file_and_target_have_different_effective_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "private-process-key")
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=private-file-key\nUNRELATED_SECRET=private-unrelated\n", encoding="utf-8")
    before = dict(os.environ)
    rows = environment_settings("gemini:test", env_file, include_target=True)
    assert rows["env_file"] == ".env"
    assert rows["env_file_status"] == "Exists"
    assert rows["model_env.GEMINI_API_KEY"] == "Set; source: Environment file"
    assert rows["target_env.GEMINI_API_KEY"] == "Set; source: Process environment"
    assert target_environment(type(env_file)(sys.executable), env_file)["GEMINI_API_KEY"] == "private-process-key"
    monkeypatch.setenv("GEMINI_API_KEY", "")
    rows = environment_settings("gemini:test", env_file, include_target=True)
    assert rows["target_env.GEMINI_API_KEY"] == "Unset or empty; source: Process environment"
    monkeypatch.setenv("GEMINI_API_KEY", before["GEMINI_API_KEY"])
    assert dict(os.environ) == before
    assert "private-" not in json.dumps(rows)
    assert "UNRELATED_SECRET" not in json.dumps(rows)


def test_missing_file_uses_process_but_existing_file_does_not_fill_from_process(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-process-key")
    path = tmp_path / "missing.env"
    rows = environment_settings("google:test", path)
    assert rows["env_file_status"] == "Does not exist"
    assert rows["model_env.GEMINI_API_KEY"] == "Set; source: Process environment"
    path.write_text("OTHER=ignored\n", encoding="utf-8")
    rows = environment_settings("gemi:test", path)
    assert rows["model_env.GEMINI_API_KEY"] == "Unset or empty; source: None"


def test_empty_bare_and_interpolated_file_entries(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("PRIVATE_TOKEN", "private-interpolation-key")
    path = tmp_path / ".env"
    for text in ("GEMINI_API_KEY=\n", "GEMINI_API_KEY\n"):
        path.write_text(text, encoding="utf-8")
        rows = environment_settings("gemini:test", path, include_target=True)
        assert rows["model_env.GEMINI_API_KEY"] == "Unset or empty; source: Environment file"
        assert rows["target_env.GEMINI_API_KEY"].startswith("Unset or empty;")
    path.write_text("GEMINI_API_KEY=${PRIVATE_TOKEN}\n", encoding="utf-8")
    rows = environment_settings("gemini:test", path, include_target=True)
    assert rows["model_env.GEMINI_API_KEY"] == "Set; source: Environment file (interpolated)"
    assert rows["target_env.GEMINI_API_KEY"] == "Set; source: Environment file (interpolated)"
    assert "private-interpolation-key" not in json.dumps(rows)
    assert "PRIVATE_TOKEN" not in json.dumps(rows)


@pytest.mark.parametrize("model,entries,variables", [
    ("openai:test", "OPENAI_API_KEY=private-key", ["OPENAI_API_KEY"]),
    ("azure:test", "AZURE_OPENAI_API_KEY=private-key\nAZURE_OPENAI_BASE_URL=https://private.test", ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL"]),
    ("openai:test", "AZURE_OPENAI_API_KEY=private-key\nAZURE_OPENAI_BASE_URL=https://private.test", ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL"]),
    ("claude:test", "ANTHROPIC_API_KEY=private-key", ["ANTHROPIC_API_KEY"]),
    ("gemini:test", "GEMINI_API_KEY=private-key", ["GEMINI_API_KEY"]),
])
def test_required_variables_match_installed_akasha_model_loader(tmp_path, monkeypatch, model, entries, variables):
    # Exercise the real installed loader with a client constructor that makes no
    # network calls. This catches dependency changes to file/process selection.
    from akasha.helper.handle_objects import handle_model
    import akasha.utils.models.chat as chat

    path = tmp_path / ".env"
    path.write_text(entries, encoding="utf-8")
    captured = []

    def build(provider, name, env, *args):
        captured.append(env)
        return object()

    monkeypatch.setattr(chat, "build_chat_model", build)
    for key in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for use_file in (True, False):
        for line in entries.splitlines():
            key, _, value = line.partition("=")
            monkeypatch.setenv(key, value + "-process")
        if not use_file:
            path.unlink()
        handle_model(model, env_file=str(path), verbose=False)
        rows = environment_settings(model, path)
        assert {key.removeprefix("model_env.") for key in rows if key.startswith("model_env.")} == set(variables)
        for key in variables:
            assert bool(captured[-1][key])
            assert rows["model_env." + key] == "Set; source: " + ("Environment file" if use_file else "Process environment")
        assert "private-" not in json.dumps(rows)


def test_local_and_unknown_models_do_not_list_unrelated_credentials(tmp_path):
    assert environment_settings("ollama:local", None)["model_env"] == "No required credential variables"
    assert environment_settings("custom:local", None)["model_env"] == "Provider requirements not inspected"


def test_external_paths_and_unreadable_files_are_safe(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    path = tmp_path / "private-user" / "config.env"
    rows = environment_settings("gemini:test", path)
    assert rows["env_file"] == "<external>/config.env"
    assert "private-user" not in json.dumps(rows)
    assert environment_settings("gemini:test", "../private-user/config.env")["env_file"] == "<external>/config.env"
    path.parent.mkdir()
    path.write_bytes(b"\xff\xfe\x80")
    rows = environment_settings("gemini:test", path)
    assert rows["env_file_status"] == "Cannot inspect environment file"
    assert rows["model_env"] == "Credential sources could not be inspected"


@pytest.mark.parametrize("verbose", [True, False])
def test_cli_summary_and_log_never_include_values(tmp_path, monkeypatch, capsys, verbose):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "private-process-key")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=private-file-key", encoding="utf-8")
    log = tmp_path / "run.log"
    # A missing input exercises the real CLI and logging without model calls.
    argv = ["create", "test-dataset", "--knowledge", "missing.md", "--log", str(log), "--model", "gemini:test"]
    if not verbose:
        argv.append("--no-verbose")
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    for text in (output.out + output.err, log.read_text(encoding="utf-8")):
        assert ("model_env.GEMINI_API_KEY" in text) == verbose
        assert "private-process-key" not in text
        assert "private-file-key" not in text
    assert "env_file" not in output.out


def test_summary_is_localized(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with language_scope(Language("zh-TW")):
        rows = environment_settings("gemini:test", tmp_path / "missing.env")
    assert rows["env_file_status"] == "不存在"
    assert rows["model_env.GEMINI_API_KEY"] == "未設定或空白；來源：無"
