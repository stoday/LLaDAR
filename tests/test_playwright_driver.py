from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest
import playwright.sync_api as playwright_sync

from lladar.browser_target import (
    BrowserAuthenticationRequired,
    BrowserTarget,
    CapturedInteraction,
    RequestTemplate,
)
from lladar.playwright_driver import PlaywrightBrowserDriver
from lladar.evaluation import evaluate
from lladar.records import read_records
from lladar.response_capture import ObservedResponse
from lladar.answer_extraction import ExtractionError
from fixture_extraction import fixture_options, approve, FixtureTerminal
from lladar.runner import run_agent


pytestmark = pytest.mark.browser


def test_browser_startup_navigation_and_request_have_separate_deadlines(tmp_path, monkeypatch):
    observed = {}

    class Page:
        def goto(self, url, **options):
            observed["navigation"] = options["timeout"]
            return SimpleNamespace(header_value=lambda _: "text/html")

        def reload(self, **options):
            observed["reload"] = options["timeout"]

        def bring_to_front(self):
            pass

        def wait_for_timeout(self, _):
            pass

        def evaluate(self, script, value):
            if isinstance(value, dict):
                observed["request"] = value["timeoutMs"]
            if "replaySnapshot" in script:
                return {
                    "overLimit": False, "timedOut": False, "failed": False,
                    "status": 200, "ok": True, "contentType": "text/plain",
                    "body": "Original answer", "done": True,
                }

    page = Page()
    context = SimpleNamespace(
        pages=[page], add_init_script=lambda _: None, close=lambda: None,
        set_default_navigation_timeout=lambda value: observed.update(default_navigation=value),
    )

    def launch(*_, **options):
        observed["startup"] = options.get("timeout")
        return context

    engine = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=lambda: None)
    monkeypatch.setattr(playwright_sync, "sync_playwright", lambda: SimpleNamespace(start=lambda: engine))
    driver = PlaywrightBrowserDriver(
        page_url="https://fixture.test", profile_dir=tmp_path / "profile", timeout=1,
    )
    try:
        driver.show_for_manual_login()
        template = RequestTemplate("POST", "https://fixture.test/ask", {}, "marker", "marker", "body")
        assert driver.replay(template, "question", "r").body == "Original answer"
    finally:
        driver.close()
    assert observed == {
        "startup": 300000, "navigation": 300000, "reload": 300000,
        "default_navigation": 300000, "request": 1000,
    }


def test_missing_chromium_reports_one_actionable_setup_command(tmp_path: Path, monkeypatch):
    class MissingChromium:
        chromium = None

        def __init__(self) -> None:
            self.chromium = self

        def launch_persistent_context(self, *_args, **_kwargs):
            raise playwright_sync.Error("browser executable is missing")

        def stop(self) -> None:
            return None

    missing = MissingChromium()

    class Starter:
        def start(self):
            return missing

    monkeypatch.setattr(playwright_sync, "sync_playwright", lambda: Starter())

    with pytest.raises(RuntimeError, match=r"python -m playwright install chromium"):
        PlaywrightBrowserDriver(
            page_url="https://example.test/chat",
            profile_dir=tmp_path / "profile",
            timeout=5,
            headless=True,
        )


