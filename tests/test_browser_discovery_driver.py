"""Real local browsers exercise generic tools, not website-name branches."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from lladar.browser_discovery_driver import GenericBrowserDriver, BrowserDiscoveryError


@contextmanager
def discovery_site():
    stopping = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path in {"/keyed","/private-client.js"}:
                body=(b'{"outer":{"Bearer fixture-private-credential":"Full public answer."}}' if self.path=="/keyed"
                      else b'globalThis.privateExample={"eyJsynthetic.eyJsynthetic.signature":"Public metadata"};')
                self.send_response(200)
                self.send_header("Content-Type","application/json" if self.path=="/keyed" else "text/javascript")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/client.js":
                body = b'globalThis.clientSchema={envelope:"custom-envelope",revision:7};'
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    self.wfile.write(b'event: paragraph\ndata: first\n\nevent: finished-business-turn\ndata: last\n\n')
                    self.wfile.flush()
                    stopping.wait(5)
                except OSError:
                    pass
                self.close_connection = True
                return
            body = b'''<!doctype html><html><title>Generic discovery fixture</title><body>
              <button id="open">Open dialogue</button><div id="panel" hidden>
              <textarea aria-label="Your question"></textarea><button id="send">Deliver</button>
              <input type="password" value="private-password"><article aria-busy="false">Earlier answer</article></div>
              <script src="/client.js"></script><script>
              document.querySelector('#open').onclick=()=>document.querySelector('#panel').hidden=false;
              document.querySelector('#send').onclick=async()=>{
                const q=document.querySelector('textarea').value;
                const out=document.querySelector('article');out.setAttribute('aria-busy','true');
                const reply=await fetch('/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:q})});
                out.textContent=(await reply.json()).answer;out.setAttribute('aria-busy','false');
              };
              </script></body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps({"answer": "Answer: " + data["question"], "session_token": "private-response-token"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "session=private-session-cookie; Path=/")
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        worker.join(3)


def _node(snapshot, **attrs):
    return next(node for frame in snapshot["frames"] for node in frame["nodes"] if all(node.get(k) == v for k, v in attrs.items()))


@pytest.mark.browser
def test_multistep_dom_submission_records_http_and_full_same_turn_body(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            first = driver.open()
            assert not any(node["editable"] for f in first["frames"] for node in f["nodes"])
            driver.action({"kind": "click", "ref": _node(first, text="Open dialogue")["ref"]})
            panel = driver.inspect()
            driver.begin_turn("first")
            driver.action({"kind": "fill", "ref": _node(panel, name="Your question")["ref"], "value": "New question"})
            driver.action({"kind": "click", "ref": _node(panel, text="Deliver")["ref"]}, submit=True)
            driver.page.get_by_text("Answer: New question", exact=True).wait_for()
            result = driver.observe_turn("first")
            request = next(e for e in result["events"] if e["kind"] == "http_request" and e["url"].endswith("/ask"))
            submission = next(e for e in result["events"] if e["kind"] == "submission_attempted")
            assert submission["input_values"] == ["New question"]
            assert submission["observation_source"] == "browser_host"
            assert submission["method"] == "click"
            assert submission["page_id"] == result["page"]["active_page_id"] == first["active_page_id"]
            assert request["page_id"] == submission["page_id"]
            assert submission["sequence"] < request["sequence"]
            assert json.loads(request["body"]) == {"question": "New question"}
            chunks = [e for e in result["events"] if e["kind"] == "fetch_chunk"]
            assert json.loads("".join(e["text"] for e in chunks))["answer"] == "Answer: New question"
            assert result["submitted"]
            assert [e["sequence"] for e in result["events"]] == sorted(e["sequence"] for e in result["events"])
            assert "Earlier answer" in json.dumps(driver._turns["first"]["baseline"])
            assert "Earlier answer" not in json.dumps(result["page"])
            assert "private-password" not in json.dumps(result)
            assert "private-response-token" in json.dumps(result)
            assert "private-session-cookie" not in json.dumps(result)
            with pytest.raises(BrowserDiscoveryError, match="already_submitted"):
                driver.action({"kind": "click", "ref": _node(panel, text="Deliver")["ref"]}, submit=True)
        finally:
            driver.close()


@pytest.mark.browser
def test_open_sse_records_complete_application_events_without_transport_close(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            driver.open()
            driver.begin_turn("stream")
            result = driver.http_request({"url": url + "/stream"})
            assert result["started"]
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                driver.action({"kind":"wait","seconds":.05})
                events=driver.observe_turn("stream")["events"]
                if "finished-business-turn" in "".join(e.get("text","") for e in events):
                    break
            assert "finished-business-turn" in "".join(e.get("text", "") for e in events)
            assert not any(e["kind"] == "fetch_end" for e in events)
            assert not any(e["kind"] == "http_finished" and e["url"].endswith("/stream") for e in events)
        finally:
            driver.close()


@pytest.mark.browser
def test_named_eventsource_messages_are_observed_without_known_event_names(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            driver.open()
            driver.begin_turn("eventsource")
            # Neither the application nor the driver registers these event names.
            driver.page.evaluate("() => {globalThis.source=new EventSource('/stream');}")
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                driver.action({"kind":"wait","seconds":.05})
                events=[e for e in driver.observe_turn("eventsource")["events"] if e["kind"]=="eventsource_message"]
                if len(events)==2:
                    break
            assert [(e["event_name"], e["text"]) for e in events] == [("paragraph", "first"), ("finished-business-turn", "last")]
            assert all(e["observation_source"] == "browser_network" for e in events)
        finally:
            driver.close()


@pytest.mark.browser
def test_persistent_websocket_records_text_binary_and_second_turn(tmp_path):
    from websockets.sync.server import serve
    stopping = threading.Event()
    def handler(socket):
        try:
            for message in socket:
                socket.send("text:" + message)
                socket.send(b"\x01\x02binary-envelope")
        except Exception:
            pass
    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        with discovery_site() as url:
            driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3, allowed_origins=[address])
            try:
                driver.open()
                driver.page.evaluate("url => {globalThis.socket=new WebSocket(url);}", address)
                driver.page.wait_for_function("socket.readyState === 1")
                created = next(e for e in driver.events() if e["kind"] == "websocket_created")
                for turn in ("turn-one", "turn-two"):
                    driver.begin_turn(turn)
                    driver.websocket_send(created["connection_id"], turn, submit=True)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        driver.action({"kind": "wait", "seconds": .05})
                        if len([e for e in driver.observe_turn(turn)["events"] if e["kind"] == "websocket_received"]) >= 2:
                            break
                    events = driver.observe_turn(turn)["events"]
                    receipt=next(e for e in events if e["kind"]=="submission_attempted")
                    outbound=next(e for e in events if e["kind"]=="websocket_sent")
                    assert receipt["method"]=="websocket" and receipt["observation_source"]=="browser_host"
                    assert receipt["page_id"]==outbound["page_id"]==driver.inspect()["active_page_id"]
                    assert receipt["connection_id"]==outbound["connection_id"] and receipt["sequence"]<outbound["sequence"]
                    received = [e for e in events if e["kind"] == "websocket_received"]
                    assert received[0]["text"] == "text:" + turn
                    binary = received[1]
                    assert binary["opaque"] and "binary_b64" not in binary
                    assert driver.raw_event(binary["event_id"])["binary_b64"] == "AQJiaW5hcnktZW52ZWxvcGU="
                    assert not any(e["kind"] == "websocket_close" for e in events)
                assert driver.page.evaluate("socket instanceof WebSocket")
            finally:
                driver.close()
        server.shutdown()
        thread.join(3)


@pytest.mark.browser
def test_browser_network_actions_enforce_host_origins_and_password_controls(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            snapshot = driver.open()
            driver.action({"kind": "click", "ref": _node(snapshot, text="Open dialogue")["ref"]})
            snapshot = driver.inspect()
            private = _node(snapshot, private=True)
            assert private["value"] == "[host-private]"
            with pytest.raises(BrowserDiscoveryError, match="private_control"):
                driver.action({"kind": "fill", "ref": private["ref"], "value": "hidden"})
            with pytest.raises(BrowserDiscoveryError, match="origin_not_authorized"):
                driver.http_request({"url": "https://example.com/private"})
            with pytest.raises(BrowserDiscoveryError, match="submission_without_turn"):
                driver.action({"kind": "click", "ref": _node(snapshot, text="Deliver")["ref"]}, submit=True)
        finally:
            driver.close()


def test_agent_view_preserves_authorized_recorded_request(tmp_path):
    driver = GenericBrowserDriver(page_url="http://127.0.0.1:1000", profile_dir=tmp_path)
    event_id = driver._record("http_request", {"url": "http://127.0.0.1:1000/ask?access_token=private-token", "headers": {"authorization": "Bearer secret", "content-type": "application/json"}, "body": '{"question":"hi", "password":"secret-password"}'})
    view = driver._view(driver._event_map[event_id])
    assert all(secret in json.dumps(view) for secret in ("Bearer secret", "private-token", "secret-password"))
    assert json.loads(view["body"])["question"] == "hi"


@pytest.mark.browser
def test_loaded_client_resources_are_readable_and_paginated_without_server_sources(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", headless=True, timeout=3)
        try:
            driver.open()
            driver.page.wait_for_function("globalThis.clientSchema?.revision === 7")
            driver.action({"kind": "wait", "seconds": .1})
            inventory = driver.inspect()["client_resources"]
            assert any(r["url"] == url + "/client.js" for r in inventory)
            first = driver.client_resource(url + "/client.js", limit=20)
            second = driver.client_resource(url + "/client.js", offset=first["next_offset"], limit=1000)
            assert first["text"] + second["text"] == 'globalThis.clientSchema={envelope:"custom-envelope",revision:7};'
            assert second["next_offset"] is None
            with pytest.raises(BrowserDiscoveryError, match="not_observed"):
                driver.client_resource(url + "/private-server-source.py")
        finally:
            driver.close()


def test_old_request_completion_does_not_get_tagged_with_new_turn(tmp_path):
    driver = GenericBrowserDriver(page_url="http://127.0.0.1:1000", profile_dir=tmp_path)
    driver._current_turn = "new-turn"
    event_id = driver._record("http_finished", {"request_event_id": "old-request"}, turn_id=None)
    assert driver._event_map[event_id]["turn_id"] is None


def test_page_bridge_cannot_override_host_event_identifiers(tmp_path):
    driver = GenericBrowserDriver(page_url="http://127.0.0.1:1000", profile_dir=tmp_path)
    event_id = driver._record("fetch_chunk", {"event_id": "pretend-native-request", "sequence": -100,
                             "turn_id": "forged", "kind": "http_request"}, turn_id="actual")
    assert driver._event_map[event_id]["event_id"] == "event-1"
    assert driver._event_map[event_id]["sequence"] == 1
    assert driver._event_map[event_id]["turn_id"] == "actual"
    assert driver._event_map[event_id]["kind"] == "fetch_chunk"


@pytest.mark.browser
def test_discovery_tabs_are_isolated_without_closing_initial_user_page(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path/"profile", headless=True, timeout=3)
        try:
            driver.open()
            original = driver.page
            with pytest.raises(BrowserDiscoveryError,match="page_not_owned"):
                driver.action({"kind":"close_page"})
            result = driver.action({"kind":"open_page","url":url})
            assert result["page_index"] == 1
            assert driver.page != original
            driver.action({"kind":"close_page"})
            assert driver.page == original
            assert not original.is_closed()
        finally:
            driver.close()


@pytest.mark.browser
def test_browser_observation_identifies_actual_active_page_after_tab_selection(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            original=driver.open()
            assert original["active_page_index"]==0
            original_id=original["active_page_id"]
            driver.action({"kind":"open_page","url":url})
            scratch=driver.inspect()
            assert scratch["active_page_index"]==1
            assert scratch["active_page_id"]!=original_id
            driver.action({"kind":"select_page","page_index":0})
            state=driver.inspect()
            assert state["active_page_index"]==0
            assert state["active_page_id"]==original_id
            assert {frame["page_index"] for frame in state["frames"]}=={0,1}
            assert {frame["page_id"] for frame in state["frames"]}=={original_id,scratch["active_page_id"]}
        finally:
            driver.close()


@pytest.mark.browser
def test_http_and_navigation_receipts_are_host_owned_and_precede_actual_wire(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            first=driver.open()
            driver.begin_turn("http-host")
            driver.http_request({"url":url+"/ask","method":"POST","body":json.dumps({"question":"Host request"}),
                                 "headers":{"content-type":"application/json"}},submit=True)
            driver.page.wait_for_function("globalThis.__lladarDiscovery !== undefined")
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                observed=driver.observe_turn("http-host")
                requests=[e for e in observed["events"] if e["kind"]=="http_request" and e["url"].endswith("/ask")]
                if requests:
                    break
                driver.action({"kind":"wait","seconds":.05})
            receipt=next(e for e in observed["events"] if e["kind"]=="submission_attempted")
            assert receipt["observation_source"]=="browser_host" and receipt["method"]=="http"
            assert receipt["page_id"]==requests[0]["page_id"]==first["active_page_id"]
            assert receipt["sequence"]<requests[0]["sequence"]
            driver.begin_turn("navigation-host")
            driver.action({"kind":"navigate","url":url+"/?question=Navigation"},submit=True)
            observed=driver.observe_turn("navigation-host")
            receipt=next(e for e in observed["events"] if e["kind"]=="submission_attempted")
            assert receipt["method"]=="navigate" and receipt["page_id"]==first["active_page_id"]
            request=next(e for e in observed["events"] if e["kind"]=="http_request" and "question=Navigation" in e.get("url",""))
            assert receipt["sequence"]<request["sequence"]
        finally:
            driver.close()


@pytest.mark.browser
def test_foreign_page_control_reference_cannot_fill_or_submit_active_turn(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            first=driver.open()
            driver.action({"kind":"click","ref":_node(first,text="Open dialogue")["ref"]})
            original=driver.page
            original_snapshot=driver.inspect()
            editor=_node(original_snapshot,name="Your question")
            button=_node(original_snapshot,text="Deliver")
            driver.action({"kind":"open_page","url":url})
            driver.begin_turn("active-turn")
            with pytest.raises(BrowserDiscoveryError,match="browser_action_page_not_active"):
                driver.action({"kind":"fill","ref":editor["ref"],"value":"Wrong page question"})
            with pytest.raises(BrowserDiscoveryError,match="browser_action_page_not_active"):
                driver.action({"kind":"click","ref":button["ref"]},submit=True)
            assert original.get_by_role("textbox",name="Your question").input_value()==""
            assert not driver._turns["active-turn"]["submitted"]
            assert not any(e["kind"]=="http_request" and e.get("method")=="POST" for e in driver.observe_turn("active-turn")["events"])
        finally:
            driver.close()


@pytest.mark.browser
def test_host_dom_changes_preserve_all_blocks_and_arbitrary_state_attributes(tmp_path):
    with discovery_site() as url:
        driver = GenericBrowserDriver(page_url=url, profile_dir=tmp_path/"profile", headless=True, timeout=3)
        try:
            driver.open()
            driver.page.set_content('<main data-stage="idle">Earlier</main>')
            driver.begin_turn("dom-changes")
            driver.page.evaluate('''() => {const m=document.querySelector('main');m.textContent='';
                for(const text of ['First complete block','Second complete block']) {const p=document.createElement('p');p.textContent=text;m.append(p);}
                m.setAttribute('data-stage','finished');}''')
            result = driver.observe_turn("dom-changes")
            changed = next(e for e in result["events"] if e["kind"] == "dom_changed")
            assert changed["observation_source"] == "browser_dom"
            assert {c["after"]["text"] for c in changed["changes"] if c["after"] and c["after"]["tag"]=="p"} == {'First complete block','Second complete block'}
            main = _node(result["page"],tag="main")
            assert main["attributes"]["data-stage"] == "finished"
        finally:
            driver.close()


@pytest.mark.browser
def test_pending_turn_keeps_own_page_binding_during_isolated_repair_probe(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            original=driver.page
            baseline=driver.begin_turn("formal-pending")
            driver.http_request({"url":url+"/stream"},submit=True)
            original.evaluate("() => setTimeout(()=>fetch('/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:'Late formal result'})}),300)")
            driver.action({"kind":"open_page","url":url})
            driver.begin_turn("repair-probe")
            driver.http_request({"url":url+"/ask","method":"POST","headers":{"content-type":"application/json"},"body":json.dumps({"question":"Probe"})},submit=True)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                driver.action({"kind":"wait","seconds":.05})
                if any(e["kind"]=="http_request" and 'Late formal result' in e.get("body","") for e in driver.observe_turn("formal-pending")["events"]):
                    break
            assert not any('Late formal result' in e.get("body","") for e in driver.observe_turn("repair-probe")["events"])
            counts=len(driver._events)
            restored=driver.activate_turn("formal-pending")
            assert restored["submitted"] and driver.page==original
            assert driver._turns["formal-pending"]["baseline"]==baseline["baseline"]
            assert len(driver._events)==counts
            assert len([e for e in driver.observe_turn("formal-pending")["events"] if e["kind"]=="submission_attempted"])==1
        finally:
            driver.close()


@pytest.mark.browser
def test_native_http_budget_counts_each_dispatch_and_aborts_exhausted_request(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        calls=[]
        def debit():
            calls.append("dispatch")
            if len(calls)>1:
                raise BrowserDiscoveryError("budget_stopped")
        try:
            driver.open()
            assert calls==[]
            coverage=driver.enable_request_budget(debit)
            assert coverage["http"]=="all_after_enable"
            driver.begin_turn("budget")
            driver.http_request({"url":url+"/ask","method":"POST","headers":{"content-type":"application/json"},"body":json.dumps({"question":"First"})})
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                driver.action({"kind":"wait","seconds":.05})
                if any(e["kind"]=="http_body" and e.get("url","").endswith("/ask") for e in driver.events()):
                    break
            driver.http_request({"url":url+"/ask","method":"POST","headers":{"content-type":"application/json"},"body":json.dumps({"question":"Second"})})
            driver.page.wait_for_timeout(100)
            with pytest.raises(BrowserDiscoveryError,match="request_budget_exhausted"):
                driver.observe_turn("budget")
            assert len(calls)==2
        finally:
            driver.close()


@pytest.mark.browser
def test_websocket_budget_preserves_native_binary_observations_and_exact_wire(tmp_path):
    from websockets.sync.server import serve
    received=[]
    def handler(socket):
        try:
            for value in socket:
                received.append(value)
                socket.send(value)
        except Exception:
            pass
    with serve(handler,"127.0.0.1",0) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        ws_url=f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        with discovery_site() as url:
            driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3,allowed_origins=[ws_url])
            calls=[]
            try:
                driver.open()
                driver.enable_request_budget(lambda:calls.append("wire"))
                driver.action({"kind":"open_page","url":url})
                driver.page.evaluate("url=>{globalThis.peer=new WebSocket(url);}",ws_url)
                driver.page.wait_for_function("peer.readyState===1")
                calls.clear()  # exclude the normal page HTTP navigation
                driver.begin_turn("budget-ws")
                driver.page.evaluate("() => peer.send(new Uint8Array([0,1,255,37]))")
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    driver.action({"kind":"wait","seconds":.05})
                    events=driver.observe_turn("budget-ws")["events"]
                    if any(e["kind"]=="websocket_received" for e in events):
                        break
                assert received==[b'\x00\x01\xff%']
                assert calls==["wire"]
                assert any(e["kind"]=="websocket_sent" and e["observation_source"]=="browser_network" for e in events)
                assert any(e["kind"]=="websocket_received" and e.get("opaque") for e in events)
            finally:
                driver.close()
        server.shutdown()
        thread.join(3)


def test_capture_limit_fails_observation_instead_of_truncating(tmp_path, monkeypatch):
    monkeypatch.setenv("LLADAR_MAX_CAPTURE_BYTES", "200")
    driver = GenericBrowserDriver(page_url="http://127.0.0.1:1000", profile_dir=tmp_path)
    driver._record("fetch_chunk", {"text": "x" * 1000}, turn_id="turn")
    assert driver._overflow
    assert driver._events[0]["kind"] == "capture_limit"
    assert "x" * 1000 not in json.dumps(driver._events)


def test_http_template_bindings_keep_private_credentials_in_host_and_bind_observed_metadata(tmp_path):
    from types import SimpleNamespace
    driver=GenericBrowserDriver(page_url="https://service.test/chat",profile_dir=tmp_path)
    dispatched=[]
    driver.context=SimpleNamespace(cookies=lambda:[])
    driver.page=SimpleNamespace(evaluate=lambda code,spec:dispatched.append(spec) or "job", is_closed=lambda:False)
    initial=driver._record("http_request",{"url":"https://service.test/ask","method":"POST",
      "headers":{"authorization":"Bearer host-private-credential","content-type":"application/json"},
      "body":json.dumps({"private_session":"host-private-session","nested":{"turns":[{"question":"Calibration","request_id":"old-id"}]}})})
    reply=driver._record("http_body",{"url":"https://service.test/ask","text":'{"metadata":{"request_id":"dynamic-server-id","csrf":"host-private-csrf"}}'})
    driver.http_request({"request_ref":initial,"body_bindings":[
      {"path":["nested","turns",0,"question"],"value":"正式問題"},
      {"path":["nested","turns",0,"request_id"],"value_ref":{"event_id":reply,"path":["metadata","request_id"]}}]})
    actual=dispatched[0]
    assert actual["headers"]["authorization"]=="Bearer host-private-credential"
    body=json.loads(actual["body"])
    assert body["private_session"]=="host-private-session"
    assert body["nested"]["turns"][0]=={"question":"正式問題","request_id":"dynamic-server-id"}
    assert "host-private-credential" in json.dumps(driver.read_event(initial))
    assert "host-private-session" in json.dumps(driver.read_event(initial))


def test_binding_paths_scope_and_duplicate_json_fail_before_dispatch(tmp_path):
    from types import SimpleNamespace
    driver=GenericBrowserDriver(page_url="https://service.test/chat",profile_dir=tmp_path)
    dispatched=[]
    driver.context=SimpleNamespace(cookies=lambda:[])
    driver.page=SimpleNamespace(evaluate=lambda code,spec:dispatched.append(spec) or "job", is_closed=lambda:False)
    base={"url":"https://service.test/ask","method":"POST","body":'{"a":{"question":"old"}}'}
    with pytest.raises(BrowserDiscoveryError,match="path_invalid"):
        driver.http_request({**base,"body_bindings":[{"path":["a","__proto__"],"value":"new"}]})
    with pytest.raises(BrowserDiscoveryError,match="json_invalid"):
        driver.http_request({**base,"body":'{"a":1,"a":2}',"body_bindings":[{"path":["a"],"value":"new"}]})
    external=driver._record("http_body",{"url":"https://other.test/private","text":'{"id":"hidden"}'})
    with pytest.raises(BrowserDiscoveryError,match="scope_invalid"):
        driver.http_request({**base,"body_bindings":[{"path":["a","question"],"value_ref":{"event_id":external,"path":["id"]}}]})
    assert dispatched==[]


def test_one_character_cookie_flags_do_not_corrupt_numbers_or_binary_source(tmp_path):
    from types import SimpleNamespace
    driver=GenericBrowserDriver(page_url="https://service.test/chat",profile_dir=tmp_path)
    driver.context=SimpleNamespace(cookies=lambda:[{"name":"logged_in","value":"1"}])
    driver._refresh_secrets()
    assert driver._cookie_flag_names=={"logged_in"}
    assert "1" not in driver._secrets
    text='{"content":"Number 37, not 1; keep this negation.","authorization":"secret"}'
    identifier=driver._record("http_body",{"text":text})
    view=driver.read_event(identifier)
    assert json.loads(view["text"])["content"]=="Number 37, not 1; keep this negation."
    assert json.loads(view["text"])["authorization"]=="secret"
    binary=driver._record("websocket_received",{"binary_b64":"Mzcgbm90IDE="})
    assert driver.raw_event(binary)["binary_b64"]=="Mzcgbm90IDE="


@pytest.mark.browser
@pytest.mark.parametrize("attribute",["known-cookie","jwt-pattern"])
def test_sensitive_dom_attribute_name_blocks_entire_inspection_before_pager_can_capture(tmp_path,attribute):
    from lladar.browser_inspection import BrowserInspectionPager
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            driver.context.add_cookies([{"name":"session","value":"private-cookie-key-material","url":url}])
            name="data-private-cookie-key-material" if attribute=="known-cookie" else "data-eyJsynthetic.eyJsynthetic.signature"
            driver.page.evaluate("name=>document.querySelector('#open').setAttribute(name,'public value')",name)
            snapshot = driver.inspect()
            # HTML normalizes attribute names to lowercase.
            assert name.lower() in json.dumps(snapshot)
            assert BrowserInspectionPager(4096).capture(snapshot)
        finally:
            driver.close()


@pytest.mark.browser
@pytest.mark.parametrize("source",["native-json","client-javascript"])
def test_sensitive_native_source_keys_never_leave_through_events_read_event_or_client_source(tmp_path,source):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            if source=="native-json":
                driver.page.evaluate("async()=>await (await fetch('/keyed')).json()")
                path="/keyed"
            else:
                driver.page.add_script_tag(url=url+"/private-client.js")
                path="/private-client.js"
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                driver.page.wait_for_timeout(50)
                captured=[e for e in driver._events if e["kind"]=="http_body" and e.get("url","").endswith(path)]
                if captured:
                    break
            assert captured
            assert driver.events()
            assert driver.read_event(captured[0]["event_id"])["text"] == captured[0]["text"]
            if source=="client-javascript":
                assert driver.client_resource(url+path)["text"] == captured[0]["text"]
            assert "fixture-private-credential" not in str(BrowserDiscoveryError("protected_protocol_material"))
        finally:
            driver.close()


@pytest.mark.browser
def test_rearm_unsubmitted_turn_pins_fresh_baseline_without_refunding_host_observations(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        calls=[]
        try:
            opened=driver.open()
            driver.enable_request_budget(lambda:calls.append("actual request"))
            driver.begin_turn("unsent")
            driver.action({"kind":"click","ref":_node(opened,text="Open dialogue")["ref"]})
            current=driver.inspect()
            driver.action({"kind":"fill","ref":_node(current,name="Your question")["ref"],"value":"Unsent old input"})
            driver.page.evaluate("async()=>{await fetch('/client.js');document.querySelector('article').textContent='Fresh baseline';}")
            before=driver.observe_turn("unsent")
            old_ids=[e["event_id"] for e in before["events"]]
            old_bytes=driver._capture_bytes_by_turn["unsent"]
            request_count=len(calls)
            owner=driver.page
            driver.action({"kind":"open_page","url":url})
            all_before=len(driver.events())
            refreshed=driver.rearm_unsubmitted_turn("unsent")
            assert driver.page==owner and refreshed["turn_id"]=="unsent"
            assert not refreshed["submitted"] and refreshed["filled_inputs"]=={}
            assert "Fresh baseline" in json.dumps(refreshed["baseline"])
            assert refreshed["baseline"]==refreshed["last_page"]
            assert driver._capture_bytes_by_turn["unsent"]>=old_bytes
            assert len(calls)>=request_count and len(driver.events())>=all_before
            assert all(driver.raw_event(identifier)["turn_id"]=="unsent" for identifier in old_ids)
            fresh=driver.observe_turn("unsent")
            assert not any(e["event_id"] in old_ids for e in fresh["events"])
            assert all(e["sequence"]>=refreshed["start_sequence"] for e in fresh["events"])
            state=driver.inspect()
            driver.action({"kind":"fill","ref":_node(state,name="Your question")["ref"],"value":"Fresh actual question"})
            driver.action({"kind":"click","ref":_node(state,text="Deliver")["ref"]},submit=True)
            driver.page.get_by_text("Answer: Fresh actual question",exact=True).wait_for()
            events=driver.observe_turn("unsent")["events"]
            assert len([e for e in events if e["kind"]=="submission_attempted"])==1
            with pytest.raises(BrowserDiscoveryError,match="already_submitted"):
                driver.rearm_unsubmitted_turn("unsent")
        finally:
            driver.close()


@pytest.mark.browser
def test_unmarked_native_mutating_request_prevents_rearm_even_without_a_host_receipt(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            driver.begin_turn("unknown-dispatch")
            driver.page.evaluate("async()=>await fetch('/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:'Already sent outside host command'})})")
            observed=driver.observe_turn("unknown-dispatch")
            assert not observed["submitted"]
            assert not any(e["kind"]=="submission_attempted" for e in observed["events"])
            assert any(e["kind"]=="http_request" and e["method"]=="POST" for e in observed["events"])
            with pytest.raises(BrowserDiscoveryError,match="submission_already_attempted") as caught:
                driver.rearm_unsubmitted_turn("unknown-dispatch")
            assert caught.value.request_attempted
        finally:
            driver.close()


@pytest.mark.browser
def test_unsubmitted_turn_can_rearm_on_another_authorized_host_page_but_submitted_turn_cannot(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            original=driver.page
            before=driver.begin_turn("logical-turn")
            driver.action({"kind":"open_page","url":url})
            replacement=driver.page
            new=driver.rearm_unsubmitted_turn("logical-turn",page=replacement)
            assert driver.page==replacement and not original.is_closed()
            assert new["baseline"]["active_page_id"]!=before["baseline"]["active_page_id"]
            assert driver._page_turns[original] is None
            original.evaluate("async()=>await fetch('/client.js')")
            assert not any(e["turn_id"]=="logical-turn" and e.get("page_id")==before["baseline"]["active_page_id"]
                           and e["sequence"]>=new["start_sequence"] for e in driver.events())
            driver.http_request({"url":url+"/ask","method":"POST","body":json.dumps({"question":"Replacement question"})},submit=True)
            with pytest.raises(BrowserDiscoveryError,match="already_submitted"):
                driver.rearm_unsubmitted_turn("logical-turn",page=original)
            assert driver.page==replacement
        finally:
            driver.close()


@pytest.mark.browser
def test_rearm_cannot_attach_unobserved_or_closed_host_page(tmp_path):
    with discovery_site() as url:
        driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3)
        try:
            driver.open()
            owner=driver.page
            driver.begin_turn("unsubmitted-owner")
            foreign=driver.context.new_page()  # about:blank has no approved service origin
            with pytest.raises(BrowserDiscoveryError,match="origin_not_authorized"):
                driver.rearm_unsubmitted_turn("unsubmitted-owner",page=foreign)
            assert driver.page==owner
            foreign.close()
            with pytest.raises(BrowserDiscoveryError,match="page_lost"):
                driver.rearm_unsubmitted_turn("unsubmitted-owner",page=foreign)
            assert driver.page==owner
        finally:
            driver.close()


@pytest.mark.browser
def test_unmarked_native_websocket_send_is_unknown_and_cannot_rearm(tmp_path):
    from websockets.sync.server import serve
    def handler(socket):
        try:
            for value in socket:
                socket.send(value)
        except Exception:
            pass
    with serve(handler,"127.0.0.1",0) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        ws_url=f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        with discovery_site() as url:
            driver=GenericBrowserDriver(page_url=url,profile_dir=tmp_path/"profile",headless=True,timeout=3,allowed_origins=[ws_url])
            try:
                driver.open()
                driver.page.evaluate("url=>{globalThis.peer=new WebSocket(url)}",ws_url)
                driver.page.wait_for_function("peer.readyState===1")
                driver.begin_turn("native-unknown")
                driver.page.evaluate("()=>peer.send(new Uint8Array([1,2,3]))")
                driver.page.wait_for_timeout(100)
                before=driver.observe_turn("native-unknown")
                assert not before["submitted"]
                assert any(e["kind"]=="websocket_sent" for e in before["events"])
                with pytest.raises(BrowserDiscoveryError,match="submission_already_attempted"):
                    driver.rearm_unsubmitted_turn("native-unknown")
            finally:
                driver.close()
        server.shutdown()
        worker.join(3)

