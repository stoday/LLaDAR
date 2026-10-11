from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest
from websockets.sync.server import serve

from lladar.browser_target import BrowserRequestFailure, BrowserRequestTimeout, BrowserResponseLimitExceeded, RequestTemplate, BrowserTarget, _protected_values
from lladar.playwright_driver import PlaywrightBrowserDriver
from lladar.answer_extraction import ExtractionOptions, ExtractionError, response_input
from lladar.response_capture import ObservedAnswer
from fixture_extraction import FixtureTerminal

pytestmark = pytest.mark.browser


@contextmanager
def socket_site(mode="persistent"):
    questions = []
    def chat(socket):
        assert socket.subprotocol == "chat.v1"
        assert "session=fixture-session-secret" in socket.request.headers.get("Cookie", "")
        for data in socket:
            question = json.loads(data)["question"] if data.startswith("{") else data
            questions.append(question)
            active_mode = (mode.removesuffix("-replay") if len(questions) > 1 else "closed") if mode.endswith("-replay") else mode
            if mode == "timeout" and len(questions) > 1:
                continue
            if active_mode == "binary":
                socket.send(b"private binary")
            elif active_mode == "oversized":
                socket.send("x" * (1024 * 1024 + 1))
            elif active_mode == "echo":
                socket.send(json.dumps({"answer": "fixture-session-secret"}))
            elif active_mode == "plain":
                socket.send("Working")
                socket.send("Answer: " + question)
            else:
                socket.send(json.dumps({"progress": "working"}))
                socket.send(json.dumps({"answer": "Answer: " + question}, ensure_ascii=False))
            if active_mode in {"closed", "plain", "echo"} or (mode.endswith("-replay") and len(questions) == 1):
                socket.close(1000)
                return
            if active_mode == "abnormal":
                socket.close(1011, "private failure")
                return
    server = serve(chat, "127.0.0.1", 0, subprotocols=["chat.v1"])
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    ws_port = server.socket.getsockname()[1]
    class Page(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            body = ("<html><body><div id='answer'></div><script>"
                    f"window.socket = new WebSocket('ws://127.0.0.1:{ws_port}/chat', 'chat.v1');"
                    "socket.onmessage = e => document.querySelector('#answer').textContent += e.data;"
                    "</script></body></html>").encode()
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "session=fixture-session-secret; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    http = ThreadingHTTPServer(("127.0.0.1",0),Page)
    http_thread=threading.Thread(target=http.serve_forever,daemon=True);http_thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/", questions
    finally:
        server.shutdown(); server.socket.close(); thread.join(3)
        http.shutdown();http.server_close();http_thread.join(3)


def calibrate(driver, marker):
    def submit(value):
        driver.page.wait_for_function("socket.readyState === WebSocket.OPEN")
        driver.page.evaluate("q => socket.send(JSON.stringify({question:q}))", value)
        driver.page.wait_for_function("document.querySelector('#answer').textContent.includes('Answer:')")
        return True
    return driver.capture_calibration(marker, submit)


def test_real_websocket_calibration_replay_and_question_isolation(tmp_path):
    with socket_site("closed") as (url,questions):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=5,headless=True)
        try:
            marker='客服 "週末"\n幾點開？'
            interaction=calibrate(driver,marker)
            assert interaction.method == "WEBSOCKET"
            assert interaction.socket_protocols == ("chat.v1",)
            assert "fixture-session-secret" not in repr(interaction)
            template=RequestTemplate.from_interaction(interaction,marker)
            first=driver.replay(template,'A&B "中"\n文','first')
            assert json.loads(json.loads(first.body)[-1])["answer"] == 'Answer: A&B "中"\n文'
            second=driver.replay(template,"Second question","second")
            assert json.loads(json.loads(second.body)[-1])["answer"] == "Answer: Second question"
            assert marker not in second.body and "A&B" not in second.body
            assert questions == [marker,'A&B "中"\n文',"Second question"]
            assert first.stream_closed and not first.manual_complete
        finally: driver.close()


@pytest.mark.parametrize("mode,error", [("binary",BrowserRequestFailure),("abnormal",BrowserRequestFailure),("oversized",BrowserResponseLimitExceeded)])
def test_websocket_calibration_refuses_unsafe_material(tmp_path,mode,error):
    with socket_site(mode) as (url,_):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            def submit(marker):
                driver.page.wait_for_function("socket.readyState === WebSocket.OPEN")
                driver.page.evaluate("q => socket.send(JSON.stringify({question:q}))",marker)
                driver.page.wait_for_timeout(100)
                return True
            with pytest.raises(error): driver.capture_calibration("Calibration",submit)
        finally: driver.close()