@contextmanager
def question_site(*, large_response_text: str | None = None):
    state = {"pending": ""}
    stop_streams = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _send(self, status: int, content_type: str, body: bytes, *, cookie: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    "session=fixture-cookie-token; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax",
                )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/blank":
                self._send(200, "text/html; charset=utf-8", b"<!doctype html><title>Blank</title>")
                return
            if self.path == "/bare-api":
                self._send(200, "application/json", b'{"status":"api-only"}')
                return
            supported_pages = {
                "/chat", "/chat-get", "/chat-json", "/chat-xhr", "/chat-graphql",
                "/chat-ndjson", "/chat-text", "/chat-custom", "/chat-binary",
                "/chat-ambiguous", "/chat-open-sse", "/chat-closed-calibration",
                "/chat-oversized",
                "/chat-xhr-oversized", "/chat-xhr-gzip",
                "/chat-xhr-open",
            }
            if self.path in supported_pages:
                post_question = "fetch('/api/projects/fixture-project/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input: {text: pending}})})"
                if self.path == "/chat-get":
                    request_script = "fetch('/api/projects/fixture-project/ask-get?question=' + encodeURIComponent(pending))"
                elif self.path in {"/chat-xhr", "/chat-xhr-oversized", "/chat-xhr-gzip", "/chat-xhr-open"}:
                    endpoint = {"/chat-xhr": "ask-json", "/chat-xhr-oversized": "oversized-response", "/chat-xhr-gzip": "oversized-gzip", "/chat-xhr-open": "oversized-open"}[self.path]
                    request_script = "new Promise((resolve, reject) => { const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/projects/fixture-project/ENDPOINT'); xhr.setRequestHeader('Content-Type', 'application/json'); xhr.onprogress = () => {document.querySelector('#answer').textContent = xhr.responseText}; xhr.onload = () => resolve({text: async () => xhr.responseText}); xhr.onerror = reject; xhr.send(JSON.stringify({input: {text: pending}})); })".replace("ENDPOINT", endpoint)
                elif self.path == "/chat-ambiguous":
                    request_script = f"Promise.all([{post_question}, fetch('/analytics', {{method: 'POST', body: pending}})]).then(responses => responses[0])"
                else:
                    endpoint = {
                        "/chat-json": "ask-json",
                        "/chat-graphql": "ask-graphql",
                        "/chat-ndjson": "ask-ndjson",
                        "/chat-text": "ask-text",
                        "/chat-custom": "ask-custom",
                        "/chat-binary": "ask-binary",
                        "/chat-open-sse": "ask-open-sse",
                        "/chat-closed-calibration": "ask-closed-calibration",
                        "/chat-oversized": "oversized-response",
                    }.get(self.path)
                    request_script = (
                        f"fetch('/api/projects/fixture-project/{endpoint}', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{input: {{text: pending}}}})}})"
                        if endpoint else post_question
                    )
                consume_script = (
                    """const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let visible = '';
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    visible += decoder.decode(part.value, {stream: true});
    document.querySelector('#answer').textContent = visible;
    if (visible.includes('final_text')) break;
  }"""
                    if self.path == "/chat-open-sse"
                    else "document.querySelector('#answer').textContent = await response.text();"
                )
                page = """<!doctype html><meta charset=utf-8><div id=answer></div><script>
setInterval(async () => {
  const pending = await fetch('/pending').then(r => r.text());
  if (!pending) return;
  const response = await REQUEST_SCRIPT;
  CONSUME_SCRIPT
}, 50);
</script>""".replace("REQUEST_SCRIPT", request_script).replace(
                    "CONSUME_SCRIPT", consume_script
                ).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", page, cookie=True)
                return
            if self.path.startswith("/api/projects/fixture-project/ask-get?"):
                if "session=fixture-cookie-token" not in self.headers.get("Cookie", ""):
                    self._send(401, "text/plain", b"missing browser session")
                    return
                question = parse_qs(urlsplit(self.path).query)["question"][0]
                stream = (
                    'event: progress\ndata: {"message":"working"}\n\n'
                    f'event: done\ndata: {{"answer":{json.dumps("Answer: " + question)}}}\n\n'
                ).encode("utf-8")
                self._send(200, "text/event-stream; charset=utf-8", stream)
                return
            if self.path == "/pending":
                body = state["pending"].encode("utf-8")
                state["pending"] = ""
                self._send(200, "text/plain; charset=utf-8", body)
                return
            self._send(404, "text/plain", b"not found")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if self.path == "/set-pending":
                state["pending"] = body.decode("utf-8")
                self._send(204, "text/plain", b"")
                return
            if self.path == "/slow":
                time.sleep(5)
                self._send(200, "text/plain", b"late answer")
                return
            if self.path == "/analytics":
                self._send(204, "text/plain", b"")
                return
            if self.path == "/unauthorized":
                self._send(401, "text/plain", b"private diagnostic body")
                return
            if self.path == "/api/projects/fixture-project/oversized-gzip":
                encoded = gzip.compress((large_response_text if large_response_text is not None else "界" * 400_000).encode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if self.path in {"/oversized-response", "/api/projects/fixture-project/oversized-response", "/api/projects/fixture-project/oversized-open"}:
                # Fewer than 1 Mi characters but more than 1 MiB of UTF-8.
                # No Content-Length: enforce the observed bytes, not a header.
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write((large_response_text if large_response_text is not None else "界" * 400_000).encode("utf-8"))
                    self.wfile.flush()
                    if self.path.endswith("oversized-open"):
                        stop_streams.wait(15)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
                return
            if self.path == "/api/projects/fixture-project/ask":
                if "session=fixture-cookie-token" not in self.headers.get("Cookie", ""):
                    self._send(401, "text/plain", b"missing browser session")
                    return
                question = json.loads(body)["input"]["text"]
                stream = (
                    'data: {"session_id":"fixture"}\n'
                    'data: {"text":"partial"}\n'
                    f'data: {{"final_text":{json.dumps("Answer: " + question)}}}\n'
                ).encode("utf-8")
                self._send(200, "text/event-stream; charset=utf-8", stream)
                return
            if self.path == "/api/projects/fixture-project/ask-json":
                if "session=fixture-cookie-token" not in self.headers.get("Cookie", ""):
                    self._send(401, "text/plain", b"missing browser session")
                    return
                question = json.loads(body)["input"]["text"]
                response = json.dumps({"data": {"answer": "Answer: " + question}}).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", response)
                return
            if self.path in {
                "/api/projects/fixture-project/ask-open-sse",
                "/api/projects/fixture-project/ask-closed-calibration",
            }:
                if "session=fixture-cookie-token" not in self.headers.get("Cookie", ""):
                    self._send(401, "text/plain", b"missing browser session")
                    return
                question = json.loads(body)["input"]["text"]
                stream = (
                    'event: progress\ndata: {"message":"working"}\n\n'
                    f'event: done\ndata: {{"answer":{json.dumps("Answer: " + question)}}}\n\n'
                ).encode("utf-8")
                variable_close = self.path.endswith("ask-closed-calibration")
                if variable_close and question.startswith("LLaDAR calibration "):
                    self._send(200, "text/event-stream; charset=utf-8", stream)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(stream)
                self.wfile.flush()
                time.sleep(5 if variable_close else 2)
                self.close_connection = True
                return
            if self.path in {
                "/api/projects/fixture-project/ask-graphql",
                "/api/projects/fixture-project/ask-ndjson",
                "/api/projects/fixture-project/ask-text",
                "/api/projects/fixture-project/ask-custom",
                "/api/projects/fixture-project/ask-binary",
            }:
                if "session=fixture-cookie-token" not in self.headers.get("Cookie", ""):
                    self._send(401, "text/plain", b"missing browser session")
                    return
                question = json.loads(body)["input"]["text"]
                answer = "Answer: " + question
                if self.path.endswith("ask-graphql"):
                    response = json.dumps({"data": {"answer": answer}}).encode("utf-8")
                    self._send(200, "application/graphql-response+json", response)
                elif self.path.endswith("ask-ndjson"):
                    response = (
                        json.dumps({"type": "progress", "message": "working"}) + "\n" +
                        json.dumps({"type": "final", "answer": answer}) + "\n"
                    ).encode("utf-8")
                    self._send(200, "application/x-ndjson", response)
                elif self.path.endswith("ask-text"):
                    self._send(200, "text/plain; charset=utf-8", answer.encode("utf-8"))
                elif self.path.endswith("ask-custom"):
                    self._send(200, "application/x-custom-stream", f"final|{answer}".encode("utf-8"))
                else:
                    self._send(200, "application/octet-stream", b"\xff\x00\xfe")
                return
            self._send(404, "text/plain", b"not found")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        stop_streams.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_playwright_driver_captures_and_replays_authenticated_sse(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat",
            profile_dir=tmp_path / "profile",
            timeout=10,
            headless=True,
        )
        try:
            marker = "LLaDAR calibration fixture-marker"

            def submit_from_page(value: str) -> None:
                request = Request(base_url + "/set-pending", data=value.encode("utf-8"), method="POST")
                with urlopen(request, timeout=5):
                    pass

            captured = driver.capture_calibration(marker, submit_from_page)
            template = RequestTemplate.from_interaction(captured, marker)
            replayed = driver.replay(template, "Dataset question", "record-1-trial-1")
        finally:
            driver.close()

    assert captured.method == "POST"
    assert marker in captured.request_body
    assert replayed.content_type.startswith("text/event-stream")
    assert "Answer: Dataset question" in replayed.body


def test_playwright_calibration_follows_question_workflow_to_a_new_tab(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/blank",
            profile_dir=tmp_path / "profile",
            timeout=3,
            headless=True,
        )
        try:
            marker = "LLaDAR calibration fixture-marker"

            def submit_from_new_tab(value: str) -> None:
                question_page = driver.context.new_page()
                question_page.goto(base_url + "/chat", wait_until="domcontentloaded")
                request = Request(
                    base_url + "/set-pending",
                    data=value.encode("utf-8"),
                    method="POST",
                )
                with urlopen(request, timeout=5):
                    pass

            captured = driver.capture_calibration(marker, submit_from_new_tab)
            template = RequestTemplate.from_interaction(captured, marker)
            replayed = driver.replay(template, "Dataset question", "record-1-trial-1")
        finally:
            driver.close()

    assert captured.method == "POST"
    assert marker in captured.request_body
    assert "Answer: Dataset question" in replayed.body


def test_calibration_retains_stream_after_navigation_during_manual_input(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/blank",
            profile_dir=tmp_path / "profile",
            # Allow cold browser navigation; the assertion below still rejects
            # falling back to a completed response instead of capturing the stream.
            timeout=5,
            headless=True,
        )
        marker = "LLaDAR calibration navigation-fixture"

        def manual_action(value: str) -> None:
            # Navigation and the page's own request happen while Python is blocked,
            # just as when input() waits for a person to finish in the browser.
            driver.page.evaluate("() => setTimeout(() => location.href = '/chat-open-sse', 50)")
            with urlopen(Request(base_url + "/set-pending", data=value.encode()), timeout=5):
                pass
            time.sleep(0.5)
            return True  # Explicit local completion observation for this calibration only.

        try:
            captured = driver.capture_calibration(marker, manual_action)
        finally:
            driver.close()

    assert marker in captured.request_body
    assert captured.response.stream_closed is False
    assert "Answer: " + marker in captured.response.body


def test_persistent_browser_profile_reuses_authenticated_cookie_without_exporting_it(tmp_path: Path):
    with question_site() as base_url:
        profile_dir = tmp_path / "profile"
        first = PlaywrightBrowserDriver(
            page_url=base_url + "/chat",
            profile_dir=profile_dir,
            timeout=5,
            headless=True,
        )
        first.close()

        second = PlaywrightBrowserDriver(
            page_url=base_url + "/blank",
            profile_dir=profile_dir,
            timeout=5,
            headless=True,
        )
        marker = "LLaDAR calibration fixture-marker"
        template = RequestTemplate.from_interaction(
            CapturedInteraction(
                method="POST",
                url=base_url + "/api/projects/fixture-project/ask",
                headers={"content-type": "application/json"},
                request_body=json.dumps({"input": {"text": marker}}),
                response=ObservedResponse("text/event-stream", "event: done\ndata: {}\n\n"),
            ),
            marker,
        )
        try:
            replayed = second.replay(template, "Dataset question", "record-1-trial-1")
        finally:
            second.close()

    assert "Answer: Dataset question" in replayed.body
    assert "session=fixture-cookie-token" not in repr(template)


def test_bare_api_url_is_rejected_as_a_noninteractive_page(tmp_path: Path):
    with question_site() as base_url:
        with pytest.raises(RuntimeError, match="interactive HTML question page"):
            PlaywrightBrowserDriver(
                page_url=base_url + "/bare-api",
                profile_dir=tmp_path / "profile",
                timeout=5,
                headless=True,
            )


def test_calibration_authentication_failure_never_becomes_model_evidence(tmp_path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(page_url=base_url+"/blank", profile_dir=tmp_path/"profile", timeout=5, headless=True)
        def submit(marker):
            driver.page.evaluate("marker => fetch('/unauthorized', {method:'POST',body:marker})", marker)
            return True
        try:
            with pytest.raises(BrowserAuthenticationRequired):
                driver.capture_calibration("LLaDAR auth calibration", submit)
        finally:
            driver.close()


def test_browser_target_runs_full_calibration_and_replay_against_local_site(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat",
            profile_dir=tmp_path / "profile",
            timeout=10,
            headless=True,
        )
        messages: list[str] = []

        def respond_to_prompt(prompt: str) -> str:
            if prompt.startswith("Press Enter"):
                request = Request(
                    base_url + "/set-pending",
                    data=messages[-1].encode("utf-8"),
                    method="POST",
                )
                with urlopen(request, timeout=5):
                    pass
                return ""
            return approve(prompt)

        target = BrowserTarget(
            extraction_options=fixture_options(),
            review_stream=FixtureTerminal(),
            page_url=base_url + "/chat",
            timeout=10,
            runs_root=tmp_path,
            verbose=False,
            driver=driver,
            input_fn=respond_to_prompt,
            output_fn=messages.append,
        )
        try:
            target.prepare(["Dataset question"], interactive=True, request_count=1)
            actual = target.answer("Dataset question", "record-1-trial-1")
        finally:
            target.close()

    assert actual == "Answer: Dataset question"
    assert target.evidence["status"] == "verified"


def test_closed_calibration_does_not_make_open_verification_complete(tmp_path: Path):
    # Server emits event:done but deliberately keeps verification open for five seconds.
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(page_url=base_url + "/chat-closed-calibration",
                                         profile_dir=tmp_path / "profile", timeout=2, headless=True)
        def submit(marker):
            with urlopen(Request(base_url + "/set-pending", data=marker.encode()), timeout=5):
                pass
        try:
            captured = driver.capture_calibration("LLaDAR calibration fixture", submit)
            assert captured.response.stream_closed
            template = RequestTemplate.from_interaction(captured, "LLaDAR calibration fixture")
            with pytest.raises(TimeoutError):
                driver.replay(template, "verification", "verification")
        finally:
            driver.close()


def test_playwright_driver_captures_and_replays_authenticated_get_query(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat-get",
            profile_dir=tmp_path / "profile",
            timeout=3,
            headless=True,
        )
        try:
            marker = "LLaDAR calibration fixture-marker"

            def submit_from_page(value: str) -> None:
                request = Request(base_url + "/set-pending", data=value.encode("utf-8"), method="POST")
                with urlopen(request, timeout=5):
                    pass

            captured = driver.capture_calibration(marker, submit_from_page)
            template = RequestTemplate.from_interaction(captured, marker)
            replayed = driver.replay(template, "A&B + C", "record-1-trial-1")
        finally:
            driver.close()

    assert captured.method == "GET"
    assert template.question_location == "query"
    assert "Answer: A&B + C" in replayed.body


def test_playwright_replay_stops_at_the_configured_timeout(tmp_path: Path, monkeypatch):
    with question_site() as base_url:
        launch = playwright_sync.BrowserType.launch_persistent_context

        def launch_ready_browser(browser_type, *args, **kwargs):
            # Configure the external browser fixture, not LLaDAR's timeout.
            # On Windows, the first HTTP connection can spend >1s before the
            # localhost server receives it. Warm that startup outside the
            # replay assertion; all tested driver requests still get 1 second.
            context = launch(browser_type, *args, **kwargs)
            try:
                context.pages[0].goto(base_url + "/blank", wait_until="domcontentloaded", timeout=5000)
                return context
            except Exception:
                context.close()
                raise

        monkeypatch.setattr(playwright_sync.BrowserType, "launch_persistent_context", launch_ready_browser)
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat",
            profile_dir=tmp_path / "profile",
            timeout=1.0,
            headless=True,
        )
        marker = "LLaDAR calibration fixture-marker"
        template = RequestTemplate.from_interaction(
            CapturedInteraction(
                method="POST",
                url=base_url + "/slow",
                headers={"content-type": "application/json"},
                request_body=json.dumps({"question": marker}),
                response=ObservedResponse("text/plain", "calibration"),
            ),
            marker,
        )
        started = time.perf_counter()
        try:
            with pytest.raises(TimeoutError, match="timed out"):
                driver.replay(template, "Dataset question", "record-1-trial-1")
            replay_elapsed = time.perf_counter() - started
        finally:
            driver.close()

    # The request deadline does not include browser and fixture-server cleanup.
    assert replay_elapsed < 1.8


def test_playwright_replay_rejects_oversized_utf8_instead_of_returning_partial_text(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/blank", profile_dir=tmp_path / "profile", timeout=5, headless=True,
        )
        marker = "LLaDAR calibration size-fixture"
        template = RequestTemplate.from_interaction(
            CapturedInteraction(
                method="POST", url=base_url + "/oversized-response",
                headers={"content-type": "application/json"},
                request_body=json.dumps({"question": marker}),
                response=ObservedResponse("text/plain", "calibration"),
            ), marker,
        )
        try:
            with pytest.raises(RuntimeError, match="Browser response exceeded the 1 MiB capture limit"):
                driver.replay(template, "Dataset question", "oversized-trial")
        finally:
            driver.close()


@pytest.mark.parametrize("page_path", ["/chat-oversized", "/chat-xhr-oversized", "/chat-xhr-gzip", "/chat-xhr-open"])
def test_oversized_calibration_is_blocked_without_cancelling_the_original_page_response(tmp_path: Path, page_path: str):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + page_path, profile_dir=tmp_path / "profile", timeout=5, headless=True,
        )

        def submit_from_page(marker: str) -> bool:
            with urlopen(Request(base_url + "/set-pending", data=marker.encode(), method="POST"), timeout=5):
                pass
            # The website's own response must still reach its normal UI.
            driver.page.wait_for_function("document.querySelector('#answer').textContent.length === 400000")
            return True

        try:
            with pytest.raises(RuntimeError, match="Browser response exceeded the 1 MiB capture limit"):
                driver.capture_calibration("LLaDAR calibration size-fixture", submit_from_page)
        finally:
            driver.close()


