"""Browser-owner dispatch, private output and production worker lifecycle."""
from __future__ import annotations

from types import SimpleNamespace
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from lladar.browser_agent_loop import BrowserAgentError, run_browser_agent
from lladar.providers.akasha import resolve_model_destination


def test_injected_agent_keeps_tools_on_owner_thread_and_does_not_redirect_host_progress(capsys):
    owner = threading.get_ident()
    events = []
    def inspect_browser() -> str:
        events.append(("tool", threading.get_ident()))
        print("HOST-PROGRESS")
        return "observed"
    def factory(**settings):
        print("private-provider-initializer")
        def agent(_prompt):
            print("private-provider-output")
            assert settings["tools"]["inspect_browser"]() == "observed"
        return agent
    run_browser_agent(factory=factory, tools={"inspect_browser": inspect_browser},
        prompt="explore", model="fixture:model", env_file="fixture.env",
        profile=SimpleNamespace(max_input_tokens=1000, max_output_tokens=1000),
        max_rounds=5, timeout=5, model_started=lambda: events.append(("model", threading.get_ident())),
        tool_started=lambda name: events.append(("debit", threading.get_ident())), pump=lambda: None)
    assert events == [("model", owner), ("debit", owner), ("tool", owner)]
    captured = capsys.readouterr()
    assert captured.out == "HOST-PROGRESS\n"
    assert captured.err == ""


