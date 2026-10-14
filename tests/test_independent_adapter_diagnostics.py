"""Rejected sends give the code Agent safe, actionable feedback."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lladar.independent_adapter import IndependentAdapter, IndependentAdapterError


def test_replayed_demonstration_body_reports_missing_current_question_without_sending(tmp_path):
    source = '''
def adapt(payload):
    if payload['phase'] == 'prepare':
        return {'ready': True}
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'method': 'POST',
            'url': payload['state']['endpoint'],
            'body': payload['state']['selected_request']['body'], 'submit': True}]}
    return {'complete': False}
'''
    recorded_body = 'demo question [DEMO-previous]'
    debits = []
    adapter = IndependentAdapter(source=source, state={
        'endpoint': 'http://127.0.0.1:1/chat',
        'selected_request': {'body': recorded_body},
    }, allowed_origins=['http://127.0.0.1:1'], workspace=tmp_path,
        conversation_policy='demonstrated', timeout=1,
        debit=lambda: debits.append('network_attempt'))
    try:
        with pytest.raises(IndependentAdapterError, match='submission_unconfirmed'):
            adapter.run('new question [PROBE-current]', 'probe-current', role='probe')
        assert adapter.turn('probe-current')['events'] == []
        assert debits == []
        assert adapter.last_diagnostic == {
            'reason': 'current_question_absent', 'demonstration_body_reused': True,
        }
        assert recorded_body not in repr(adapter.last_diagnostic)
    finally:
        adapter.close()


def test_adapter_can_read_live_setup_response_before_submitting_question(tmp_path):
    submissions = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length'])).decode()
            if self.path == '/prepare':
                data = {'token': 'fresh-token'}
            elif self.path == '/chat':
                submissions.append(json.loads(body))
                data = {'answer': 'Reply to ' + submissions[-1]['question']}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    source = '''
import json
def adapt(p):
    events = p['events']
    if p['phase'] == 'prepare':
        bodies = [e for e in events if e['kind'] == 'http_body']
        if not bodies:
            return {'ready': False, 'actions': [{'kind': 'http',
                'url': p['state']['endpoint'] + '/prepare', 'method': 'POST'}]}
        return {'ready': True, 'context': {'token': json.loads(bodies[-1]['text'])['token']}}
    if p['phase'] == 'send':
        body = json.dumps({'question': p['question'], 'token': p['context']['token']})
        return {'actions': [{'kind': 'http', 'url': p['state']['endpoint'] + '/chat',
            'method': 'POST', 'body': body, 'submit': True}]}
    bodies = [e for e in events if e['kind'] == 'http_body' and e['url'].endswith('/chat')]
    if not bodies:
        return {'complete': False}
    return {'complete': True, 'answer': json.loads(bodies[-1]['text'])['answer']}
'''
    adapter = IndependentAdapter(source=source, state={'endpoint': endpoint},
        allowed_origins=[endpoint], workspace=tmp_path, timeout=5)
    try:
        turn = adapter.run('current question', 'turn-1')
        assert turn['answer'] == 'Reply to current question'
        assert submissions == [{'question': 'current question', 'token': 'fresh-token'}]
        assert [event['url'].rsplit('/', 1)[-1] for event in turn['events']
                if event['kind'] == 'http_request'] == ['prepare', 'chat']
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()


def test_setup_http_403_is_reported_as_adapter_failure_before_login_refresh(tmp_path):
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
    source = '''
def adapt(p):
    if p['phase'] == 'prepare':
        return {'ready': False, 'actions': [{'kind': 'http',
            'url': p['state']['endpoint'] + '/prepare', 'method': 'POST'}]}
    if p['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': p['state']['endpoint'] + '/chat',
            'method': 'POST', 'body': p['question'], 'submit': True}]}
    return {'complete': False}
'''
    adapter = IndependentAdapter(source=source, state={'endpoint': endpoint},
        allowed_origins=[endpoint], workspace=tmp_path, timeout=5)
    try:
        with pytest.raises(IndependentAdapterError, match='adapter_http_status'):
            adapter.run('question', 'turn-1')
        turn = adapter.turn('turn-1')
        assert turn['status'] == 'not_submitted'
        assert not any(event.get('submit') for event in turn['events'])
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()


def test_submitted_http_403_preserves_rejection_for_adapter_repair(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            encoded = b'Captcha token is invalid.'
            self.send_response(403)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    source = '''
def adapt(p):
    if p['phase'] == 'prepare':
        return {'ready': True}
    if p['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': p['state']['endpoint'],
            'method': 'POST', 'body': p['question'], 'submit': True}]}
    return {'complete': False}
'''
    adapter = IndependentAdapter(source=source, state={'endpoint': endpoint},
        allowed_origins=[endpoint], workspace=tmp_path, timeout=5)
    try:
        with pytest.raises(IndependentAdapterError, match='adapter_http_status'):
            adapter.run('new question', 'turn-1')
        turn = adapter.turn('turn-1')
        assert turn['status'] == 'submission_unknown'
        assert any(event['kind'] == 'http_response' and event['status'] == 403
                   for event in turn['events'])
        assert any(event['kind'] == 'http_body' and
                   event['text'] == 'Captcha token is invalid.'
                   for event in turn['events'])
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()


def test_browser_ui_candidate_sends_current_question_and_collects_answer(tmp_path, monkeypatch):
    from lladar import browser_ui_adapter

    calls = []

    class FakeBrowserUiAdapter:
        def __init__(self, plan):
            calls.append(plan)

        def run(self, question):
            calls.append(question)
            return {'answer': 'Answer to ' + question, 'question_visible': False,
                    'submission_status': 200, 'reference_path': '/c/fresh',
                    'completion_signal': 'new_rendered_answer_stable'}

    monkeypatch.setattr(browser_ui_adapter, 'BrowserUiAdapter', FakeBrowserUiAdapter)
    source = '''
def adapt(p):
    if p['phase'] == 'send':
        return {'actions': [{'kind': 'browser_ui', 'submit': True,
            'question': p['question'], 'ui': {
                'page_url': 'https://example.test/new',
                'input_selector': 'textarea',
                'submit': {'kind': 'press', 'key': 'Enter'},
                'answer_selector': '[data-answer]',
                'confirmation': {'method': 'POST', 'path': '/chat',
                                 'form_question_key': 'prompt'}}}]}
    answers = [e for e in p['events'] if e['kind'] == 'browser_answer']
    return {'complete': bool(answers), 'answer': answers[-1]['text'] if answers else ''}
'''
    adapter = IndependentAdapter(source=source, state={'page_url': 'https://example.test/c/demo'},
        allowed_origins=['https://example.test'], workspace=tmp_path,
        browser_profile_dir=tmp_path / 'saved-profile', timeout=5)
    try:
        turn = adapter.run('new question', 'turn-1')
        assert turn['answer'] == 'Answer to new question'
        assert turn['reference_url'] == 'https://example.test/c/fresh'
        assert turn['source_refs'] and turn['completion_refs']
        assert calls[0]['profile_dir'] == str(tmp_path / 'saved-profile')
        assert calls[1] == 'new question'
        assert [event['kind'] for event in turn['events']] == [
            'browser_submission', 'browser_answer', 'browser_end']
    finally:
        adapter.close()


def test_browser_ui_candidate_cannot_use_other_origin_or_replay_demo(tmp_path, monkeypatch):
    from lladar import browser_ui_adapter

    monkeypatch.setattr(browser_ui_adapter, 'BrowserUiAdapter',
                        lambda plan: pytest.fail('browser must not open'))
    source = '''
def adapt(p):
    if p['phase'] == 'send':
        return {'actions': [{'kind': 'browser_ui', 'submit': True,
            'question': p['question'], 'ui': {'page_url': p['state']['destination'],
                'input_selector': 'textarea', 'submit': {'kind': 'press'},
                'answer_selector': '.answer'}}]}
    return {'complete': False}
'''
    adapter = IndependentAdapter(source=source,
        state={'destination': 'https://other.test/', 'page_url': 'https://example.test/'},
        allowed_origins=['https://example.test'], workspace=tmp_path,
        browser_profile_dir=tmp_path / 'profile', timeout=5)
    try:
        with pytest.raises(IndependentAdapterError, match='adapter_origin_not_authorized'):
            adapter.run('new question', 'turn-1')
    finally:
        adapter.close()