@pytest.mark.parametrize("page_path", ["/chat-oversized", "/chat-xhr-gzip"])
def test_calibration_and_replay_accept_exactly_one_mib_of_utf8(tmp_path: Path, page_path: str):
    answer = "界" * 349_525 + "a"  # Exactly 1,048,576 UTF-8 bytes.
    with question_site(large_response_text=answer) as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + page_path, profile_dir=tmp_path / "profile", timeout=5, headless=True,
        )
        marker = "LLaDAR calibration boundary-fixture"

        def submit_from_page(value: str) -> None:
            with urlopen(Request(base_url + "/set-pending", data=value.encode(), method="POST"), timeout=5):
                pass

        try:
            captured = driver.capture_calibration(marker, submit_from_page)
            template = RequestTemplate.from_interaction(captured, marker)
            replayed = driver.replay(template, "Dataset question", "boundary-trial")
            body = captured.response.body
            assert (body.decode("utf-8") if isinstance(body, bytes) else body) == answer
            assert replayed.body == answer
        finally:
            driver.close()


def test_playwright_calibration_blocks_multiple_requests_with_the_same_marker(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat-ambiguous",
            profile_dir=tmp_path / "profile",
            timeout=3,
            headless=True,
        )
        marker = "LLaDAR calibration fixture-marker"

        def submit_from_page(value: str) -> None:
            request = Request(base_url + "/set-pending", data=value.encode("utf-8"), method="POST")
            with urlopen(request, timeout=5):
                pass

        try:
            with pytest.raises(RuntimeError, match="multiple"):
                driver.capture_calibration(marker, submit_from_page)
        finally:
            driver.close()


