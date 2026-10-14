"""A live browser must stop website traffic after authentication expires.

The protocol and extraction models are deterministic fixture stand-ins. The
delayed request runs in Chromium's website JavaScript, not through a host tool.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import pytest

from lladar.answer_extraction import ExtractionOptions
from lladar.browser_discovery_driver import GenericBrowserDriver
from lladar.browser_target import BrowserDiscoveryError, BrowserTarget
from test_browser_discovery_acceptance import _answer, fixture_models, http_program


pytestmark = pytest.mark.browser
FORMAL_QUESTION = "Only the formal request expires authentication."


@contextmanager
def authentication_site(status):
    submitted, answers, heartbeats = [], {}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, status=200, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.respond(b"""<!doctype html><html><body><main>Conversation</main>
              <script>
                globalThis.startAuthHeartbeat=()=>setTimeout(()=>{
                  document.body.dataset.heartbeatAttempted='yes';
                  fetch('/heartbeat',{method:'POST'}).then(()=>{
                    document.body.dataset.heartbeatDelivered='yes';
                  }).catch(()=>{document.body.dataset.heartbeatBlocked='yes';});
                },750);
              </script></body></html>""", content_type="text/html; charset=utf-8")

        def do_POST(self):
            if self.path == "/heartbeat":
                heartbeats.append(self.path)
                self.respond(b"{}")
                return
            envelope = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            question = envelope["question"]
            submitted.append(question)
            answers[question] = _answer(question)
            result = {"question": question, "status": "finished", "content": answers[question]}
            self.respond(json.dumps(result, ensure_ascii=False).encode(),
                status if question == FORMAL_QUESTION else 200)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}",
            submitted=submitted, answers=answers, heartbeats=heartbeats)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class AuthenticationFrontend(GenericBrowserDriver):
    """Model a frontend that queues a background refresh after its formal send."""
    open_calls = 0

    def open(self):
        self.open_calls += 1
        return super().open()

    def http_request(self, spec, *, submit=False):
        result = super().http_request(spec, submit=submit)
        if submit and json.loads(spec["body"])["question"] == FORMAL_QUESTION:
            self.page.evaluate("() => globalThis.startAuthHeartbeat()")
        return result


@pytest.mark.parametrize("status", [401, 403])
def test_expired_authentication_seals_native_traffic_models_and_same_instance_prepare(tmp_path, status):
    with authentication_site(status) as site:
        agent, provider, extracted = fixture_models(http_program(site.url, "json"))
        driver = AuthenticationFrontend(page_url=site.url, profile_dir=tmp_path/"profile",
            timeout=5, headless=True)

        def host_reference(turn):
            request = driver.raw_event(turn["evidence"]["submission_events"][0])
            return site.answers[json.loads(request["body"])["question"]]

        target = BrowserTarget(page_url=site.url, driver=driver, workspace=tmp_path/"workspace",
            timeout=8, agent_factory=agent, confirmed=True, conversation_policy="new_each",
            extraction_options=ExtractionOptions(transfer_approved=True, provider_factory=provider),
            reference_reader=host_reference, output_fn=lambda _: None)
        try:
            target.prepare([], interactive=False, record_count=1, request_count=1)
            assert target.evidence["status"] == "verified"
            assert len(target.evidence["verification"]) == 3
            assert len(site.submitted) == len(extracted) == 4

            with pytest.raises(BrowserDiscoveryError) as blocked:
                target.answer(FORMAL_QUESTION, "formal-expired")
            assert blocked.value.reason == "browser_authentication_required"
            assert site.submitted[-1] == FORMAL_QUESTION
            assert len(site.submitted) == 5
            counts = dict(target.ledger.data["counts"])

            # Keep the browser open and pump its actual request interceptor until
            # the scheduled website-owned fetch is attempted and rejected.
            driver.page.wait_for_function("() => document.body.dataset.heartbeatBlocked === 'yes'",
                timeout=5000)
            assert driver.page.evaluate("() => document.body.dataset.heartbeatAttempted") == "yes"
            assert driver.page.evaluate("() => document.body.dataset.heartbeatDelivered") is None
            assert site.heartbeats == []
            assert target.ledger.data["counts"] == counts

            with pytest.raises(BrowserDiscoveryError) as blocked:
                target._model_started()
            assert blocked.value.reason == "browser_authentication_required"
            assert target.ledger.data["counts"] == counts
            prior_opens = driver.open_calls
            with pytest.raises(BrowserDiscoveryError) as blocked:
                target.prepare([], interactive=False, record_count=1, request_count=1)
            assert blocked.value.reason == "browser_authentication_required"
            assert driver.open_calls == prior_opens
            assert target.ledger.data["counts"] == counts
            assert len(site.submitted) == 5
        finally:
            target.close()
