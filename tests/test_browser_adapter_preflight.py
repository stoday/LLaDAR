"""Adapter submission checks a new question before spending a live probe."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace

import pytest

from lladar.recording_store import RecordingStore
from lladar.work_session import atomic_json


@pytest.mark.parametrize('body_expression,expected', [
    ("payload['state']['selected_request']['body']",
     {'status': 'rejected', 'reason': 'submission_unconfirmed',
      'diagnostic': {'reason': 'current_question_absent',
                     'demonstration_body_reused': True}}),
    ("payload['question'].encode('latin1')",
     {'status': 'rejected', 'reason': 'protocol_execution_error',
      'diagnostic_exception': 'LookupError'}),
    ('prepare_name_error',
     {'status': 'rejected', 'reason': 'protocol_execution_error',
      'diagnostic_exception': 'NameError'}),
    ('not valid python ???',
     {'status': 'rejected', 'reason': 'adapter_source_invalid',
      'diagnostic_exception': 'SyntaxError'}),
    ("'old question [DEMO-abc]'",
     {'status': 'rejected', 'reason': 'adapter_source_invalid',
      'diagnostic': {'reason': 'demonstration_question_literal'}}),
    ('send_list_output',
     {'status': 'rejected', 'reason': 'protocol_output_invalid',
      'diagnostic_hint': "return {'actions': [action]}"}),
    ("payload['question']", {'status': 'candidate'}),
])
def test_adapter_agent_gets_actionable_diagnostic_before_live_test(
        tmp_path, monkeypatch, body_expression, expected):
    from lladar import browser_two_agent
    from lladar.independent_target import IndependentTarget

    store = RecordingStore(tmp_path / 'recording')
    event_id = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                             'method': 'POST', 'body': 'old question [DEMO-abc]'})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': 'old question [DEMO-abc]', 'submission_refs': [event_id]})
    target = IndependentTarget(page_url='https://example.test/chat', workspace=tmp_path,
        recorder=SimpleNamespace(store=store, close=lambda: None),
        agent_factory=object(), confirmed=True, conversation_policy='demonstrated',
        output_fn=lambda _: None)
    target.demo_question = 'old question [DEMO-abc]'
    target.state = {'allowed_origins': ['https://example.test'], 'cookies': []}
    target.expected_destination = None
    target.ledger.approve()
    source = '''
def adapt(payload):
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'method': 'POST',
            'url': 'https://example.test/chat',
            'body': BODY_EXPRESSION, 'submit': True}]}
    if payload['phase'] == 'receive':
        return {'complete': False}
'''.replace('BODY_EXPRESSION', body_expression)
    if body_expression == 'prepare_name_error':
        source = source.replace("if payload['phase'] == 'send':",
                                "if missing_name and payload['phase'] == 'send':")
    if body_expression == 'send_list_output':
        source = source.replace('send_list_output', "payload['question']")
        source = source.replace("return {'actions': [", "return [")
        source = source.replace("'submit': True}]}", "'submit': True}]")

    class StopAfterSubmission(Exception):
        pass

    def fake_runner(*, tools, **_):
        if 'select_material' in tools:
            assert json.loads(tools['select_material']([event_id], 'request'))['status'] == 'selected'
            return
        assert set(tools) == {'submit_adapter'}
        result = json.loads(tools['submit_adapter'](source, 'Recorded request'))
        assert result['status'] == expected['status']
        if result['status'] == 'rejected':
            assert result['reason'] == expected['reason']
            if 'diagnostic_hint' in expected:
                assert expected['diagnostic_hint'] in result['diagnostic']['hint']
            elif 'diagnostic_exception' in expected:
                assert result['diagnostic']['exception_type'] == expected['diagnostic_exception']
            else:
                assert result['diagnostic'] == expected['diagnostic']
        attempts = list((tmp_path / 'adapter' / 'attempts').glob('*.json'))
        assert len(attempts) == 1
        archived = json.loads(attempts[0].read_text(encoding='utf-8'))
        assert archived['source'] == source
        assert archived['result'] == result
        raise StopAfterSubmission

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    try:
        with pytest.raises(StopAfterSubmission):
            target._discover()
        assert not (tmp_path / 'validation' / 'last-test.json').exists()
        assert not (tmp_path / 'runtime' / 'turns.json').exists()
    finally:
        target.close()


def test_browser_ui_source_is_accepted_for_immediate_live_probe(tmp_path, monkeypatch):
    from lladar import browser_two_agent
    from lladar.independent_target import IndependentTarget

    store = RecordingStore(tmp_path / 'recording')
    event_id = store.append({'kind': 'http_request', 'url': 'https://example.test/api',
                             'method': 'POST', 'body': 'demo [DEMO-abc]'})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': 'demo [DEMO-abc]', 'submission_refs': [event_id]})
    target = IndependentTarget(page_url='https://example.test/', workspace=tmp_path,
        recorder=SimpleNamespace(store=store, close=lambda: None,
                                 browser=SimpleNamespace(profile_dir=tmp_path / 'profile')),
        agent_factory=object(), confirmed=True, conversation_policy='independent',
        output_fn=lambda _: None)
    target.demo_question = 'demo [DEMO-abc]'
    target.state = {'allowed_origins': ['https://example.test'], 'cookies': []}
    target.expected_destination = None
    target.ledger.approve()
    source = '''
def adapt(p):
    if p['phase'] == 'send':
        return {'actions': [{'kind': 'browser_ui', 'submit': True,
            'question': p['question'], 'ui': {
                'page_url': 'https://example.test/', 'input_selector': 'textarea',
                'submit': {'kind': 'press', 'key': 'Enter'},
                'answer_selector': '.answer'}}]}
    if p['phase'] == 'receive':
        answers = [e for e in p['events'] if e['kind'] == 'browser_answer']
        return {'complete': bool(answers), 'answer': answers[-1]['text'] if answers else ''}
    return {'ready': True}
'''

    class StopAfterSubmission(Exception):
        pass

    def fake_runner(*, tools, **_):
        if 'select_material' in tools:
            tools['select_material']([event_id], 'Submission evidence')
            return
        result = json.loads(tools['submit_adapter'](source, 'DOM input and answer evidence'))
        assert result['status'] == 'candidate', result
        assert target.candidate['transport'] == 'browser_ui'
        assert target.adapter.browser_profile_dir == str(tmp_path / 'profile')
        raise StopAfterSubmission

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    try:
        with pytest.raises(StopAfterSubmission):
            target._discover()
        assert not (tmp_path / 'validation' / 'last-test.json').exists()
    finally:
        target.close()


def test_code_agent_receives_structured_live_response_diagnostic_after_failed_probe(
        tmp_path, monkeypatch):
    from lladar import browser_two_agent
    from lladar.independent_target import IndependentTarget

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            body = (b'{"type":"diagnostic","kind":"setup_required"}\n'
                    b'{"type":"end"}\n')
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}/chat'
    store = RecordingStore(tmp_path / 'recording')
    submission = store.append({'kind': 'http_request', 'url': endpoint,
        'method': 'POST', 'body': 'Question [DEMO-example]'})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
        {'question': 'Question [DEMO-example]', 'submission_refs': [submission]})
    target = IndependentTarget(page_url=endpoint, workspace=tmp_path,
        recorder=SimpleNamespace(store=store, close=lambda: None),
        agent_factory=object(), confirmed=True, conversation_policy='demonstrated',
        timeout=2, output_fn=lambda _: None)
    target.demo_question = 'Question [DEMO-example]'
    target.state = {'allowed_origins': [f'http://127.0.0.1:{server.server_port}'],
        'cookies': []}
    target.expected_destination = None
    target.ledger.approve()
    source = '''
def adapt(payload):
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': payload['state']['selected_request']['url'],
            'method': 'POST', 'body': payload['question'], 'submit': True}]}
    if payload['phase'] == 'receive':
        return {'complete': False}
'''
    code_calls = []

    class StopAfterFeedback(Exception):
        pass

    def fake_runner(*, tools, prompt, **_):
        if 'select_material' in tools:
            tools['select_material']([submission], 'Recorded submission')
            return
        code_calls.append(prompt)
        if len(code_calls) == 1:
            assert json.loads(tools['submit_adapter'](source, 'Recorded submission'))['status'] == 'candidate'
            return
        assert 'setup_required' in prompt
        raise StopAfterFeedback

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    try:
        with pytest.raises(StopAfterFeedback):
            target._discover()
        result = json.loads((tmp_path / 'validation' / 'last-test.json').read_text(encoding='utf-8'))
        assert result['reason'] == 'completion_unconfirmed'
        assert result['response_shape']['diagnostic_kinds'] == ['setup_required']
    finally:
        target.close()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('embeds_recorded_secret', [False, True])
def test_adapter_submission_accepts_dynamic_prepare_before_send(
        tmp_path, monkeypatch, embeds_recorded_secret):
    from lladar import browser_two_agent
    from lladar.independent_target import IndependentTarget

    store = RecordingStore(tmp_path / 'recording')
    submission = store.append({'kind': 'http_request',
        'url': 'https://example.test/chat', 'method': 'POST',
        'body': 'old question [DEMO-abc]'})
    recorded_secret = 'A' * 32 + 'B' * 32
    secret_event = store.append({'kind': 'http_request',
        'url': 'https://example.test/sentinel', 'method': 'POST',
        'body': json.dumps({'proofofwork': recorded_secret})})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
        {'question': 'old question [DEMO-abc]', 'submission_refs': [submission]})
    target = IndependentTarget(page_url='https://example.test/chat', workspace=tmp_path,
        recorder=SimpleNamespace(store=store, close=lambda: None),
        agent_factory=object(), confirmed=True, conversation_policy='demonstrated',
        output_fn=lambda _: None)
    target.demo_question = 'old question [DEMO-abc]'
    target.state = {'allowed_origins': ['https://example.test'], 'cookies': []}
    target.expected_destination = None
    target.ledger.approve()
    source = '''
def adapt(payload):
    if payload['phase'] == 'prepare':
        return {'ready': False, 'actions': [{'kind': 'http',
            'url': 'https://example.test/prepare', 'method': 'POST'}]}
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': 'https://example.test/chat',
            'method': 'POST', 'body': payload['question'] + payload['context']['token'],
            'submit': True}]}
    if payload['phase'] == 'receive':
        return {'complete': False}
'''
    if embeds_recorded_secret:
        source = ("RECORDED_POW = ('" + recorded_secret[:32] + "' '" +
                  recorded_secret[32:] + "')\n" + source)

    class StopAfterSubmission(Exception):
        pass

    def fake_runner(*, tools, **_):
        if 'select_material' in tools:
            tools['select_material']([submission, secret_event], 'Recorded request and challenge')
            return
        result = json.loads(tools['submit_adapter'](source, 'Prepare and submit request'))
        if embeds_recorded_secret:
            assert result['status'] == 'rejected', result
            assert result['diagnostic']['reason'] == 'private_value_literal'
        else:
            assert result['status'] == 'candidate', result
        raise StopAfterSubmission

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    try:
        with pytest.raises(StopAfterSubmission):
            target._discover()
    finally:
        target.close()


def test_code_agent_gets_setup_403_with_request_path_for_repair(tmp_path, monkeypatch):
    from lladar import browser_two_agent
    from lladar.independent_target import IndependentTarget

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(403)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    store = RecordingStore(tmp_path / 'recording')
    submission = store.append({'kind': 'http_request', 'url': endpoint + '/chat',
        'method': 'POST', 'body': 'Question [DEMO-example]'})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
        {'question': 'Question [DEMO-example]', 'submission_refs': [submission]})
    target = IndependentTarget(page_url=endpoint, workspace=tmp_path,
        recorder=SimpleNamespace(store=store, close=lambda: None),
        agent_factory=object(), confirmed=True, conversation_policy='demonstrated',
        timeout=2, output_fn=lambda _: None)
    target.demo_question = 'Question [DEMO-example]'
    target.state = {'allowed_origins': [endpoint], 'cookies': []}
    target.expected_destination = None
    target.ledger.approve()
    source = '''
def adapt(payload):
    if payload['phase'] == 'prepare':
        return {'ready': False, 'actions': [{'kind': 'http', 'method': 'POST',
            'url': payload['state']['selected_request']['url'].replace('/chat', '/prepare')}]}
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'method': 'POST',
            'url': payload['state']['selected_request']['url'],
            'body': payload['question'], 'submit': True}]}
    if payload['phase'] == 'receive':
        return {'complete': False}
'''

    class StopAfterFeedback(Exception):
        pass

    code_calls = []

    def fake_runner(*, tools, prompt, **_):
        if 'select_material' in tools:
            tools['select_material']([submission], 'Recorded submission')
            return
        code_calls.append(prompt)
        if len(code_calls) == 1:
            assert json.loads(tools['submit_adapter'](source, 'Prepare then send'))['status'] == 'candidate'
            return
        assert 'adapter_http_status' in prompt
        assert '"http_status": 403' in prompt
        assert '"request_path": "/prepare"' in prompt
        raise StopAfterFeedback

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    try:
        with pytest.raises(StopAfterFeedback):
            target._discover()
    finally:
        target.close()
        server.shutdown()
        server.server_close()