def test_persistent_websocket_requires_completion_not_idle_time(tmp_path):
    with socket_site("open-replay") as (url,questions):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            template=RequestTemplate.from_interaction(calibrate(driver,"Calibration"),"Calibration")
            with pytest.raises(BrowserRequestTimeout): driver.replay(template,"Question","r",timeout_seconds=.3)
            assert questions == ["Calibration","Question"]
        finally: driver.close()


def test_websocket_full_target_verification_and_dataset(tmp_path):
    class Provider:
        def __init__(self,**_): pass
        def extract(self,request,**_):
            frames=json.loads(request["body"])
            answer=json.loads(frames[-1])["answer"]
            return {"result":{"status":"final","text":answer},"usage":None}
    def reference(response,request_id):
        return ObservedAnswer(request_id,json.loads(json.loads(response.body)[-1])["answer"],True)
    with socket_site("closed") as (url,questions):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=5,headless=True)
        def input_fn(prompt):
            if prompt.startswith("Press Enter"):
                driver.page.wait_for_function("socket.readyState === WebSocket.OPEN")
                driver.page.evaluate("q => socket.send(JSON.stringify({question:q}))",target.calibration_question)
                driver.page.wait_for_function("document.querySelector('#answer').textContent.includes('Answer:')")
                return ""
            return "YES"
        target=BrowserTarget(page_url=url,timeout=5,driver=driver,calibration_question="Calibration",
                             confirmed=True,input_fn=input_fn,output_fn=lambda _:None,review_stream=FixtureTerminal(),
                             extraction_options=ExtractionOptions(provider_factory=Provider,transfer_approved=True,reference_reader=reference))
        try:
            target.prepare(["Dataset"],interactive=True,request_count=1)
            assert target.answer("Dataset","r") == "Answer: Dataset"
            assert questions == ["Calibration","Calibration","Dataset"]
        finally: target.close()


def test_persistent_websocket_calibration_is_unsupported(tmp_path):
    with socket_site() as (url,_):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            with pytest.raises(ExtractionError,match="completion_unconfirmed"):
                calibrate(driver,"Calibration")
        finally: driver.close()


def test_websocket_duplicate_marker_is_rejected(tmp_path):
    with socket_site() as (url,_):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            def submit(marker):
                driver.page.wait_for_function("socket.readyState === WebSocket.OPEN")
                driver.page.evaluate("q => socket.send(JSON.stringify({question:q,history:[q]}))",marker)
                return True
            with pytest.raises(BrowserRequestFailure): driver.capture_calibration("Calibration",submit)
        finally: driver.close()


@pytest.mark.parametrize("mode,error", [("binary-replay",BrowserRequestFailure),("abnormal-replay",BrowserRequestFailure),("oversized-replay",BrowserResponseLimitExceeded)])
def test_websocket_replay_refuses_unsafe_material(tmp_path,mode,error):
    with socket_site(mode) as (url,questions):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            template=RequestTemplate.from_interaction(calibrate(driver,"Calibration"),"Calibration")
            with pytest.raises(error): driver.replay(template,"Question","r")
            assert questions == ["Calibration","Question"]
        finally: driver.close()


def test_websocket_plain_frames_preserve_boundaries(tmp_path):
    with socket_site("plain") as (url,_):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            def submit(marker):
                driver.page.wait_for_function("socket.readyState === WebSocket.OPEN")
                driver.page.evaluate("q => socket.send(q)",marker)
                driver.page.wait_for_function("document.querySelector('#answer').textContent.includes('Answer:')")
                return True
            interaction=driver.capture_calibration("Calibration",submit)
            result=driver.replay(RequestTemplate.from_interaction(interaction,"Calibration"),'中文\n"Question"','r')
            assert json.loads(result.body) == ["Working",'Answer: 中文\n"Question"']
        finally: driver.close()


def test_websocket_echoed_cookie_is_blocked_before_transfer(tmp_path):
    with socket_site("echo-replay") as (url,_):
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",timeout=2,headless=True)
        try:
            interaction=calibrate(driver,"Calibration")
            result=driver.replay(RequestTemplate.from_interaction(interaction,"Calibration"),"Question","r")
            with pytest.raises(ExtractionError,match="sensitive_response"):
                response_input(result,_protected_values(interaction))
        finally: driver.close()
