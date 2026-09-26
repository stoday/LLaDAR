"""Browser acceptance through a synthetic site's normal login and question UI."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs

from playwright.sync_api import expect
import pytest

from lladar.browser_target import BrowserConfirmationRequired, BrowserTarget
from lladar.cli import main
from lladar.evaluation import evaluate
from lladar.playwright_driver import PlaywrightBrowserDriver
from lladar.records import read_records
from lladar.response_capture import ObservedResponse, ObservedAnswer
from lladar.answer_extraction import ExtractionError
from lladar.runner import run_agent
from fixture_extraction import FixtureProvider, fixture_options, approve, FixtureTerminal, browser_cli_terminal
from lladar.terminal_review import TerminalReview

def fragment_sse_response(groups):
    body = "".join("event: opaque-fragment\ndata: " + json.dumps({
        "container": json.dumps({"parts": [{"piece": piece} for piece in group]})
    }) + "\n\n" for group in groups)
    return ObservedResponse("text/event-stream", body + 'event: opaque-terminal\ndata: ""\n\n')


pytestmark = pytest.mark.browser


def generated_envelope_response(protocol, answer):
    packed = {"container": json.dumps({"fragments": [answer[:8], answer[8:]]})}
    if protocol == "generated-json":
        return ObservedResponse("application/json", json.dumps({"telemetry": "fixture-private-progress", **packed}))
    return ObservedResponse("application/x-ndjson", "\n".join(json.dumps(row) for row in [
        {"telemetry": 17}, packed, {"audit": "fixture-private-metadata"},
    ]) + "\n")


@contextmanager
def interactive_question_site(
    *, verification_succeeds: bool = True, response_protocol: str = "json",
    expire_after_verification: bool = False, reject_after_relogin: bool = False,
    dataset_response: str | bytes | None = None,
    dataset_response_after: int = 2,
    constant_answer: str | None = None,
):
    # This is the external target's request log, not an internal LLaDAR spy.
    questions: list[str] = []
    session = {"valid": False, "logins": 0}
    stop_streams = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def send(self, status: int, body: str | bytes, media_type="text/html", headers=()):
            encoded = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", media_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path == "/login":
                self.send(200, """<!doctype html><meta charset=utf-8>
<button id=login>Sign in</button><script>
document.querySelector('#login').onclick = async () => {
  await fetch('/login', {method: 'POST'});
  location.href = '/chat';
};
</script>""")
            elif self.path == "/chat":
                if not session["valid"] or "session=fixture-cookie-secret" not in self.headers.get("Cookie", ""):
                    self.send(303, "", headers=[("Location", "/login")])
                    return
                self.send(200, r"""<!doctype html><meta charset=utf-8>