def test_playwright_replay_classifies_expired_browser_session_without_response_body(tmp_path: Path):
    with question_site() as base_url:
        driver = PlaywrightBrowserDriver(
            page_url=base_url + "/chat",
            profile_dir=tmp_path / "profile",
            timeout=3,
            headless=True,
        )
        marker = "LLaDAR calibration fixture-marker"
        template = RequestTemplate.from_interaction(
            CapturedInteraction(
                method="POST",
                url=base_url + "/unauthorized",
                headers={"content-type": "application/json"},
                request_body=json.dumps({"question": marker}),
                response=ObservedResponse("text/plain", "calibration"),
            ),
            marker,
        )
        try:
            with pytest.raises(BrowserAuthenticationRequired) as raised:
                driver.replay(template, "Dataset question", "record-1-trial-1")
        finally:
            driver.close()

    assert "401" in str(raised.value)
    assert "private diagnostic body" not in str(raised.value)


class OnePassStrategyAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, _request):
        self.tools["read_dataset"]()
        self.tools["write_strategy"](
            "def select_cases(cases, schedule):\n"
            "    for case in cases:\n"
            "        schedule(case)\n"
        )
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "browser-fixture"}}


class ExactAnswerEvaluationAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name = Path(skills[0]).name
        self.tools = tools

    def __call__(self, request):
        if request["stage"] == "plan":
            self.tools["submit_plan"]({
                "title": "Exact answer",
                "approach": "Compare the saved final response.",
                "dimensions": [{
                    "name": "correct",
                    "description": "Actual response equals expected answer.",
                    "kind": "boolean",
                }],
                "limitations": ["Fixture-only integration evidence."],
            })
        else:
            self.tools["submit_judgment"]({
                "values": {"correct": request["actual_response"] == request["expected_answer"]},
                "reason": "Compared saved strings.",
            })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "browser-fixture"}}


