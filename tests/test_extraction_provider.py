from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from lladar.answer_extraction import ExtractionError
from lladar.extraction_provider import GeminiExtractionProvider


@contextmanager
def provider_site(*, status=200, body=None, drip=False, location=None):
    requests = []
    disconnected = threading.Event()
    shutdown = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, self.headers.get('x-goog-api-key'), request))
            response = json.dumps((body(len(requests)) if callable(body) else body) if body is not None else {
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{
                    'text': json.dumps({'status': 'final', 'text': '不知道。'}),
                }]}}],
            }).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(1000000 if drip else len(response)))
            if location is not None:
                self.send_header('Location', location)
            self.end_headers()
            try:
                if drip:
                    while not shutdown.wait(0.02):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                else:
                    self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                disconnected.set()

        def do_GET(self):
            requests.append((self.path, self.headers.get('x-goog-api-key'), {}))
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, disconnected
    finally:
        shutdown.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_provider_sends_real_body_once_and_returns_only_answer_and_usage(capsys):
    with provider_site() as (endpoint, requests, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        result = provider.extract({"content_type": "text/plain", "body": "不知道。"}, timeout_seconds=5)
    assert result == {"result": {"status": "final", "text": "不知道。"}, "usage": None}
    assert len(requests) == 1
    _, key, request = requests[0]
    assert key == "fixture-key"
    assert request["generationConfig"]["maxOutputTokens"] == 8192
    assert request["generationConfig"]["candidateCount"] == 1
    assert request["generationConfig"]["responseMimeType"] == "application/json"
    assert not request.get("tools")
    assert "不知道。" in request["contents"][0]["parts"][0]["text"]
    assert "fixture-key" not in json.dumps(request)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status,reason", [(302,"provider_redirect_refused"),(307,"provider_redirect_refused"),
    (401,"provider_authentication_failed"),(403,"provider_authentication_failed"),(404,"provider_model_unavailable"),
    (429,"provider_rate_limited"),(500,"provider_service_unavailable"),(400,"provider_request_rejected")])
def test_http_failure_is_safe_and_never_retried(status, reason):
    with provider_site(status=status, location="/exfil", body={"error":"private-provider-message"}) as (endpoint, requests, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        with pytest.raises(ExtractionError) as caught:
            provider.extract({"content_type":"text/plain", "body":"raw answer"}, timeout_seconds=5)
    assert caught.value.reason == reason
    assert len(requests) == 1
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("finish,reason", [("MAX_TOKENS","provider_max_tokens"),("SAFETY","provider_safety_blocked"),
                                          ("private-unknown-finish", "provider_output_incomplete")])
def test_finish_categories_do_not_collapse_or_echo_provider_text(finish, reason):
    with provider_site(body={"candidates":[{"finishReason":finish,"content":{"parts":[{"text":"private-partial"}]}}]}) as (endpoint, requests, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        with pytest.raises(ExtractionError) as caught:
            provider.extract({"content_type":"text/plain", "body":"raw answer"}, timeout_seconds=5)
    assert caught.value.reason == reason
    assert len(requests) == 1
    assert "private" not in str(caught.value)


def test_deadline_kills_dripping_worker_and_does_not_retry():
    with provider_site(drip=True) as (endpoint, requests, disconnected):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=2,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        started = time.monotonic()
        with pytest.raises(ExtractionError, match="extraction_time_exhausted"):
            provider.extract({"content_type":"text/plain", "body":"raw answer"}, timeout_seconds=2)
        assert time.monotonic() - started < 4
        assert len(requests) == 1
        assert disconnected.wait(2)


@pytest.mark.parametrize("model_input", [
    {"body":"x", "content_type":"text/plain", "expected_answer":"secret"},
    {"body":"x"*123000, "content_type":"text/plain"},
    {"body":"fixture-key", "content_type":"text/plain"},
])
def test_invalid_oversized_or_credential_bearing_input_is_not_transmitted(model_input):
    with provider_site() as (endpoint, requests, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        with pytest.raises(ExtractionError):
            provider.extract(model_input, timeout_seconds=5)
    assert not requests


@pytest.mark.parametrize("parts", [
    [{"functionCall":{"name":"unsafe"}}],
    [{"text":'{"source":"print(123)"}'}],
    [{"text":'{"status":"final","text":"first","text":"second"}'}],
    [{"text":'{"status":"final","text":"private-thought"}',"thought":True}],
    [{"text":"x"*270000}],
])
def test_invalid_model_output_is_never_executed_or_partially_accepted(parts):
    with provider_site(body={"candidates":[{"finishReason":"STOP","content":{"parts":parts}}]}) as (endpoint, requests, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        with pytest.raises(ExtractionError):
            provider.extract({"content_type":"text/plain", "body":"raw answer"}, timeout_seconds=5)
    assert len(requests) == 1


def test_thoughts_signatures_and_arbitrary_usage_are_never_returned():
    body = {"candidates":[{"finishReason":"STOP","content":{"parts":[
        {"text":"private-thought", "thought":True},
        {"text":'{"status":"final","text":"不確定"}', "thoughtSignature":"private-signature"},
    ]}}], "usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":4,"unknown":"private-usage"}}
    with provider_site(body=body) as (endpoint, _, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        result = provider.extract({"content_type":"text/plain", "body":"不確定"}, timeout_seconds=5)
    assert result == {"result":{"status":"final","text":"不確定"},"usage":{"promptTokenCount":3,"candidatesTokenCount":4}}


def test_incomplete_provider_still_reports_available_safe_usage():
    body = {"candidates":[{"finishReason":"MAX_TOKENS","content":{"parts":[{"text":"private-partial"}]}}],
            "usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":8192,"private":"private-usage"}}
    with provider_site(body=body) as (endpoint, _, _):
        provider = GeminiExtractionProvider(model_destination="gemini:fixture-model", timeout_seconds=5,
                                            max_output_tokens=8192, api_key="fixture-key", endpoint=endpoint)
        with pytest.raises(ExtractionError) as caught:
            provider.extract({"content_type":"text/plain", "body":"raw answer"}, timeout_seconds=5)
    assert caught.value.reason == "provider_max_tokens"
    assert caught.value.usage == {"promptTokenCount":3,"candidatesTokenCount":8192}
