"""Portable export exercised against real HTTP and isolated Python processes."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from lladar.auto_adapter import AutoAdapter
from lladar.cli import main
from lladar.command_log import command_log
from lladar.portable_api import bundle_environment, bundle_readme, required_environment, write_bundle
from lladar.language import Language, current_language, language_scope


SOURCE = b'''import json, os, sys
from lladar_api_runtime import request
REQUIRED_ENV = ["EXAMPLE_API_KEY"]
r = json.load(sys.stdin)
with request("chat", data=json.dumps({"question": r["message"]}).encode(),
             headers={"X-API-Key": os.environ["EXAMPLE_API_KEY"], "Content-Type": "application/json"}) as response:
    answer = json.load(response)["answer"]
print(json.dumps({"request_id": r["request_id"], "output": answer, "observation": "HTTP chat response"}))
'''


@contextmanager
def api(*, redirect=None, auth_header="X-API-Key", auth_value="recipient-private-key", denial=403):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append({"path": self.path, "key": self.headers.get("X-API-Key")})
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if redirect:
                self.send_response(307)
                self.send_header("Location", redirect)
                self.end_headers()
                return
            status = 200 if auth_header is None or self.headers.get(auth_header) == auth_value else denial
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps({"answer": "reply: " + payload["question"],
                                        "private": "server-private-secret"}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def launch(bundle, cwd, url, *, key="recipient-private-key"):
    environment = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    if key is not None:
        environment["EXAMPLE_API_KEY"] = key
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.run([sys.executable, "-I", "-S", str(bundle / "adapter.py"),
                           "--service-url", url, "--message", "hello"],
                          cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10)


def test_export_moves_to_another_host_without_project_or_installed_packages(tmp_path):
    bundle = tmp_path / "creator" / "client"
    write_bundle(bundle, SOURCE)
    recipient = tmp_path / "recipient"
    recipient.mkdir()
    moved = recipient / "api-client"
    shutil.move(str(bundle), moved)
    with api() as (first, calls1), api() as (second, calls2):
        for url in (first, second):
            result = launch(moved, recipient, url)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["output"] == "reply: hello"
        assert len(calls1) == len(calls2) == 1
    assert {p.name for p in moved.iterdir()} == {"adapter.py", "README.md", ".env.example"}
    for path in moved.iterdir():
        assert b"recipient-private-key" not in path.read_bytes()
        assert first.encode() not in path.read_bytes()
    assert required_environment((moved / "adapter.py").read_bytes()) == ["EXAMPLE_API_KEY"]


def test_python_file_alone_runs_without_readme_or_isolation_flags(tmp_path):
    bundle = tmp_path / "client"
    write_bundle(bundle, SOURCE)
    recipient = tmp_path / "recipient"
    recipient.mkdir()
    standalone = recipient / "my_client.py"
    shutil.copyfile(bundle / "adapter.py", standalone)
    environment = os.environ.copy()
    environment.update(EXAMPLE_API_KEY="recipient-private-key", NO_PROXY="127.0.0.1,localhost")
    with api() as (url, calls):
        result = subprocess.run([sys.executable, str(standalone), "--service-url", url, "--message", "hello"],
                                cwd=recipient, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["output"] == "reply: hello"
        assert len(calls) == 1
    assert {p.name for p in recipient.iterdir()} == {"my_client.py"}


@pytest.mark.parametrize("module_import", [False, True])
def test_inlining_preserves_import_aliases_future_annotations_and_module_globals(tmp_path, module_import):
    imported = b"import lladar_api_runtime as api" if module_import else b"from lladar_api_runtime import request as send"
    call = b"api.request(" if module_import else b"send("
    source = SOURCE.replace(b"from lladar_api_runtime import request", imported).replace(b"with request(", b"with " + call)
    source = source.replace(b'r = json.load(sys.stdin)', b'r = json.load(sys.stdin)\ndef question() -> UnknownAnnotation:\n    return r["message"]')
    source = b"from __future__ import annotations\n" + source.replace(b'{"question": r["message"]}', b'{"question": question()}')
    source += b"\nraise SystemExit(0)\n"
    bundle = tmp_path / "client"
    write_bundle(bundle, source)
    with api() as (url, calls):
        result = launch(bundle, tmp_path, url)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["output"] == "reply: hello"
        assert len(calls) == 1


def test_standalone_file_is_idempotent_and_can_be_reused_by_lladar(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    bundle = tmp_path / "client"
    write_bundle(bundle, SOURCE)
    source = (bundle / "adapter.py").read_bytes()
    second = tmp_path / "copy"
    write_bundle(second, source)
    assert (second / "adapter.py").read_bytes() == source
    project = tmp_path / "project"
    project.mkdir()
    with api() as (url, calls):
        builder = AutoAdapter(project, python=Path(sys.executable), env_file=None,
                              model="unused", service_url=url, verbose=False)
        builder.prepare_existing(second / "adapter.py", ["hello"])
        assert builder.portable
        assert builder.report["status"] == "verified"
        assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_credentials_are_required_before_request_and_forbidden_is_sanitized(tmp_path, status):
    bundle = tmp_path / "client"
    write_bundle(bundle, SOURCE)
    with api(denial=status) as (url, calls):
        result = launch(bundle, tmp_path, url, key=None)
        assert result.returncode == 1
        assert "EXAMPLE_API_KEY" in result.stderr
        assert not calls
        result = launch(bundle, tmp_path, url, key="wrong-private-key")
        assert result.returncode == 1
        assert str(status) in result.stderr
        assert not result.stdout
        assert "server-private-secret" not in result.stderr
        assert "wrong-private-key" not in result.stderr


@pytest.mark.parametrize("header,expression,value", [
    ("Authorization", '"Bearer " + os.environ["EXAMPLE_API_KEY"]', "Bearer recipient-private-key"),
    ("Cookie", '"session=" + os.environ["EXAMPLE_API_KEY"]', "session=recipient-private-key"),
    (None, '""', None),
])
def test_authentication_is_project_specific_rather_than_fixed_to_api_key(tmp_path, header, expression, value):
    source = SOURCE.replace(b'"X-API-Key": os.environ["EXAMPLE_API_KEY"]',
                            (json.dumps(header or "X-Public") + ": " + expression).encode())
    if header is None:
        source = source.replace(b'REQUIRED_ENV = ["EXAMPLE_API_KEY"]', b'REQUIRED_ENV = []')
    bundle = tmp_path / "client"
    write_bundle(bundle, source)
    with api(auth_header=header, auth_value=value) as (url, calls):
        result = launch(bundle, tmp_path, url, key=None if header is None else "recipient-private-key")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["output"] == "reply: hello"
        assert len(calls) == 1


def test_redirect_does_not_forward_credentials_or_resubmit(tmp_path):
    bundle = tmp_path / "client"
    write_bundle(bundle, SOURCE)
    with api() as (target, target_calls), api(redirect=target + "/chat") as (url, calls):
        result = launch(bundle, tmp_path, url)
        assert result.returncode == 1
        assert "307" in result.stderr
        assert len(calls) == 1
        assert not target_calls


def test_auto_adapter_verifies_without_project_and_exports_existing_source(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "private.py").write_text("raise RuntimeError('must never import project')")
    original = tmp_path / "adapter.py"
    original.write_bytes(SOURCE)
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with api() as (url, calls):
        builder = AutoAdapter(project, python=Path(sys.executable), env_file=None,
                              model="unused", service_url=url, portable=True, verbose=False)
        builder.prepare_existing(original, ["probe"])
        assert builder.answer("hello", "q1") == "reply: hello"
        assert len(calls) == 2
        assert builder.report["status"] == "verified"


@pytest.mark.parametrize("custom_destination,lang,logged", [
    (False, "en", False),
    (True, "en", True),
    (False, "zh-TW", True),
    (True, "zh-TW", False),
    (True, "fr", True),
])
def test_runner_exports_a_verified_bundle_and_records_it(tmp_path, monkeypatch, isolated_target_python, custom_destination, lang, logged):
    # Cover all destination/log combinations and each language here; detailed
    # language, cache and document preservation checks live in the bundle tests.
    import akasha
    from lladar.runner import run_agent
    from test_simple_pipeline import StrategySkillAgent

    translations = []
    def ask(**kwargs):
        translations.append(kwargs)
        assert f"language {lang}" in kwargs["system_prompt"]
        return lambda prompt: json.dumps({key: "translated " + value for key, value in json.loads(prompt).items()})
    monkeypatch.setattr(akasha, "ask", ask)
    project = tmp_path / "project"
    project.mkdir()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "reply", "actual_response": None}) + "\n")
    adapter = tmp_path / "source.py"
    adapter.write_bytes(SOURCE)
    output = tmp_path / "responses.jsonl"
    bundle = tmp_path / "api-client" if custom_destination else Path(str(output) + ".api-adapter")
    options = {"adapt": adapter, "export_adapter": bundle} if custom_destination else {}
    if not custom_destination:
        monkeypatch.setattr(AutoAdapter, "prepare", lambda self, probes, **kwargs: self.prepare_existing(adapter, probes))
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    log = tmp_path / "run.log"
    with api() as (url, calls), command_log(log if logged else None):
        assert run_agent(dataset, output, project=project, service_url=url,
                         target_python=isolated_target_python, verbose=logged,
                         strategy_agent_factory=StrategySkillAgent, lang=lang, **options) == 1
        assert json.loads(output.read_text())["actual_response"] == "reply: hello"
        assert json.loads(Path(str(output) + ".run.json").read_text())["target"]["portable_export"] == str(bundle.resolve())
        recipient = tmp_path / "recipient"
        recipient.mkdir()
        result = launch(bundle, recipient, url)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["output"] == "reply: hello"
        readme = (bundle / "README.md").read_text(encoding="utf-8")
        assert readme.startswith("# Portable" if lang == "en" else "# translated")
        assert len(translations) == (0 if lang == "en" else 1)
        if lang != "en":
            assert (Path(str(output) + ".work") / "portable-documents.json").is_file()
    if logged:
        assert log.stat().st_size > 0
        assert "reply: hello" in log.read_text(encoding="utf-8")


def test_fresh_generation_receives_portable_contract_and_verifies_real_http(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("# POST /chat uses X-API-Key from EXAMPLE_API_KEY\n")
    monkeypatch.setenv("EXAMPLE_API_KEY", "recipient-private-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    prompts = []
    with api() as (url, calls):
        def agent_factory(**kwargs):
            def respond(prompt):
                prompts.append(prompt)
                if len(prompts) == 1:
                    return json.dumps({"candidates": [{"id": "chat", "label": "Chat",
                        "entrypoint": "POST /chat", "public_boundary": True,
                        "rationale": "Public HTTP endpoint", "flow": ["HTTP", "agent", "answer"],
                        "output": "answer field", "transport": "http",
                        "service": {"mode": "existing", "base_url": url, "command": [],
                            "readiness_path": "", "request": "POST /chat, JSON question, X-API-Key header",
                            "response": "JSON answer", "coverage": ["HTTP", "agent"], "excluded": [], "missing": []},
                        "evidence": [{"path": "app.py", "line": 1, "quote": "POST /chat"}]}],
                        "unresolved": [], "summary": "One public interface"})
                builder.explorer.write_harness("adapter.py", SOURCE.decode())
                return json.dumps({"harness": "adapter.py", "explanation": "Public API", "blockers": []})
            return respond

        builder = AutoAdapter(project, python=Path(sys.executable), env_file=None,
                              model="unused", service_url=url, portable=True, graphify=False,
                              verbose=False, agent_factory=agent_factory)
        builder.prepare(["probe"], interactive=False)
        assert builder.report["status"] == "verified"
        assert "PORTABLE API EXPORT CONTRACT" in prompts[1]
        assert len(calls) == 1


def test_launcher_never_echoes_generated_exception_or_diagnostics_with_credentials(tmp_path):
    bundle = tmp_path / "client"
    source = b'''import sys
REQUIRED_ENV = []
print("private-diagnostic", file=sys.stderr)
raise RuntimeError("API HTTP 403: private-exception")
'''
    write_bundle(bundle, source)
    result = launch(bundle, tmp_path, "http://127.0.0.1:1")
    assert result.returncode == 1
    assert "private-" not in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("source", [b"import project\nREQUIRED_ENV=[]", b"import requests\nREQUIRED_ENV=[]", b"import os\n"])
def test_export_rejects_undeclared_credentials_and_external_imports(source):
    with pytest.raises(ValueError):
        required_environment(source)


def test_export_does_not_overwrite_existing_directory(tmp_path):
    with pytest.raises(FileExistsError):
        write_bundle(tmp_path, SOURCE)


@pytest.mark.parametrize("lang,heading", [
    ("en", "# Portable API adapter"), ("zh-TW", "# 可攜式 API adapter"),
    ("ja", "# 翻訳 Portable API adapter"), ("fr", "# Traduction Portable API adapter")])
def test_export_localizes_readme_and_documents_environment_without_values(tmp_path, monkeypatch, lang, heading):
    import akasha
    calls = []

    def ask(**kwargs):
        calls.append(kwargs)
        assert f"language {lang}" in kwargs["system_prompt"]
        def translate(prompt):
            assert "creator-private-key" not in prompt
            assert "import json" not in prompt
            assert "py -3.11" not in prompt
            texts = json.loads(prompt)
            prefix = {"zh-TW": "必填說明 ", "ja": "翻訳 ", "fr": "Traduction "}[lang]
            return json.dumps({key: "可攜式 API adapter" if lang == "zh-TW" and value == "Portable API adapter"
                               else prefix + value for key, value in texts.items()})
        return translate

    monkeypatch.setattr(akasha, "ask", ask)
    monkeypatch.setenv("EXAMPLE_API_KEY", "creator-private-key")
    bundle = tmp_path / "bundle"
    cache = tmp_path / "documentation.json"
    with language_scope(Language(lang)):
        write_bundle(bundle, SOURCE, model="unused", documentation_cache=cache)
        write_bundle(bundle, SOURCE, model="unused", documentation_cache=cache, resume=True)
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    example = (bundle / ".env.example").read_text(encoding="utf-8")
    assert readme.startswith(heading)
    assert ".env.example" in readme
    assert "LLADAR_SERVICE_URL=https://api.example.com" in example
    assert "LLADAR_API_TIMEOUT=60" in example
    assert "EXAMPLE_API_KEY=\n" in example
    assert "必填" in example if lang == "zh-TW" else "Required" in example
    assert "creator-private-key" not in readme + example
    assert len(calls) == (0 if lang == "en" else 1)
    assert "$env:EXAMPLE_API_KEY = 'REPLACE_WITH_YOUR_CREDENTIAL'" in readme
    assert "py -3.11 --version" in readme
    assert '"request_id":"q1"' in readme
    assert 'LLADAR_API_TIMEOUT' in readme
    assert "3.11+" in readme
    assert "-I -S" in readme
    assert re.findall(r"```.*?```", readme, re.S) == re.findall(
        r"```.*?```", bundle_readme(["EXAMPLE_API_KEY"]), re.S)
    assignments = lambda text: [line for line in text.splitlines() if line and not line.startswith("#")]
    assert assignments(example) == assignments(bundle_environment(["EXAMPLE_API_KEY"]))


@pytest.mark.parametrize("failure", ["missing", "placeholder", "duplicate", "multiline", "json"])
def test_invalid_translation_never_publishes_a_bundle(tmp_path, monkeypatch, failure):
    import akasha
    def translate(prompt):
        texts = json.loads(prompt)
        if failure == "missing":
            texts.pop("0")
        elif failure == "json":
            return "not JSON"
        else:
            key = next(key for key, value in texts.items() if "[[T0]]" in value)
            texts[key] = {"placeholder": "lost placeholder", "duplicate": texts[key] + " [[T0]]",
                          "multiline": texts[key] + "\ninvalid"}[failure]
        return json.dumps(texts)
    monkeypatch.setattr(akasha, "ask", lambda **kwargs: translate)
    from lladar.exceptions import ProviderError
    with pytest.raises((ValueError, ProviderError)):
        write_bundle(tmp_path / "bundle", SOURCE, lang="zh-TW", model="unused")
    assert not (tmp_path / "bundle").exists()


def test_unauthenticated_export_has_no_invented_credentials(tmp_path):
    source = SOURCE.replace(b'REQUIRED_ENV = ["EXAMPLE_API_KEY"]', b'REQUIRED_ENV = []')
    write_bundle(tmp_path / "bundle", source)
    example = (tmp_path / "bundle" / ".env.example").read_text(encoding="utf-8")
    assert "EXAMPLE_API_KEY" not in example


def test_resume_accepts_only_identical_export_and_never_overwrites(tmp_path):
    bundle = tmp_path / "bundle"
    write_bundle(bundle, SOURCE)
    write_bundle(bundle, SOURCE, resume=True)
    (bundle / "adapter.py").write_text("changed")
    with pytest.raises(ValueError, match="differs"):
        write_bundle(bundle, SOURCE, resume=True)
    assert (bundle / "adapter.py").read_text() == "changed"


@pytest.mark.parametrize("args", [[], ["--project", "project"], ["--service-url", "http://localhost:8000"],
    ["--page-url", "http://localhost:8000"],
    ["--project", "project", "--service-url", "http://localhost:8000", "--situation-config", "situation.json"]])
def test_cli_rejects_unsupported_export_modes(args):
    with pytest.raises(SystemExit):
        main(["run-agent", "--export-adapter", "bundle", *args])


def test_cli_forwards_export_destination(monkeypatch, tmp_path):
    received = {}
    monkeypatch.chdir(tmp_path)
    def run(*args, **kwargs):
        received.update(kwargs)
        received["language"] = current_language().lang
        return 1
    monkeypatch.setattr("lladar.cli.run_agent", run)
    assert main(["run-agent", "dataset.jsonl", "--project", "project", "--service-url", "http://localhost:8000",
                 "--export-adapter", "client", "--lang", "zh-TW"]) == 0
    assert received["export_adapter"] == "client"
    assert received["language"] == "zh-TW"