@pytest.mark.parametrize("page_path", ["/chat-json", "/chat"])
def test_real_browser_flow_writes_standard_responses_and_eval_accepts_them(
    tmp_path: Path,
    page_path: str,
):
    with question_site() as base_url:
        dataset = tmp_path / "dataset.jsonl"
        output = tmp_path / "responses.jsonl"
        dataset_rows = [
            {
                "question": "Dataset question one",
                "expected_answer": "Answer: Dataset question one",
                "actual_response": None,
            },
            {
                "question": "Dataset question two",
                "expected_answer": "Answer: Dataset question two",
                "actual_response": None,
            },
        ]
        dataset.write_text(
            "".join(json.dumps(row) + "\n" for row in dataset_rows),
            encoding="utf-8",
        )
        messages: list[str] = []

        def browser_factory(**options):
            driver = PlaywrightBrowserDriver(
                page_url=options["page_url"],
                profile_dir=tmp_path / "profile",
                timeout=options["timeout"],
                headless=True,
            )

            def respond_to_prompt(prompt: str) -> str:
                if prompt.startswith("Press Enter"):
                    request = Request(
                        base_url + "/set-pending",
                        data=messages[-1].encode("utf-8"),
                        method="POST",
                    )
                    with urlopen(request, timeout=5):
                        pass
                    return ""
                return approve(prompt)

            return BrowserTarget(
                extraction_options=fixture_options(),
                review_stream=FixtureTerminal(),
                page_url=options["page_url"],
                timeout=options["timeout"],
                runs_root=options["runs_root"],
                verbose=False,
                driver=driver,
                input_fn=respond_to_prompt,
                output_fn=messages.append,
            )

        completed = run_agent(
            dataset,
            output,
            page_url=base_url + page_path,
            browser_target_factory=browser_factory,
            strategy_agent_factory=OnePassStrategyAgent,
            interactive=True,
            verbose=False,
        )

    assert completed == 2
    assert read_records(output) == [
        {
            "question": "Dataset question one",
            "expected_answer": "Answer: Dataset question one",
            "actual_response": "Answer: Dataset question one",
        },
        {
            "question": "Dataset question two",
            "expected_answer": "Answer: Dataset question two",
            "actual_response": "Answer: Dataset question two",
        },
    ]
    persisted_sidecars = (
        output.with_name(output.name + ".run.json").read_text(encoding="utf-8")
        + output.with_name(output.name + ".trials.jsonl").read_text(encoding="utf-8")
    )
    for sensitive in ("session=fixture-cookie-token", "fixture-project", "LLaDAR calibration", "working"):
        assert sensitive not in persisted_sidecars
    result = evaluate(
        output,
        output=tmp_path / "evaluation.json",
        skill_agent_factory=ExactAnswerEvaluationAgent,
    )
    assert result["summary"]["evaluated"] == 2
    assert [item["values"] for item in result["items"]] == [
        {"correct": True},
        {"correct": True},
    ]


