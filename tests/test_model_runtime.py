"""Model transport and adapter repair boundaries; no external model requests."""
import json
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from lladar.exceptions import ProviderError
from test_auto_adapter import controller


def test_real_sdk_request_times_out_instead_of_waiting_for_server(tmp_path):
    import akasha
    from langchain_openai import ChatOpenAI
    from lladar.model_runtime import configure_agent, call_agent
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            calls.append(1)
            time.sleep(2)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ChatOpenAI(model='fixture', api_key='fixture', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        agent = akasha.agents(model=client, tools=[], stream=False, thinking=False, verbose=False, keep_logs=False)
        configure_agent(agent, timeout=0.5,
                        events=tmp_path / 'events.jsonl', stage='discovery')
        with pytest.raises(ProviderError, match='provider_timeout'):
            call_agent(agent, 'private prompt')
        assert calls == [1]
        text = (tmp_path / 'events.jsonl').read_text()
        assert 'provider_timeout' in text and 'private prompt' not in text
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_google_timeout_and_attempt_count_reach_request_config(tmp_path):
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage
    from lladar.model_runtime import configure_agent
    client = ChatGoogleGenerativeAI(model='gemini-3.8-flash', api_key='fixture', vertexai=True, location='')
    configure_agent(SimpleNamespace(model_obj=client), timeout=90,
                    events=tmp_path / 'events.jsonl', stage='generation')
    config = client._prepare_request([HumanMessage(content='local config probe')])['config']
    assert config.http_options.timeout == 90000
    assert config.http_options.retry_options.attempts == 1


def test_real_model_timeout_and_no_hidden_retries(tmp_path):
    from lladar.model_runtime import configure_agent, call_agent
    from langchain_openai import ChatOpenAI
    from test_extraction_provider import akasha_site

    with akasha_site(status=429) as (endpoint, requests):
        client = ChatOpenAI(model='fixture', api_key='fixture', base_url=endpoint)
        agent = SimpleNamespace(model_obj=client)
        configure_agent(agent, timeout=5, events=tmp_path / 'events.jsonl', stage='discovery')
        assert client.request_timeout == 5
        with pytest.raises(ProviderError, match='provider_rate_limited'):
            call_agent(lambda _: client.invoke('local fixture'), 'local fixture')
        assert len(requests) == 1
    text = (tmp_path / 'events.jsonl').read_text()
    assert 'provider_rate_limited' in text
    assert 'local fixture' not in text


@pytest.mark.parametrize('destination', ['stderr', 'service_log'])
def test_target_provider_failure_stops_repairs_and_preserves_evidence(tmp_path, destination):
    adapter = controller(tmp_path)
    adapter.source = ('''import json, os, sys
from pathlib import Path
event = 'LLADAR_MODEL_EVENT ' + json.dumps({'event': 'error', 'error_code': 'provider_timeout',
      'request_id': os.environ['LLADAR_REQUEST_ID']})
if DESTINATION == 'stderr':
    print(event, file=sys.stderr)
else:
    Path('lladar-service.log').write_text(event)
sys.exit(1)
'''.replace('DESTINATION', repr(destination))).encode()
    repairs = []
    def repair(*args):
        repairs.append(args)
        raise RuntimeError('provider failure must not enter repair')
    with pytest.raises(ProviderError, match='target_provider_timeout'):
        adapter._verify_with_repairs(['question'], repair)
    assert repairs == []
    rows = [json.loads(line) for line in (adapter.evidence / 'observations.jsonl').read_text().splitlines()]
    assert rows[-1]['failure_kind'] == 'target_provider_timeout'


def test_unknown_process_timeout_is_not_claimed_as_provider_failure(tmp_path):
    adapter = controller(tmp_path, timeout=0.1)
    adapter.source = b'import time; time.sleep(30)'
    repairs = []
    with pytest.raises(ProviderError, match='target_execution_timeout'):
        adapter._verify_with_repairs(['question'], lambda *args: repairs.append(args))
    assert not repairs
    row = json.loads((adapter.evidence / 'observations.jsonl').read_text().splitlines()[-1])
    assert row['failure_kind'] == 'target_execution_timeout'