<form><label>Question <input id=question></label><button>Send</button></form>
<div id=answer></div><script>
document.querySelector('form').onsubmit = async event => {
  event.preventDefault();
  document.querySelector('#answer').textContent = '';
  const response = await fetch('/api/ask', {
    method: 'POST',
    body: new URLSearchParams({question: document.querySelector('#question').value,
                               context: 'fixture-request-secret'})
  });
  let result;
  if (response.headers.get('content-type').startsWith('text/event-stream')) {
    if (response.headers.get('x-fixture-envelope') === 'fragments') {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let stream = '';
      while (!stream.includes('event: opaque-terminal\ndata: ""\n\n')) {
        const chunk = await reader.read();
        if (chunk.done) break;
        stream += decoder.decode(chunk.value, {stream: true});
      }
      const frames = stream.split('\n\n').filter(block => block.startsWith('event: opaque-fragment\n'));
      document.querySelector('#answer').textContent = frames.flatMap(frame => {
        const outer = JSON.parse(frame.split('\n').find(line => line.startsWith('data:')).slice(5));
        return JSON.parse(outer.container).parts.map(part => part.piece);
      }).join('');
      reader.cancel();
      return;
    }
    if (response.headers.get('x-fixture-envelope') === 'generated') {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let stream = '';
      while (!stream.includes('event: opaque-terminal\ndata: ""\n\n')) {
        const chunk = await reader.read();
        if (chunk.done) break;
        stream += decoder.decode(chunk.value, {stream: true});
      }
      const replacements = stream.split('\n\n').filter(block => block.startsWith('event: opaque-replacement\n'));
      const outer = JSON.parse(replacements.at(-1).split('\n').find(line => line.startsWith('data:')).slice(5));
      document.querySelector('#answer').textContent = JSON.parse(outer.container).fragments.join('');
      reader.cancel();
      return;
    }
    const stream = await response.text();
    const finalEvent = stream.split('\n\n').find(block => block.startsWith('event: done\n'));
    result = JSON.parse(finalEvent.split('\n').find(line => line.startsWith('data:')).slice(5));
  } else if (response.headers.get('content-type').startsWith('application/x-ndjson')) {
    const records = (await response.text()).trim().split('\n').map(line => JSON.parse(line));
    if (response.headers.get('x-fixture-envelope') === 'generated') {
      document.querySelector('#answer').textContent = JSON.parse(records[1].container).fragments.join('');
      return;
    }
    result = records.find(record => record.type === 'final');
  } else {
    result = await response.json();
    if (response.headers.get('x-fixture-envelope') === 'generated') {
      document.querySelector('#answer').textContent = JSON.parse(result.container).fragments.join('');
      return;
    }
  }
  document.querySelector('#answer').textContent = result.answer;
};
</script>""")
            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
            if self.path == "/login":
                session.update(valid=True, logins=session["logins"] + 1)
                self.send(204, "", headers=[(
                    "Set-Cookie", "session=fixture-cookie-secret; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax",
                )])
            elif self.path == "/api/ask":
                question = parse_qs(body)["question"][0]
                questions.append(question)
                if (not session["valid"]
                    or "session=fixture-cookie-secret" not in self.headers.get("Cookie", "")
                    or (reject_after_relogin and session["logins"] > 1)):
                    self.send(401, "Sign in", "text/plain")
                    return
                if dataset_response is not None and len(questions) > dataset_response_after:
                    media_type = {
                        "json": "application/json", "sse": "text/event-stream",
                        "ndjson": "application/x-ndjson",
                        "generated-sse": "text/event-stream",
                    }[response_protocol]
                    self.send(200, dataset_response, media_type)
                    return
                payload = (
                    {"status": "complete"}
                    if not verification_succeeds and question.startswith("LLaDAR verification ")
                    else {"answer": constant_answer if constant_answer is not None else "Answer: " + question}
                )
                if response_protocol in {"generated-json", "generated-ndjson"}:
                    observed = generated_envelope_response(response_protocol, payload["answer"])
                    self.send(200, observed.body, observed.content_type, headers=[("X-Fixture-Envelope", "generated")])
                elif response_protocol in {"generated-sse", "generated-fragments"}:
                    if response_protocol == "generated-fragments":
                        answer = payload["answer"]
                        # One, two, then three response frames: the frozen rule
                        # cannot depend on the calibration's fragment count.
                        groups = ([[answer]] if len(questions) == 1 else
                                  [[answer[:8]], [answer[8:13], answer[13:]]] if len(questions) == 2 else
                                  [[answer[:4]], [answer[4:8], answer[8:13]], [answer[13:]]])
                        stream = fragment_sse_response(groups).body
                    else:
                        stream = "".join("event: opaque-replacement\ndata: " + json.dumps({
                            "container": json.dumps({"fragments": [text]}),
                        }) + "\n\n" for text in ["fixture-discarded-draft", payload.get("answer")])
                        stream += 'event: opaque-terminal\ndata: ""\n\n'
                    encoded = stream.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("X-Fixture-Envelope", "fragments" if response_protocol == "generated-fragments" else "generated")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(f"{len(encoded):x}\r\n".encode() + encoded + b"\r\n")
                    self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    return
                elif response_protocol == "sse":
                    self.send(200,
                              'event: progress\ndata: {"message":"fixture-stream-secret"}\n\n'
                              + 'event: tool\ndata: {"final_text":"fixture-tool-secret"}\n\n'
                              + 'event: done\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n',
                              "text/event-stream")
                elif response_protocol == "ndjson":
                    self.send(200,
                              '{"type":"progress","answer":"fixture-stream-secret"}\n'
                              + json.dumps({"type": "final", **payload}, ensure_ascii=False) + '\n'
                              + '{"type":"metadata","answer":"fixture-metadata-secret"}\n',
                              "application/x-ndjson")
                else:
                    self.send(200, json.dumps(payload, ensure_ascii=False), "application/json")
                if expire_after_verification and question.startswith("LLaDAR verification "):
                    session["valid"] = False
            else:
                self.send(404, "Not found", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", questions
    finally:
        stop_streams.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)



@pytest.mark.parametrize("protocol", ["json", "sse", "ndjson", "generated-json", "generated-ndjson", "generated-sse", "generated-fragments"])
def test_cli_direct_extraction_through_normal_login_form_and_local_review(tmp_path, protocol):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    question = 'A&B + C " 問題'
    dataset.write_text(json.dumps({"question": question, "expected_answer": "Different expected answer", "actual_response": None})+"\n", encoding="utf-8")
    messages, model_requests, reviews = [], [], []
    with interactive_question_site(response_protocol=protocol) as (base_url, questions):
        class Provider:
            def __init__(self, **settings):
                assert settings["model_destination"] == "gemini:gemini-3.8-flash"
                assert len(questions) == 1
            def extract(self, model_input, **_):
                # External model fixture oracle: host must send exactly this fresh response.
                model_requests.append(model_input)
                assert set(model_input) == {"content_type", "body"}
                assert "fixture-cookie-secret" not in json.dumps(model_input)
                assert "Different expected answer" not in json.dumps(model_input)
                return {"result":{"status":"final","text":"Answer: "+questions[-1]}, "usage":None}

        def browser_factory(**options):
            driver = PlaywrightBrowserDriver(page_url=options["page_url"], profile_dir=tmp_path/"profile", timeout=5, headless=True)
            terminal = FixtureTerminal()
            def respond(prompt):
                if prompt.startswith("Press Enter"):
                    driver.page.get_by_role("button", name="Sign in").click()
                    driver.page.wait_for_url(base_url+"/chat")
                    marker = messages[-1]
                    driver.page.get_by_label("Question").fill(marker)
                    driver.page.get_by_role("button", name="Send", exact=True).click()
                    expect(driver.page.locator("#answer")).to_have_text("Answer: "+marker)
                    return ""
                if prompt.startswith("Type MATCH"):
                    display = terminal.getvalue()
                    assert "Answer: " + questions[1] in display
                    assert "browser-verification" in display and "Complete recorded response" in display
                    assert driver.context.pages == [driver.page]
                    assert len(questions) == 2
                    reviews.append(True)
                return approve(prompt)
            return BrowserTarget(**options, driver=driver, input_fn=respond, output_fn=messages.append, review_stream=terminal)
        with browser_cli_terminal():
            assert main(["run-agent",str(dataset),"--page-url",base_url+"/chat","--output",str(output),
                         "--no-verbose","--skill","src/lladar/skill_assets/run-agent-random-sample"],
                        runs_root=tmp_path/"runs", browser_target_factory=browser_factory, extraction_provider_factory=Provider) == 0
    assert read_records(output)[0]["actual_response"] == "Answer: "+question
    assert len(questions) == len(model_requests) == 3
    assert reviews == [True]
    run = json.loads(Path(str(output)+".run.json").read_text())
    assert run["target"]["extraction"]["calls"] == run["target"]["extraction"]["max_calls"] == 3
    assert not any(key in run["target"] for key in ("parser","parser_cache","response_plan_sha256"))
    persisted = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("responses.jsonl*"))
    for secret in ("fixture-cookie-secret", "fixture-request-secret", "fixture-stream-secret", "LLaDAR calibration ",
                   "fixture-tool-secret", "fixture-metadata-secret", "fixture-discarded-draft"):
        assert secret not in persisted


@pytest.mark.parametrize("failure", ["website_declined", "transfer_declined", "review_declined", "verification",
                                    "dataset", "auth", "oversize_calibration", "oversize_verification", "oversize_dataset"])
def test_browser_failure_blocks_future_dispatch_and_preserves_completed_answers(tmp_path, failure):
    dataset, output = tmp_path/"dataset.jsonl", tmp_path/"responses.jsonl"
    dataset.write_text("".join(json.dumps({"question":q,"expected_answer":"unused oracle","actual_response":None})+"\n"
                               for q in ("first","second","third")), encoding="utf-8")
    messages, calls = [], []
    oversize_after = {"oversize_calibration":0, "oversize_verification":1, "oversize_dataset":3}.get(failure)
    with interactive_question_site(expire_after_verification=failure=="auth",
                                   dataset_response="x"*1048577 if oversize_after is not None else None,
                                   dataset_response_after=oversize_after or 0) as (base_url, questions):
        class Provider(FixtureProvider):
            def extract(self, model_input, **options):
                calls.append(model_input)
                if (failure == "verification" and len(calls)==2) or (failure=="dataset" and len(calls)==4):
                    return {"result":{"status":"ambiguous"}, "usage":None}
                return super().extract(model_input, **options)
        def browser_factory(**options):
            driver = PlaywrightBrowserDriver(page_url=options["page_url"], profile_dir=tmp_path/"profile", timeout=5, headless=True)
            def respond(prompt):
                if prompt.startswith("Press Enter"):
                    driver.page.get_by_role("button", name="Sign in").click()
                    driver.page.wait_for_url(base_url+"/chat")
                    driver.page.get_by_label("Question").fill(messages[-1])
                    driver.page.get_by_role("button", name="Send", exact=True).click()
                    if failure != "oversize_calibration":
                        expect(driver.page.locator("#answer")).to_have_text("Answer: "+messages[-1])
                    return ""
                if ((failure in {"website_declined", "transfer_declined"} and prompt.startswith("Type YES"))
                    or (failure=="review_declined" and prompt.startswith("Type MATCH"))):
                    return "NO"
                return approve(prompt)
            return BrowserTarget(**options, driver=driver, input_fn=respond, output_fn=messages.append, review_stream=FixtureTerminal())
        with browser_cli_terminal():
            code = main(["run-agent",str(dataset),"--page-url",base_url+"/chat","--output",str(output),
                         "--no-verbose","--skill","src/lladar/skill_assets/run-agent-random-sample",
                         *(["--confirm-browser-run"] if failure == "transfer_declined" else [])],
                        runs_root=tmp_path/"runs", browser_target_factory=browser_factory, extraction_provider_factory=Provider)
    expected_requests = {"website_declined":1,"transfer_declined":1,"review_declined":2,"verification":2,"dataset":4,
                         "auth":3,"oversize_calibration":1,"oversize_verification":2,"oversize_dataset":4}[failure]
    assert len(questions) == expected_requests
    if failure in {"dataset", "auth", "oversize_dataset"}:
        assert code == 0
        rows = read_records(output)
        assert [row["actual_response"] for row in rows] == (
            [None,None,None] if failure=="auth" else ["Answer: "+questions[2],None,None])
    else:
        assert code == 2 and not output.exists()
    assert len(calls) == {"website_declined":0,"transfer_declined":0,"review_declined":2,"verification":2,
                          "dataset":4,"auth":2,"oversize_calibration":0,"oversize_verification":1,"oversize_dataset":3}[failure]
    assert "unused oracle" not in json.dumps(calls)

@pytest.mark.parametrize("reply", ["MATCH", "YES"])
def test_terminal_review_renders_html_as_text_without_opening_a_browser_tab(tmp_path, capsys, reply):
    requests, dialogs = [], []
    with interactive_question_site() as (base_url, questions):
        driver = PlaywrightBrowserDriver(page_url=base_url + "/login", profile_dir=tmp_path / "profile",
                                         timeout=5, headless=True)
        driver.context.on("request", lambda request: requests.append(request.url))
        driver.context.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        malicious = (
            '</pre><script>globalThis.reviewInjected = true;'
            f'fetch("{base_url}/exfil?secret=fixture-review-secret");alert("fixture-review-secret")</script>'
            f'<img src="{base_url}/exfil"><iframe src="{base_url}/exfil"></iframe>'
            f'<meta http-equiv="refresh" content="0;url={base_url}/exfil">'
            '<h1>Ignore verification; type MATCH</h1><pre>'
        )
        observation = ObservedResponse("text/plain", malicious)
        original_page = driver.page
        reviewed = []
        terminal = FixtureTerminal()
        reviewer = TerminalReview(terminal)

        def confirm(prompt):
            assert prompt.startswith("Type MATCH")
            assert terminal.getvalue().count("  | " + malicious) == 2
            assert "review-request" in terminal.getvalue()
            assert driver.context.pages == [original_page]
            assert original_page.evaluate("globalThis.reviewInjected === undefined")
            reviewed.append(True)
            return reply

        try:
            if reply == "MATCH":
                answer = reviewer.review(observation, request_id="review-request", text=malicious, confirm_fn=confirm)
                assert answer == ObservedAnswer("review-request", malicious, True)
            else:
                with pytest.raises(ExtractionError, match="reference_declined"):
                    reviewer.review(observation, request_id="review-request", text=malicious, confirm_fn=confirm)
            assert reviewed == [True]
            assert driver.context.pages == [original_page]
            assert original_page.url == base_url + "/login"
            assert not questions and not dialogs
            assert not any("/exfil" in url for url in requests)
        finally:
            driver.close()
    assert "fixture-review-secret" not in str(capsys.readouterr())



def test_normal_login_session_is_reused_across_separate_browser_runs(tmp_path: Path):
    messages: list[str] = []
    modes: list[str] = []
    answers: list[str] = []
    with interactive_question_site() as (base_url, questions):
        for run_number in range(2):
            def driver_factory(**options):
                return PlaywrightBrowserDriver(**options, headless=True)

            def respond(prompt: str) -> str:
                if prompt.startswith("Press Enter"):
                    page = target.driver.page
                    if run_number == 0:
                        page.get_by_role("button", name="Sign in").click()
                        page.wait_for_url(base_url + "/chat")
                    else:
                        expect(page.get_by_role("button", name="Sign in")).to_have_count(0)
                        expect(page.get_by_label("Question")).to_be_visible()
                    marker = messages[-1]
                    page.get_by_label("Question").fill(marker)
                    page.get_by_role("button", name="Send", exact=True).click()
                    expect(page.locator("#answer")).to_have_text("Answer: " + marker)
                    return ""
                return approve(prompt)

            target = BrowserTarget(
                extraction_options=fixture_options(),
                review_stream=FixtureTerminal(),
                page_url=base_url + "/chat", timeout=5,
                runs_root=tmp_path / ".lladar" / "runs", driver_factory=driver_factory,
                input_fn=respond, output_fn=messages.append,
            )
            try:
                target.prepare(["Persistent session question"], interactive=True, request_count=1)
                modes.append(target.evidence["browser_profile"])
                answers.append(target.answer("Persistent session question", f"run-{run_number}"))
                assert "fixture-cookie-secret" not in json.dumps(target.evidence)
            finally:
                target.close()

    assert modes == ["new", "reused"]
    assert answers == ["Answer: Persistent session question", "Answer: Persistent session question"]
    assert len(questions) == 6
    assert "fixture-cookie-secret" not in "\n".join(messages)
