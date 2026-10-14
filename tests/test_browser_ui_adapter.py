"""A saved browser profile can send a fresh question and return a live answer."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from lladar.browser_ui_adapter import BrowserUiAdapter


@pytest.mark.browser
def test_browser_ui_adapter_submits_fresh_question_and_reads_answer(tmp_path):
    class Site(BaseHTTPRequestHandler):
        retry_attempts = 0

        def do_GET(self):
            body = b'''<!doctype html><button id="open" type="button">Ask</button>
              <section hidden><form id="ask"><textarea name="prompt"></textarea>
              <button type="submit">Send</button></form><div data-answer></div>
              <div data-error></div></section>
              <script>document.querySelector('#open').addEventListener('click',()=>{
                document.querySelector('section').hidden=false;});
              document.querySelector('form').addEventListener('submit',async e=>{
                e.preventDefault();const q=document.querySelector('textarea').value;
                const r=await fetch('/submit',{method:'POST',body:new URLSearchParams({prompt:q})});
                const text=await r.text();
                if(r.ok)document.querySelector('[data-answer]').textContent=text;
                else document.querySelector('[data-error]').textContent=text;
              })</script>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            from urllib.parse import parse_qs
            body = self.rfile.read(int(self.headers['Content-Length'])).decode()
            if parse_qs(body).get('prompt') == ['Blocked question']:
                reply = b'Captcha token is invalid.'
                self.send_response(403)
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
                return
            if parse_qs(body).get('prompt') == ['Retry question']:
                Site.retry_attempts += 1
                if Site.retry_attempts == 1:
                    reply = b'Captcha token is invalid.'
                    self.send_response(403)
                    self.send_header('Content-Length', str(len(reply)))
                    self.end_headers()
                    self.wfile.write(reply)
                    return
            reply = (b'The known reply' if parse_qs(body).get('prompt') == ['A new question']
                     else b'The retried reply' if parse_qs(body).get('prompt') == ['Retry question']
                     else b'wrong question')
            self.send_response(200)
            self.send_header('Content-Length', str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = BrowserUiAdapter({
            'page_url': f'http://127.0.0.1:{server.server_port}/',
            'profile_dir': str(Path(tmp_path) / 'profile'),
            'headless': True,
            'open': {'kind': 'click', 'selector': '#open'},
            'input_selector': 'textarea[name="prompt"]',
            'submit': {'kind': 'click', 'selector': 'button[type="submit"]'},
            'answer_selector': '[data-answer]',
            'confirmation': {'method': 'POST', 'path': '/submit', 'form_question_key': 'prompt'},
            'stable_seconds': 0.2,
            'timeout_seconds': 10,
        })
        result = adapter.run('A new question')
        assert result['answer'] == 'The known reply'
        assert result['submission_status'] == 200
        assert result['question_visible'] is False  # site clears no page text with the question
        adapter.plan.pop('confirmation')
        adapter.plan['timeout_seconds'] = 5
        with pytest.raises(RuntimeError, match='browser_ui_answer_timeout') as rejected:
            adapter.run('Blocked question')
        assert '403' in str(rejected.value)
        assert '/submit' in str(rejected.value)
        assert 'Captcha token is invalid.' in str(rejected.value)
        adapter.plan['retry'] = {'method': 'POST', 'path': '/submit',
                                 'form_question_key': 'prompt', 'statuses': [403],
                                 'max_attempts': 2}
        retry_debits = []
        adapter.plan['on_retry'] = lambda: retry_debits.append('second_send')
        adapter.plan['timeout_seconds'] = 10
        retried = adapter.run('Retry question')
        assert retried['answer'] == 'The retried reply'
        assert Site.retry_attempts == 2
        assert retry_debits == ['second_send']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