def _automated_browser_target(base_url: str, page_path: str, profile_dir: Path):
    driver = PlaywrightBrowserDriver(
        page_url=base_url + page_path,
        profile_dir=profile_dir,
        timeout=5,
        headless=True,
    )
    messages: list[str] = []

    def respond_to_prompt(prompt: str) -> str:
        if prompt.startswith("Press Enter"):
            request = Request(
                base_url + "/set-pending",
                data=messages[-1].encode("utf-8"),
                method="POST",
            )
            with urlopen(request, timeout=5):
                pass
            return ""
        return approve(prompt)

    return BrowserTarget(
        extraction_options=fixture_options(),
        review_stream=FixtureTerminal(),
        page_url=base_url + page_path,
        timeout=5,
        verbose=False,
        driver=driver,
        input_fn=respond_to_prompt,
        output_fn=messages.append,
    )


@pytest.mark.parametrize(("page_path", "protocol"), [
    ("/chat", "text/event-stream"),
    ("/chat-json", "application/json"),
    ("/chat-xhr", "application/json"),
    ("/chat-graphql", "application/graphql-response+json"),
    ("/chat-ndjson", "application/x-ndjson"),
    ("/chat-text", "text/plain"),
])
def test_browser_transport_corpus_returns_only_final_text(
    tmp_path: Path,
    page_path: str,
    protocol: str,
):
    with question_site() as base_url:
        target = _automated_browser_target(base_url, page_path, tmp_path / "profile")
        try:
            target.prepare(["Dataset question"], interactive=True, request_count=1)
            actual = target.answer("Dataset question", "record-1-trial-1")
        finally:
            target.close()

    assert actual == "Answer: Dataset question"
    assert target.evidence["response_protocol"] == protocol


@pytest.mark.parametrize("page_path", ["/chat-custom", "/chat-binary"])
def test_browser_transport_corpus_blocks_unsupported_opaque_responses(
    tmp_path: Path,
    page_path: str,
):
    with question_site() as base_url:
        target = _automated_browser_target(base_url, page_path, tmp_path / "profile")
        try:
            with pytest.raises(ExtractionError, match="completion_unconfirmed" if page_path=="/chat-binary" else "unsupported_response_protocol"):
                target.prepare(["Dataset question"], interactive=True, request_count=1)
        finally:
            target.close()