@contextmanager
def model_site(*, arguments=None, block=False):
    calls, entered, disconnected = [], threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            entered.set()
            if block:
                self.connection.settimeout(10)
                try:
                    if self.connection.recv(1) == b"":
                        disconnected.set()
                except ConnectionResetError:
                    disconnected.set()
                except OSError:
                    pass
                return
            if len(calls) == 1:
                message = {"role": "assistant", "content": "", "tool_calls": [{"id": "tool-1", "type": "function",
                    "function": {"name": "inspect_browser", "arguments": json.dumps(arguments or {})}}]}
                finish = "tool_calls"
            else:
                message, finish = {"role": "assistant", "content": "Integration completed."}, "stop"
            body = {"id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture",
                    "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, entered, disconnected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def production_settings(tmp_path, endpoint, tools, events, **overrides):
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(f"OPENAI_API_KEY=synthetic-key\nOPENAI_BASE_URL={endpoint}\n", encoding="utf-8")
    expected = resolve_model_destination("openai:fixture", str(env_file), 256)
    settings = dict(factory=None, tools=tools, prompt="Inspect the authorized browser with tools.",
        model="openai:fixture", env_file=str(env_file),
        profile=SimpleNamespace(max_input_tokens=10000, max_output_tokens=256), max_rounds=5, timeout=60,
        model_started=lambda: events.append(("model", threading.get_ident())),
        tool_started=lambda name: events.append(("debit", threading.get_ident())), pump=lambda: None,
        expected_destination=expected)
    settings.update(overrides)
    return settings


def test_real_akasha_process_brokers_each_model_call_and_tool_on_owner_thread(tmp_path, capsys):
    owner, events = threading.get_ident(), []
    def inspect_browser() -> str:
        events.append(("tool", threading.get_ident()))
        print("HOST-PROGRESS")
        return '{"visible": "complete browser observation"}'
    with model_site() as (endpoint, calls, _entered, _disconnected):
        run_browser_agent(**production_settings(tmp_path, endpoint, {"inspect_browser": inspect_browser}, events))
    assert events == [("model", owner), ("debit", owner), ("tool", owner), ("model", owner)]
    assert len(calls) == 2
    assert "complete browser observation" in json.dumps(calls[1])
    captured = capsys.readouterr()
    assert captured.out == "HOST-PROGRESS\n"
    assert captured.err == ""


def test_real_worker_budget_denial_prevents_second_provider_request(tmp_path):
    events = []
    def model_started():
        from lladar.browser_target import BrowserDiscoveryError
        if events:
            raise BrowserDiscoveryError("discovery_budget_exhausted")
        events.append(True)
    def inspect_browser() -> str:
        return "observed"
    with model_site() as (endpoint, calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {"inspect_browser": inspect_browser}, [], model_started=model_started)
        with pytest.raises(BrowserAgentError) as caught:
            run_browser_agent(**settings)
    assert caught.value.reason == "browser_agent_budget_exhausted"
    assert len(calls) == 1


@pytest.mark.parametrize("known", [True, False])
def test_actual_worker_returns_recoverable_safe_tool_feedback_to_the_agent(tmp_path, known):
    from lladar.browser_target import BrowserDiscoveryError
    def inspect_browser() -> str:
        if known:
            raise BrowserDiscoveryError("unmarked_submission")
        raise RuntimeError("synthetic-private-tool-error")
    with model_site() as (endpoint, calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {"inspect_browser": inspect_browser}, [])
        run_browser_agent(**settings)
    assert len(calls) == 2
    tool_messages = [message for message in calls[1]["messages"] if message["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"]) == {
        "status": "needs_revision", "reason": "unmarked_submission" if known else "tool_failed"}
    assert "synthetic-private-tool-error" not in json.dumps(calls)


@pytest.mark.parametrize("reason", [
    "backend_origin_unobserved", "event_page_invalid", "protocol_resource_unavailable",
    "protocol_resource_page_invalid", "protocol_dependency_integrity_failed",
    "synthetic-private-resource-error",
])
def test_agent_receives_precise_closed_protocol_feedback(reason):
    from lladar.protocol_resources import ProtocolResourceError
    feedback = []
    def inspect_browser() -> str:
        raise ProtocolResourceError(reason)
    def factory(**settings):
        def agent(_prompt):
            feedback.append(json.loads(settings["tools"]["inspect_browser"]()))
        return agent
    run_browser_agent(factory=factory, tools={"inspect_browser": inspect_browser},
        prompt="explore", model="fixture:model", env_file="fixture.env",
        profile=SimpleNamespace(max_input_tokens=1000, max_output_tokens=1000),
        max_rounds=5, timeout=5, model_started=lambda: None,
        tool_started=lambda name: None, pump=lambda: None)
    assert feedback == [{"status": "needs_revision", "reason":
        "tool_failed" if reason == "synthetic-private-resource-error" else reason}]


def test_actual_worker_stops_before_another_model_call_when_authentication_is_required(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    def inspect_browser() -> str:
        raise BrowserDiscoveryError("browser_authentication_required")
    with model_site() as (endpoint, calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {"inspect_browser": inspect_browser}, [])
        with pytest.raises(BrowserAgentError) as caught:
            run_browser_agent(**settings)
    assert caught.value.reason == "browser_authentication_required"
    assert len(calls) == 1


def test_real_worker_rechecks_destination_before_any_provider_call(tmp_path):
    with model_site() as (endpoint, calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {}, [])
        env_file = tmp_path / "synthetic.env"
        env_file.write_text(f"OPENAI_API_KEY=synthetic-key\nOPENAI_BASE_URL={endpoint}/other-tenant\n", encoding="utf-8")
        with pytest.raises(BrowserAgentError) as caught:
            run_browser_agent(**settings)
    assert caught.value.reason == "browser_agent_destination_changed"
    assert calls == []


def test_cancellation_terminates_inflight_provider_process_and_closes_its_connection(tmp_path):
    with model_site(block=True) as (endpoint, calls, entered, disconnected):
        def pump():
            if entered.is_set():
                raise KeyboardInterrupt
        settings = production_settings(tmp_path, endpoint, {}, [], pump=pump)
        with pytest.raises(KeyboardInterrupt):
            run_browser_agent(**settings)
        assert disconnected.wait(2), "the cancelled worker must release the in-flight provider connection"
    assert len(calls) == 1


def test_parent_pump_deadline_is_fatal_and_preserved_when_it_terminates_the_worker(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    with model_site(block=True) as (endpoint, calls, entered, disconnected):
        def pump():
            if entered.is_set():
                raise BrowserDiscoveryError("browser_agent_time_exhausted")
        settings = production_settings(tmp_path, endpoint, {}, [], pump=pump)
        with pytest.raises(BrowserAgentError, match="^browser_agent_time_exhausted$"):
            run_browser_agent(**settings)
        assert disconnected.wait(2)
    assert len(calls) == 1


def test_startup_timeout_terminates_process_without_calling_provider(tmp_path):
    with model_site() as (endpoint, calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {}, [], timeout=0.05)
        started = time.monotonic()
        with pytest.raises(BrowserAgentError, match="^browser_agent_time_exhausted$"):
            run_browser_agent(**settings)
        assert time.monotonic() - started < 5
    assert calls == []


def test_oversized_worker_rpc_is_rejected_without_executing_browser_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LLADAR_PROVIDER_WORKER_BYTES", "4096")
    invoked = []
    def inspect_browser(value: str) -> str:
        invoked.append(True)
        return "must not run"
    with model_site(arguments={"value": "x" * 8000}) as (endpoint, _calls, _entered, _disconnected):
        settings = production_settings(tmp_path, endpoint, {"inspect_browser": inspect_browser}, [])
        with pytest.raises(BrowserAgentError, match="^browser_agent_ipc_limit$"):
            run_browser_agent(**settings)
    assert invoked == []
