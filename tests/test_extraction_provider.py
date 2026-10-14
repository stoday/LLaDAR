from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from lladar.answer_extraction import ExtractionError
from lladar.extraction_provider import AkashaExtractionProvider, _extract_message
from lladar.providers.akasha import AkashaProvider


@pytest.mark.parametrize('model', ['gemini:fixture', 'openai:fixture', 'anthropic:fixture', 'ollama:fixture'])
def test_extraction_routes_selected_model_through_akasha_without_tools(monkeypatch, model):
    from akasha.helper import handle_objects
    calls = []
    message = SimpleNamespace(content='{"status":"final","text":"原文"}', response_metadata={'finish_reason': 'STOP'})
    class Model:
        max_retries = 5
        _llm_type = 'chat-google-generative-ai' if model.startswith('gemini:') else 'fixture'
        def invoke(self, messages, **kwargs):
            calls.append((messages, kwargs))
            assert self.max_retries in (0, 1)
            return message
    def factory(selected, **kwargs):
        assert selected == model
        assert kwargs['env_file'] == 'fixture.env'
        assert kwargs['max_output_tokens'] == 123456
        return Model()
    monkeypatch.setattr(handle_objects, 'handle_model', factory)
    provider = AkashaProvider(env_file='fixture.env', max_output_tokens=123456)
    assert provider.generate_message('complete input', system_prompt='extract only', model=model) is message
    assert len(calls) == 1
    assert [m.content for m in calls[0][0]] == ['extract only', 'complete input']
    assert calls[0][1] == {'config': {'callbacks': []}}


@pytest.mark.parametrize('finish,reason', [('MAX_TOKENS','provider_max_tokens'), ('length','provider_max_tokens'),
    ('max_tokens','provider_max_tokens'), ('SAFETY','provider_safety_blocked'),
    ('content_filter','provider_safety_blocked'), ('tool_calls','provider_output_incomplete')])
