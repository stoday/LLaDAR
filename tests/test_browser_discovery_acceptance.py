"""Public-target local acceptance with independent server-held answer oracles.

The Agent and extraction models below are explicit deterministic stand-ins. This
proves browser/codec/qualification data flow, not real-model discovery or any live
ChatGPT, Gemini, EIP or Streamlit acceptance.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sys
from types import SimpleNamespace
import threading

import pytest

pytestmark = pytest.mark.usefixtures("qualified_browser_demonstration")

from lladar.answer_extraction import ExtractionOptions
from lladar.browser_discovery_driver import GenericBrowserDriver
from lladar.browser_target import BrowserTarget


pytestmark = [pytest.mark.browser, pytest.mark.usefixtures("qualified_browser_demonstration")]


def _answer(question):
    return "第一段：完整回答「" + question + "」。\n\n第二段：數字 37；不是 73。尾段必須保留。"


def _encode(envelope):
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return b"LG" + len(body).to_bytes(4, "big") + body


def _decode(wire):
    assert wire[:2] == b"LG"
    assert int.from_bytes(wire[2:6], "big") == len(wire) - 6
    return json.loads(wire[6:].decode("utf-8"))


@contextmanager
def acceptance_site(*, socket_url=None, pure_dom=False, omit_tail=False, reference_guard=False):
    stopped = threading.Event()
    answers, jobs, submitted = {}, {}, []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass
        def respond(self, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path.startswith("/jobs/"):
                key = self.path.rsplit("/", 1)[-1]
                job = jobs[key]
                job["reads"] += 1
                if job["reads"] == 1:
                    self.respond({"job_id": key, "stage": "building", "question": job["question"]})
                else:
                    self.respond({"job_id": key, "stage": "finished", "question": job["question"], "content": answers[job["question"]]})
                return
            socket_script = "globalThis.peer=new WebSocket(" + json.dumps(socket_url) + ");" if socket_url else ""
            dom_script = '''document.querySelector('button').onclick=()=>{
                const question=document.querySelector('textarea').value;
                const block=document.querySelector('main');block.setAttribute('data-stage','working');
                setTimeout(()=>{block.textContent='';
                  for (const text of ['第一段：完整回答「'+question+'」。','第二段：數字 37；不是 73。尾段必須保留。']) {
                    const paragraph=document.createElement('p');paragraph.textContent=text;block.append(paragraph);
                  }
                  block.setAttribute('data-stage','finished');
                },30);
              };''' if pure_dom else ""
            if reference_guard:
                dom_script = dom_script.replace("block.setAttribute('data-stage','working');",
                    "block.setAttribute('data-stage','working');block.setAttribute('aria-busy','true');")
                dom_script = dom_script.replace("block.setAttribute('data-stage','finished');",
                    "block.setAttribute('data-stage','finished');block.setAttribute('aria-busy','false');")
            body = ("<!doctype html><html><title>Independent acceptance</title><body><h1>Dialogue</h1>"
                    "<textarea aria-label='Question'></textarea><button>Send</button><main data-stage='idle'>New conversation</main>"
                    "<script>" + socket_script + dom_script + "</script></body></html>")
            if reference_guard:
                body = body.replace('<main data-stage=', '<main aria-busy="false" data-stage=')
            body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            envelope = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            question = envelope["question"]
            submitted.append(question)
            answers[question] = _answer(question)
            if self.path == "/ask/json":
                self.respond({"question": question, "status": "finished", "content": answers[question]})
            elif self.path == "/ask/jobs":
                job_id = secrets.token_hex(12)
                jobs[job_id] = {"question": question, "reads": 0}
                self.respond({"question": question, "status": "accepted", "job_id": job_id})
            elif self.path == "/ask/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                halves = answers[question].split("\n\n")
                data = [
                    {"question": question, "stage": "fragment", "content": halves[0] + "\n\n"},
                    {"question": question, "stage": "finished", "content": halves[1]},
                ]
                if omit_tail:
                    data = [{"question": question, "stage": "finished", "content": halves[0]}]
                try:
                    for part in data:
                        self.wfile.write(("event: wire-envelope\ndata: " + json.dumps(part, ensure_ascii=False) + "\n\n").encode())
                        self.wfile.flush()
                    stopped.wait(10)
                except OSError:
                    pass
                self.close_connection = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", answers=answers, jobs=jobs, submitted=submitted, pure_dom=pure_dom)
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


SETUP = '''
    if phase == "setup":
        actions = []
        if context.get("scratch"):
            return {"complete":True,"context":context}
        actions.append({"kind":"open_page", "url":BASE})
        return {"actions":actions, "complete":not actions, "context":{"scratch":True}}
    if phase == "restore":
        actions = [{"kind":"close_page"}] if context.get("scratch") else []
        return {"actions":actions, "context":{"scratch":False}}
'''


def http_program(url, mode):
    return "import json\nBASE=" + repr(url) + "\nMODE=" + repr(mode) + '''
def adapt(payload):
    phase=payload["phase"]
    context=payload.get("context", {})
''' + SETUP + '''
    if phase == "send":
        context["question"]=payload["question"]
        context["polled"]=[]
        return {"actions":[{"kind":"http", "url":BASE+"/ask/"+MODE,
          "method":"POST", "headers":{"content-type":"application/json"},
          "body":json.dumps({"question":payload["question"]}), "submit":True}],"context":context}
    if phase == "observe":
        events=payload["observation"]["events"]
        requests=[e for e in events if e["kind"]=="http_request" and e.get("url","").endswith("/ask/"+MODE)]
        if not requests:
            return {"context":context}
        request=requests[0]
        if MODE == "json":
            bodies=[e for e in events if e["kind"]=="http_body" and e.get("request_event_id")==request["event_id"]]
            ends=[e for e in events if e["kind"]=="http_finished" and e.get("request_event_id")==request["event_id"]]
            if bodies and ends and json.loads(bodies[0]["text"])["status"]=="finished":
                return {"complete":True,"context":context,"evidence":{"submission_events":[request["event_id"]],"completion_events":[ends[0]["event_id"]]},"materials":[{"event_ids":[bodies[0]["event_id"]],"data":bodies[0]["text"]}]}
        if MODE == "stream":
            starts=[e for e in events if e["kind"]=="fetch_start" and e.get("url","").endswith("/ask/stream")]
            if starts:
                chunks=[e for e in events if e["kind"]=="fetch_chunk" and e.get("request_id")==starts[0]["request_id"]]
                wire="".join(e.get("text","") for e in chunks)
                records=[json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
                if records and records[-1]["stage"]=="finished" and records[-1]["question"]==context["question"]:
                    return {"complete":True,"context":context,"evidence":{"submission_events":[request["event_id"]],"completion_events":[chunks[-1]["event_id"]]},"materials":[{"event_ids":[e["event_id"] for e in chunks],"data":wire}]}
        if MODE == "jobs":
            bodies=[e for e in events if e["kind"]=="http_body" and e.get("request_event_id")==request["event_id"]]
            if not bodies:
                return {"context":context}
            ack=json.loads(bodies[0]["text"])
            context["job_id"]=ack["job_id"]
            poll_bodies=[e for e in events if e["kind"]=="http_body" and e.get("url","").endswith("/jobs/"+context["job_id"])]
            for body in poll_bodies:
                record=json.loads(body["text"])
                if record["stage"]=="finished" and record["question"]==context["question"]:
                    materials=[{"event_ids":[bodies[0]["event_id"]],"data":bodies[0]["text"]}]
                    materials.extend({"event_ids":[e["event_id"]],"data":e["text"]} for e in poll_bodies)
                    return {"complete":True,"context":context,"evidence":{"submission_events":[request["event_id"]],"completion_events":[body["event_id"]]},"materials":materials}
            read_count=len(poll_bodies)
            if read_count not in context["polled"]:
                context["polled"].append(read_count)
                return {"actions":[{"kind":"http","url":BASE+"/jobs/"+context["job_id"],"method":"GET"}],"context":context}
    return {"context":context}
'''


def binary_program(url):
    return "import json\nimport base64\nBASE=" + repr(url) + '''
def decode(text):
    wire=base64.b64decode(text)
    if wire[:2] != b"LG":
        return {}
    size=(wire[2]<<24)|(wire[3]<<16)|(wire[4]<<8)|wire[5]
    if size != len(wire)-6:
        return {}
    return json.loads(wire[6:].decode("utf-8"))
def adapt(payload):
    phase=payload["phase"]
    context=payload.get("context", {})
''' + SETUP + '''
    if phase == "send":
        context["pending"]={"question":payload["question"],"client_turn":payload["turn_id"]}
        context["sent"]=False
    if phase in ["send","observe"] and not context.get("sent") and context.get("pending"):
        page=payload["observation"]["page"]
        last_page=max(f["page_index"] for f in page["frames"])
        connections=[c for c in page.get("connections",[]) if c["state"]==1 and c["page_index"]==last_page]
        if connections:
            socket=sorted(connections,key=lambda c:c["page_index"])[-1]
            raw=json.dumps(context["pending"],ensure_ascii=False).encode("utf-8")
            wire=b"LG"+bytes.fromhex(hex(len(raw))[2:].rjust(8,"0"))+raw
            context["sent"]=True
            return {"actions":[{"kind":"websocket","connection_id":socket["connection_id"],"value":base64.b64encode(wire).decode("ascii"),"binary":True,"submit":True}],"context":context}
    if phase == "observe":
        events=payload["observation"]["events"]
        sent=[e for e in events if e["kind"]=="websocket_sent" and e.get("binary_b64")]
        received=[e for e in events if e["kind"]=="websocket_received" and e.get("binary_b64")]
        selected=[]
        for event in received:
            envelope=decode(event["binary_b64"])
            if envelope.get("client_turn")==context["pending"]["client_turn"]:
                selected.append({"event_ids":[event["event_id"]],"source_hashes":{event["event_id"]:event["payload_sha256"]},"data":json.dumps(envelope,ensure_ascii=False)})
        if sent and selected and json.loads(selected[-1]["data"])["stage"]=="finished":
            return {"complete":True,"context":context,"evidence":{"submission_events":[sent[0]["event_id"]],"completion_events":selected[-1]["event_ids"],
              "decoded_submission":{"event_id":sent[0]["event_id"],"data":decode(sent[0]["binary_b64"]),"source_sha256":sent[0]["payload_sha256"]}},"materials":selected}
    return {"context":context}
'''


def dom_program(url):
    return "import json\nBASE=" + repr(url) + '''
def adapt(payload):
    phase=payload["phase"]
    context=payload.get("context",{})
''' + SETUP + '''
    if phase == "send":
        frames=payload["observation"]["page"]["frames"]
        frame=sorted(frames,key=lambda f:f["page_index"])[-1]
        inputs=[n for n in frame["nodes"] if n["editable"] and not n["private"]]
        buttons=[n for n in frame["nodes"] if n["tag"]=="button"]
        return {"actions":[{"kind":"fill","ref":inputs[0]["ref"],"value":payload["question"]},
          {"kind":"click","ref":buttons[0]["ref"],"submit":True}],"context":context}
    if phase == "observe":
        observation=payload["observation"]
        frames=observation["page"]["frames"]
        frame=sorted(frames,key=lambda f:f["page_index"])[-1]
        terminal=[n for n in frame["nodes"] if n.get("attributes",{}).get("data-stage")=="finished"]
        sent=[e for e in observation["events"] if e["kind"]=="submission_attempted" and e.get("observation_source")=="browser_host"]
        changes=[e for e in observation["events"] if e["kind"]=="dom_changed"]
        if terminal and sent and changes:
            node=terminal[0]
            return {"complete":True,"context":context,"materials":[{"frame_id":frame["frame_id"],"event_ids":[changes[-1]["event_id"]],"data":json.dumps(frame,ensure_ascii=False)}],
              "evidence":{"submission_events":[sent[0]["event_id"]],"completion_events":[changes[-1]["event_id"]],
                "dom_completion":{"frame_id":frame["frame_id"],"ref":node["ref"],"attribute":"data-stage","before":"idle","after":"finished"}}}
    return {"context":context}
'''


def fixture_models(program):
    extracted = []
    def agent_factory(**options):
        def agent(_):
            options["tools"]["submit_adapter"](program, "Deterministic fixture Agent supplied observed protocol programme")
            result = json.loads(options["tools"]["test_adapter"]())
            if result["status"] != "accepted":
                print("Fixture adapter feedback: " + json.dumps(result), file=sys.stderr)
            assert result["status"] == "accepted", result
        return agent
    def provider_factory(**_):
        def extract(request, **_):
            payload=json.loads(request['body'])
            if isinstance(payload,dict) and payload.get('kind')=='independent_dom_reference':
                text=next(node['rendered_text'] for frame in payload['after']
                    for node in frame['nodes'] if node['tag']=='main')
                return {'result':{'status':'final','text':text},'usage':None}
            records=[]
            for material in payload:
                data=material["data"]
                if data.startswith("event:"):
                    records.extend(json.loads(line[6:]) for line in data.splitlines() if line.startswith("data: "))
                else:
                    records.append(json.loads(data))
            if records and records[-1].get("nodes"):
                # Explicit fixture-model stand-in; programme passes the whole DOM.
                text="\n\n".join(n["text"] for n in records[-1]["nodes"] if n["tag"]=="p")
            else:
                text="".join(record.get("content", "") for record in records)
            extracted.append({"records":records,"text":text})
            return {"result":{"status":"final","text":text},"usage":None}
        return SimpleNamespace(extract=extract)
    return agent_factory, provider_factory, extracted


def build_target(tmp_path, site, source, *, extra_origins=None, conversation_policy="new_each"):
    agent, provider, extracted = fixture_models(source)
    driver = GenericBrowserDriver(page_url=site.url, profile_dir=tmp_path/"profile", timeout=5, headless=True,
                                  allowed_origins=extra_origins)
    def reference_reader(turn):
        request=driver.raw_event(turn["evidence"]["submission_events"][0])
        if request.get("input_values"):
            return _answer(request["input_values"][0])
        envelope=_decode(base64.b64decode(request["binary_b64"])) if request.get("binary_b64") else json.loads(request["body"])
        return site.answers[envelope["question"]]
    target=BrowserTarget(page_url=site.url, driver=driver, workspace=tmp_path/"workspace", timeout=8,
        agent_factory=agent, confirmed=True,
        conversation_policy=conversation_policy,
        extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=provider),
        reference_reader=reference_reader,output_fn=lambda _:None)
    return target,driver,extracted


@pytest.mark.parametrize("mode", ["json", "stream", "jobs"])
def test_generated_network_workflow_qualifies_and_runs_new_formal_question(tmp_path, mode):
    with acceptance_site() as site:
        target,driver,extracted=build_target(tmp_path,site,http_program(site.url,mode))
        try:
            target.prepare(["NEVER_EXPOSE_FUTURE_QUESTION"],interactive=False,record_count=1,request_count=1)
            assert target.evidence["status"]=="verified"
            assert len(target.evidence["verification"])==1
            question="正式驗收問題：請保留全文。"
            assert target.answer(question,"formal-local")==site.answers[question]
            assert len(site.submitted)==3
            assert len(extracted)==3
            assert "NEVER_EXPOSE_FUTURE_QUESTION" not in json.dumps(site.submitted)
            assert target.ledger.data["turns"]["formal-local"]["status"]=="completed"
            assert all(row["reference_method"]=="independent_reference" for row in target.evidence["verification"])
            if mode=="stream":
                events=driver.observe_turn("formal-local")["events"]
                assert not any(e["kind"]=="fetch_end" for e in events)
                assert len(extracted[-1]["records"])==2
            if mode=="jobs":
                assert len(site.jobs)==3
                assert all(job["reads"]==2 for job in site.jobs.values())
                assert len(extracted[-1]["records"])==3  # ack, progress, complete
            assert (tmp_path/"workspace"/"adapter.json").is_file()
        finally:
            target.close()


@pytest.mark.parametrize('known_private', [False, True], ids=['whole-wire', 'authorized-private-metadata'])
def test_agent_supplied_binary_codec_runs_persistent_native_socket_without_preset_adapter(tmp_path, known_private):
    from websockets.sync.server import serve
    holder={}
    def handler(socket):
        try:
            for frame in socket:
                envelope=_decode(frame)
                site=holder["site"]
                question=envelope["question"]
                site.submitted.append(question)
                site.answers[question]=_answer(question)
                pieces=site.answers[question].split("\n\n")
                socket.send(_encode({"client_turn":"background-unrelated","stage":"finished","content":"Must not be mistaken for this turn"}))
                metadata={'session_id':'local-private-session-f258'} if known_private else {}
                socket.send(_encode({"client_turn":envelope["client_turn"],"stage":"fragment","content":pieces[0]+"\n\n",**metadata}))
                socket.send(_encode({"client_turn":envelope["client_turn"],"stage":"finished","content":pieces[1],**metadata}))
        except Exception:
            pass
    with serve(handler,"127.0.0.1",0) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        socket_url=f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        with acceptance_site(socket_url=socket_url) as site:
            holder["site"]=site
            target,driver,extracted=build_target(tmp_path,site,binary_program(site.url),extra_origins=[socket_url])
            if known_private:
                driver._secrets.add('local-private-session-f258')
            try:
                try:
                    target.prepare([],interactive=False,record_count=1,request_count=1)
                except Exception:
                    print("Fixture wire diagnostics: "+json.dumps([
                        {"kind":e["kind"],"turn":e.get("turn_id"),"connection":e.get("connection_id"),
                         "sequence":e["sequence"],"bytes":e.get("bytes"),"binary":bool(e.get("binary_b64"))}
                        for e in driver._events if "websocket" in e["kind"]]),file=sys.stderr)
                    raise
                question="二進位正式回合，尾段不可省略。"
                assert target.answer(question,"formal-binary")==site.answers[question]
                assert len(site.submitted)==3
                sends=[e for e in driver.events() if e["kind"]=="websocket_sent"]
                assert len({e["connection_id"] for e in sends})==3
                assert len(extracted[-1]["records"])==2
                assert all("background-unrelated" not in json.dumps(r) for r in extracted[-1]["records"])
                events=driver.observe_turn("formal-binary")["events"]
                assert any(e["kind"]=="websocket_received" and e.get("opaque") for e in events)
                assert not any(e["kind"]=="websocket_close" for e in events)
                if known_private:
                    assert 'local-private-session-f258' in json.dumps(extracted)
                    private_sources=[e for e in driver._events if e.get('turn_id')=='formal-binary'
                                     and e['kind']=='websocket_received' and e.get('binary_b64')
                                     and b'local-private-session-f258' in base64.b64decode(e['binary_b64'])]
                    assert private_sources
                    for original in private_sources:
                        view=target._protocol_authority(original['event_id'])
                        assert original['payload_sha256']==view['payload_sha256']
                        assert original['binary_b64']==view['binary_b64']
            finally:
                target.close()
        server.shutdown()
        thread.join(3)


def test_pure_dom_multiblock_fallback_is_qualified_against_independent_fulltext(tmp_path):
    with acceptance_site(pure_dom=True) as site:
        target,driver,extracted=build_target(tmp_path,site,dom_program(site.url))
        try:
            target.prepare([],interactive=False,record_count=1,request_count=1)
            question="DOM 多段同一輪回答。"
            assert target.answer(question,"formal-dom")==_answer(question)
            assert target.evidence["send_method"]=="dom"
            assert target.evidence["receive_method"]=="dom"
            events=driver.observe_turn("formal-dom")["events"]
            assert not any(e["kind"]=="http_request" and e.get("method")=="POST" for e in events)
            assert any(e["kind"]=="dom_changed" for e in events)
            assert len([n for n in extracted[-1]["records"][0]["nodes"] if n["tag"]=="p"])==2
            assert len(target.evidence["verification"])==1
        finally:
            target.close()


def test_terminal_marker_cannot_qualify_missing_tail_against_fulltext_oracle(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    with acceptance_site(omit_tail=True) as site:
        target,driver,extracted=build_target(tmp_path,site,http_program(site.url,"stream"))
        try:
            with pytest.raises(BrowserDiscoveryError):
                target.prepare([],interactive=False,record_count=1,request_count=1)
            assert target.evidence["status"]=="blocked"
            assert target.evidence["candidates"][-1]["reason"]=="reference_mismatch"
            assert len(site.submitted)==1
            assert not (tmp_path/"workspace"/"adapter.json").exists()
            with pytest.raises(BrowserDiscoveryError,match="adapter_not_qualified"):
                target.answer("Formal question must not be sent","formal-blocked")
            assert len(site.submitted)==1
        finally:
            target.close()


def test_existing_policy_keeps_original_page_across_two_formal_turns(tmp_path):
    with acceptance_site() as site:
        source=http_program(site.url,"json").replace('    if phase == "setup":\n',
            '    if phase == "setup" and payload.get("role") == "formal":\n        return {"complete":True,"context":context}\n    if phase == "setup":\n')
        target,driver,extracted=build_target(tmp_path,site,source,conversation_policy="existing")
        try:
            target.prepare([],interactive=False,record_count=2,request_count=2)
            original=driver.page
            for number in range(2):
                question="同一既有會話的正式問題 "+str(number+1)
                assert target.answer(question,"formal-existing-"+str(number))==site.answers[question]
                assert driver.page==original
                assert driver.context.pages==[original]
            assert len(site.submitted)==4
            assert target.conversation_policy=="existing"
            assert all(turn["status"]=="completed" for turn in target.ledger.data["turns"].values())
        finally:
            target.close()


@pytest.mark.parametrize('reference_guard', [True, False])
def test_formal_reference_uses_independent_dom_only_after_full_answer_qualification(tmp_path, reference_guard, monkeypatch):
    monkeypatch.setenv("LLADAR_BROWSER_DISCOVERY_VERIFICATIONS", "3")
    from io import StringIO
    class ReviewStream(StringIO):
        def isatty(self):
            return True
    with acceptance_site(pure_dom=True,reference_guard=reference_guard) as site:
        source=dom_program(site.url)
        agent,provider,_extracted=fixture_models(source)
        reviews=[]
        driver=GenericBrowserDriver(page_url=site.url,profile_dir=tmp_path/'profile',timeout=5,headless=True)
        target=BrowserTarget(page_url=site.url,driver=driver,workspace=tmp_path/'workspace',timeout=8,
            agent_factory=agent,confirmed=True,conversation_policy='new_each',
            extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=provider),
            input_fn=lambda prompt: reviews.append(prompt) or 'MATCH',review_stream=ReviewStream(),
            output_fn=lambda _:None)
        try:
            target.prepare([],interactive=False,record_count=1,request_count=1)
            assert len(reviews)==4  # One probe and three fresh trusted verifications.
            assert target.evidence.get('independent_reference_qualified',False) is reference_guard
            question='A complete new formal answer with its final paragraph.'
            assert target.answer(question,'formal-independent-reference')==_answer(question)
            row=target.ledger.data['turns']['formal-independent-reference']
            if reference_guard:
                assert len(reviews)==4
                assert row['reference_method']=='qualified_independent_dom_reference'
            else:
                assert len(reviews)==5
                assert row['reference_method']=='manual_full_answer_review'
        finally:
            target.close()


def test_inspection_tools_cannot_dispatch_an_unqualified_question(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    with acceptance_site(pure_dom=True) as site:
        class DraftDriver(GenericBrowserDriver):
            def action(self, spec, **options):
                result=super().action(spec,**options)
                if spec.get('kind')=='open_page':
                    self.page.locator('textarea').fill('Existing unsent draft')
                return result
        driver=DraftDriver(page_url=site.url,profile_dir=tmp_path/'profile',timeout=5,headless=True)
        def factory(**settings):
            def agent(_):
                tools=settings['tools']
                tools['inspect_action'](json.dumps({'kind':'open_page','url':site.url}))
                snapshot=json.loads(tools['inspect_browser']())
                frame=next(frame for frame in snapshot['frames'] if frame['page_index']==snapshot['active_page_index'])
                input_node=next(node for node in frame['nodes'] if node['tag']=='textarea')
                button=next(node for node in frame['nodes'] if node['tag']=='button')
                for action in ({'kind':'fill','ref':input_node['ref'],'value':'An ad hoc question'},
                               {'kind':'click','ref':button['ref']}):
                    result=json.loads(tools['inspect_action'](json.dumps(action)))
                    assert result=={'status':'needs_revision','reason':'unqualified_submission'}
                current=json.loads(tools['inspect_browser']())
                assert any(node.get('attributes',{}).get('data-stage')=='idle'
                    for frame in current['frames'] if frame['page_index']==current['active_page_index']
                    for node in frame['nodes'])
            return agent
        target=BrowserTarget(page_url=site.url,driver=driver,workspace=tmp_path/'workspace',
            agent_factory=factory,confirmed=True,
            extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=lambda **_:None),
            output_fn=lambda _:None)
        try:
            with pytest.raises(BrowserDiscoveryError,match='adapter_not_qualified'):
                target.prepare([],interactive=False,record_count=1,request_count=1)
            assert target.ledger.data['counts'].get('submissions',0)==0
            assert site.submitted==[]
        finally:
            target.close()

