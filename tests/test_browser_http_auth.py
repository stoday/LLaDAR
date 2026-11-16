"""HTTP entrance authentication stays host-owned across browser restarts."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from lladar.browser_http_auth import BrowserHttpAuth
from lladar.browser_discovery_driver import GenericBrowserDriver, BrowserDiscoveryError
from lladar.independent_adapter import IndependentAdapter
from lladar.recording_store import RecordingStore


USER = 'fixture-auth-user'
PASSWORD = 'fixture-auth-password'
TOKEN = base64.b64encode(f'{USER}:{PASSWORD}'.encode()).decode()


@contextmanager
def protected_site(*, protected=True):
    requests = []

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            authorized = self.headers.get('Authorization') == 'Basic ' + TOKEN
            requests.append((self.path, self.headers.get('Authorization')))
            body = (b'<html><body>Ready<textarea></textarea><button onclick="'
                    b'document.querySelector(\'p\').textContent=document.querySelector(\'textarea\').value;'
                    b'fetch(\'/ask\',{method:\'POST\',body:document.querySelector(\'textarea\').value})'
                    b'.then(r=>r.text()).then(t=>document.querySelector(\'article\').textContent=t)">Send</button>'
                    b'<p></p><article></article></body></html>') if authorized or not protected else b'Unauthorized'
            self.send_response(200 if authorized or not protected else 401)
            if protected and not authorized:
                self.send_header('WWW-Authenticate', 'Basic realm="Fixture"')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            requests.append((self.path, self.headers.get('Authorization')))
            self.send_response(200)
            self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'Reply')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def auth_session(password=PASSWORD):
    prompts, output = [], []

    def username(prompt):
        prompts.append('username')
        return USER

    def secret(prompt):
        prompts.append('password')
        return password

    return BrowserHttpAuth(input_fn=username, password_fn=secret, output_fn=output.append), prompts, output


@pytest.mark.browser
def test_browser_basic_auth_prompts_once_reopens_and_never_records_credentials(tmp_path):
    auth, prompts, output = auth_session()
    store = RecordingStore(tmp_path / 'recording')
    with protected_site() as (url, requests):
        for index in range(2):
            browser = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / 'profile',
                headless=True, http_auth=auth, recording_store=store)
            try:
                browser.open()
                assert 'Ready' in browser.page.locator('body').inner_text()
            finally:
                browser.close()
        assert prompts == ['username', 'password']
        assert any(header == 'Basic ' + TOKEN for _, header in requests)
    for file in (tmp_path / 'recording').rglob('*'):
        if file.is_file():
            for secret in (USER, PASSWORD, TOKEN):
                assert secret.encode() not in file.read_bytes()
    assert all(secret not in json.dumps(output) for secret in (USER, PASSWORD, TOKEN))


@pytest.mark.browser
def test_wrong_basic_password_has_bounded_retries_and_specific_error(tmp_path):
    auth, prompts, _ = auth_session(password='wrong-fixture-password')
    with protected_site() as (url, _):
        browser = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / 'profile',
            headless=True, http_auth=auth)
        try:
            with pytest.raises(BrowserDiscoveryError, match='browser_authentication_required'):
                browser.open()
            assert prompts == ['username', 'password'] * 3
            assert browser.context is None
        finally:
            browser.close()


@pytest.mark.browser
def test_public_site_does_not_prompt(tmp_path):
    auth, prompts, _ = auth_session()
    with protected_site(protected=False) as (url, _):
        browser = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / 'profile',
            headless=True, http_auth=auth)
        try:
            browser.open()
            assert prompts == []
        finally:
            browser.close()


def test_http_adapter_injects_auth_only_at_the_matching_origin(tmp_path):
    auth, prompts, _ = auth_session()
    with protected_site() as (url, requests), protected_site(protected=False) as (other, foreign):
        auth.prompt(url)
        adapter = IndependentAdapter(source='def adapt(payload):\n return {}', state={},
            allowed_origins=[url, other], workspace=tmp_path, http_auth=auth)
        try:
            for index, destination in enumerate([url, other]):
                turn = {'request_id': str(index), 'question': 'Question', 'events': [], 'status': 'not_submitted'}
                adapter.turns[str(index)] = turn
                adapter._actions({'actions': [{'kind': 'http', 'url': destination}]},
                    turn, time.monotonic() + 10, False)
            assert any(value == 'Basic ' + TOKEN for _, value in requests)
            assert all(value is None for _, value in foreign)
        finally:
            adapter.close()
    for file in tmp_path.rglob('*.json'):
        assert all(secret not in file.read_text() for secret in (USER, PASSWORD, TOKEN))


@pytest.mark.browser
def test_formal_ui_turn_reuses_the_host_authentication(tmp_path):
    from lladar.browser_ui_adapter import BrowserUiAdapter
    auth, prompts, _ = auth_session()
    with protected_site() as (url, _):
        auth.prompt(url)
        adapter = BrowserUiAdapter({'page_url': url, 'profile_dir': str(tmp_path / 'profile'),
            'headless': True, 'input_selector': 'textarea', 'submit': {'kind': 'click', 'selector': 'button'},
            'answer_selector': 'article', 'timeout_seconds': 10, 'stable_seconds': 0.1}, http_auth=auth)
        result = adapter.run('A fresh question')
        assert result['answer'] == 'Reply'
        assert result['question_visible'] is True
        assert prompts == ['username', 'password']


def test_resume_reprompts_without_serializing_credentials():
    first, _, _ = auth_session()
    first.prompt('https://fixture.test/')
    saved = json.loads(json.dumps(first.required_origins()))
    first.clear()
    second, prompts, _ = auth_session()
    second.restore(saved, ['https://fixture.test/'])
    assert prompts == ['username', 'password']
    assert second.authorization('https://fixture.test/path') == 'Basic ' + TOKEN
    assert second.authorization('https://other.fixture.test/') is None
    assert all(secret not in json.dumps(saved) for secret in (USER, PASSWORD, TOKEN))
    with pytest.raises(ValueError, match='browser_origin_not_authorized'):
        second.restore(['https://unapproved.test'], ['https://fixture.test'])


def test_auth_redacts_binary_headers_and_plaintext():
    auth, _, _ = auth_session()
    auth.prompt('https://fixture.test')
    captured = {'headers': {'Authorization': 'Basic ' + TOKEN, 'Other': 'ok'},
        'text': USER + PASSWORD, 'binary_b64': base64.b64encode(PASSWORD.encode()).decode()}
    screened = auth.redact(captured)
    assert 'Authorization' not in screened['headers']
    assert PASSWORD.encode() not in base64.b64decode(screened['binary_b64'])
    assert all(secret not in json.dumps(screened) for secret in (USER, PASSWORD, TOKEN))


def test_password_input_refuses_echoing_fallback():
    import getpass
    import warnings
    from lladar.browser_http_auth import BasicAuthChallenge

    def insecure_input(_):
        warnings.warn('Password input may be echoed.', getpass.GetPassWarning)
        pytest.fail('Do not accept an echoing password reader.')

    auth = BrowserHttpAuth(input_fn=lambda _: USER, password_fn=insecure_input, output_fn=lambda _: None)
    with pytest.raises(BasicAuthChallenge):
        auth.prompt('https://fixture.test/')
    assert auth.required_origins() == []


@pytest.mark.browser
def test_demonstration_saves_only_auth_requirement_and_reusable_state(tmp_path):
    from lladar.demonstration_recorder import DemonstrationRecorder
    auth, prompts, _ = auth_session()
    with protected_site() as (url, _):
        recorder = DemonstrationRecorder(page_url=url, workspace=tmp_path, headless=True)
        recorder.browser.http_auth = auth

        def demonstrate(_):
            recorder.browser.page.locator('textarea').fill('Explain this service')
            recorder.browser.page.locator('button').click()
            recorder.browser.page.locator('article').filter(has_text='Reply').wait_for()
            return ''

        state = recorder.capture('Explain this service', demonstrate, lambda _: None)
        assert recorder.closed
        assert state['http_basic_auth_origins'] == [url.rstrip('/')]
        assert prompts == ['username', 'password']
    for file in tmp_path.rglob('*'):
        if file.is_file() and 'browser-profile' not in file.parts:
            assert all(secret.encode() not in file.read_bytes() for secret in (USER, PASSWORD, TOKEN))


@pytest.mark.browser
def test_browser_does_not_send_credentials_to_a_different_challenging_origin(tmp_path):
    auth, _, _ = auth_session()
    with protected_site() as (url, _), protected_site() as (other, foreign):
        auth.prompt(url)
        browser = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / 'profile',
            headless=True, http_auth=auth)
        try:
            browser.open()
            try:
                browser.page.goto(other, timeout=10000)
            except Exception:
                pass  # Chromium refuses the unauthenticated foreign navigation.
            assert foreign
            assert all(value is None for _, value in foreign)
        finally:
            browser.close()