def test_partial_response_is_rejected_even_if_json_is_valid(finish, reason):
    message = SimpleNamespace(content='{"status":"final","text":"partial"}',
                              response_metadata={'finish_reason': finish},
                              usage_metadata={'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30})
    with pytest.raises(ExtractionError) as caught:
        _extract_message(message, output_bytes=1000)
    assert caught.value.reason == reason
    assert caught.value.usage == {'promptTokenCount':10,'candidatesTokenCount':20,'totalTokenCount':30}


def test_blocks_keep_whole_original_answer_and_discard_thoughts():
    text = '完整原文\n' * 60000
    message = SimpleNamespace(content=[{'type':'thinking','thinking':'private thought'},
        {'type':'text','text':json.dumps({'status':'final','text':text})}],
        response_metadata={'stop_reason':'end_turn'}, tool_calls=[])
    assert _extract_message(message, output_bytes=4000000)['result']['text'] == text


@pytest.mark.parametrize('content', ['{"status":"final","text":"cut',
    '{"status":"final","text":"a","text":"b"}', '{"status":"final","text":"a","extra":1}'])
def test_invalid_or_partial_json_is_never_accepted(content):
    with pytest.raises(ExtractionError):
        _extract_message(content, output_bytes=1000)


def test_string_models_and_explicit_nonfinal_status_remain_supported():
    assert _extract_message('{"status":"incomplete"}', output_bytes=1000) == {
        'result':{'status':'incomplete'},'usage':None}


def test_tools_cannot_become_answer_data():
    message = SimpleNamespace(content='{"status":"final","text":"a"}', tool_calls=[{'name':'evil'}])
    with pytest.raises(ExtractionError, match='provider_response_invalid'):
        _extract_message(message, output_bytes=1000)


def test_output_limit_is_enforced_without_truncation():
    with pytest.raises(ExtractionError, match='provider_output_limit'):
        _extract_message('{"status":"final","text":"abcdef"}', output_bytes=5)


def test_worker_receives_selected_model_env_and_full_large_input(monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"result":{"status":"final","text":"whole"},"usage":null}')
    monkeypatch.setattr(subprocess, 'run', run)
    body = 'complete website answer ' * 10000
    provider = AkashaExtractionProvider(model_destination='anthropic:fixture', timeout_seconds=3600,
                                       max_output_tokens=123456, env_file=tmp_path/'local.env')
    assert provider.extract({'content_type':'text/plain','body':body}, timeout_seconds=100)['result']['text'] == 'whole'
    payload = json.loads(calls[0][1]['input'])
    assert payload['model'] == 'anthropic:fixture'
    assert payload['max_output_tokens'] == 123456
    assert json.loads(payload['prompt'])['body'] == body
    assert payload['env_file'] == str(tmp_path/'local.env')
    assert calls[0][1]['timeout'] == 100
    assert calls[0][1]['env']['LANGSMITH_TRACING'] == 'false'
    assert len(calls) == 1


def test_wall_clock_deadline_still_kills_worker(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', run)
    provider = AkashaExtractionProvider(model_destination='ollama:fixture', timeout_seconds=1, max_output_tokens=65536)
    with pytest.raises(ExtractionError, match='extraction_time_exhausted'):
        provider.extract({'content_type':'text/plain','body':'answer'}, timeout_seconds=1)


@contextmanager
def akasha_site(*, finish='stop', status=200):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            response = {'id':'fixture','object':'chat.completion','created':0,'model':'fixture',
                'choices':[{'index':0,'finish_reason':finish,'message':{'role':'assistant',
                    'content':json.dumps({'status':'final','text':'完整網站原文\nsecond paragraph'})}}],
                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}
            if status != 200: response = {'error':{'message':'private error','type':'fixture'}}
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally: server.shutdown(); server.server_close(); thread.join(timeout=5)


@pytest.mark.parametrize('model,key,url_key', [('openai:fixture','OPENAI_API_KEY','OPENAI_BASE_URL'),
    ('azure:fixture','AZURE_OPENAI_API_KEY','AZURE_OPENAI_BASE_URL')])
def test_real_akasha_worker_uses_configured_loopback_endpoint(tmp_path, model, key, url_key):
    with akasha_site() as (endpoint, requests):
        env_file = tmp_path/'fixture.env'
        env_file.write_text(f'{key}=fixture-key\n{url_key}={endpoint}\n', encoding='utf-8')
        provider = AkashaExtractionProvider(model_destination=model, timeout_seconds=60,
                                            max_output_tokens=65536, env_file=env_file)
        result = provider.extract({'content_type':'text/plain','body':'complete capture'},timeout_seconds=60)
    assert result['result']['text'] == '完整網站原文\nsecond paragraph'
    assert result['usage']['totalTokenCount'] == 30
    assert len(requests) == 1
    assert requests[0]['model'] == 'fixture'
    assert requests[0].get('max_completion_tokens', requests[0].get('max_tokens')) == 65536
    assert 'tools' not in requests[0]


def test_real_akasha_worker_rejects_length_finish(tmp_path):
    with akasha_site(finish='length') as (endpoint, requests):
        env_file = tmp_path/'fixture.env'
        env_file.write_text(f'OPENAI_API_KEY=fixture-key\nOPENAI_BASE_URL={endpoint}\n',encoding='utf-8')
        provider = AkashaExtractionProvider(model_destination='openai:fixture',timeout_seconds=60,
                                            max_output_tokens=65536,env_file=env_file)
        with pytest.raises(ExtractionError,match='provider_max_tokens'):
            provider.extract({'content_type':'text/plain','body':'answer'},timeout_seconds=60)
    assert len(requests) == 1


def test_real_akasha_worker_does_not_retry_provider_errors(tmp_path):
    with akasha_site(status=429) as (endpoint, requests):
        env_file = tmp_path/'fixture.env'
        env_file.write_text(f'OPENAI_API_KEY=fixture-key\nOPENAI_BASE_URL={endpoint}\n',encoding='utf-8')
        provider = AkashaExtractionProvider(model_destination='openai:fixture',timeout_seconds=60,
                                            max_output_tokens=65536,env_file=env_file)
        with pytest.raises(ExtractionError,match='provider_rate_limited'):
            provider.extract({'content_type':'text/plain','body':'answer'},timeout_seconds=60)
    assert len(requests) == 1


def test_consented_identity_reaches_worker_without_entering_the_capture_prompt(monkeypatch):
    expected = {"origin": "https://approved.example", "model": "fixture", "configuration_sha256": "a" * 64}
    calls = []
    def run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=b'{"result":{"status":"final","text":"whole"},"usage":null}')
    monkeypatch.setattr(subprocess, "run", run)
    provider = AkashaExtractionProvider(model_destination="openai:fixture", timeout_seconds=60,
        max_output_tokens=65536, expected_destination=expected)
    expected["origin"] = "https://changed.example"
    provider.extract({"content_type": "text/plain", "body": "complete source material"}, timeout_seconds=60)
    assert calls[0]["expected_destination"]["origin"] == "https://approved.example"
    assert "approved.example" not in calls[0]["prompt"]


@pytest.mark.parametrize("same_origin", [False, True])
def test_real_worker_rejects_endpoint_or_deployment_drift_before_any_request(tmp_path, same_origin):
    from lladar.providers.akasha import resolve_model_destination

    with akasha_site() as (approved, approved_requests), akasha_site() as (changed, changed_requests):
        env_file = tmp_path / "fixture.env"
        env_file.write_text(f"OPENAI_API_KEY=fixture-key\nOPENAI_BASE_URL={approved}\n", encoding="utf-8")
        expected = resolve_model_destination("openai:fixture", str(env_file), 65536)
        provider = AkashaExtractionProvider(model_destination="openai:fixture", timeout_seconds=60,
            max_output_tokens=65536, env_file=env_file, expected_destination=expected)
        final_endpoint = approved + "/different-deployment" if same_origin else changed
        env_file.write_text(f"OPENAI_API_KEY=fixture-key\nOPENAI_BASE_URL={final_endpoint}\n", encoding="utf-8")
        with pytest.raises(ExtractionError) as caught:
            provider.extract({"content_type": "text/plain", "body": "private captured answer"}, timeout_seconds=60)
        assert caught.value.reason == "provider_destination_changed"
    assert approved_requests == changed_requests == []


def test_real_worker_accepts_consented_actual_initialized_endpoint(tmp_path):
    from lladar.providers.akasha import resolve_model_destination

    with akasha_site() as (endpoint, requests):
        env_file = tmp_path / "fixture.env"
        env_file.write_text(f"OPENAI_API_KEY=fixture-key\nOPENAI_BASE_URL={endpoint}\n", encoding="utf-8")
        expected = resolve_model_destination("openai:fixture", str(env_file), 65536)
        provider = AkashaExtractionProvider(model_destination="openai:fixture", timeout_seconds=60,
            max_output_tokens=65536, env_file=env_file, expected_destination=expected)
        result = provider.extract({"content_type": "text/plain", "body": "complete capture"}, timeout_seconds=60)
    assert result["result"]["status"] == "final"
    assert len(requests) == 1
