"""Public contracts for recorded evidence and browser-independent execution."""
import pytest
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import os
import secrets

from lladar.cli import build_parser


def test_cli_accepts_one_demonstration_and_independent_adapter():
    args = build_parser().parse_args([
        'run-agent', 'dataset.jsonl', '--page-url', 'https://example.test/chat',
        '--demo-question', 'Explain this service', '--adapter', 'manifest.json',
        '--conversation-policy', 'independent',
    ])
    assert (args.demo_question, args.adapter, args.conversation_policy) == (
        'Explain this service', 'manifest.json', 'independent')


@pytest.mark.browser
def test_explicit_demonstration_question_is_sent_verbatim_and_recoverable(tmp_path, chat_service):
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.independent_target import IndependentTarget

    endpoint, received = chat_service
    question = 'How does Streamlit caching work?'
    recorder = DemonstrationRecorder(page_url=endpoint, workspace=tmp_path, headless=True)
    target = IndependentTarget(page_url=endpoint, workspace=tmp_path,
        recorder=recorder, probe_question=question, confirmed=True,
        output_fn=lambda _: None)
    assert target.demo_question == question

    def human(_prompt):
        recorder.browser.page.locator('#question').fill(target.demo_question)
        recorder.browser.page.locator('#send').click()
        recorder.browser.page.wait_for_function(
            "document.querySelector('#answer').textContent.startsWith('Exact reply:')")
        return ''

    try:
        state = recorder.capture(target.demo_question, human, lambda _: None)
        assert received == [question]
        metadata_path = tmp_path / 'recording' / 'demonstration.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        assert metadata['question'] == question and metadata['submission_refs']
        metadata_path.unlink()
        assert recorder.recover_completed_capture(state) == metadata
    finally:
        target.close()


def test_cli_help_explains_demonstration_shutdown_and_user_actions(capsys):
    from lladar.cli import main
    with pytest.raises(SystemExit) as caught:
        main(['run-agent', '--help'])
    assert caught.value.code == 0
    help_text = ' '.join(capsys.readouterr().out.split())
    assert 'save the recording and session state, then close the browser' in help_text
    assert 'wait for the complete answer' in help_text
    assert '--demo-question' in help_text
    assert '--probe-question' not in help_text


def test_cli_explains_empty_demonstration_question_in_selected_language(capsys):
    from lladar.cli import main
    from fixture_extraction import browser_cli_terminal
    with browser_cli_terminal(), pytest.raises(SystemExit):
        main(['run-agent','missing.jsonl','--page-url','https://example.test/chat',
              '--demo-question','','--lang','zh-TW'])
    assert '示範題不得空白' in capsys.readouterr().err


def test_agent_finds_demonstration_among_background_events_without_loading_bodies(tmp_path):
    from lladar.recording_store import RecordingStore
    store = RecordingStore(tmp_path / 'recording')
    for index in range(1500):
        store.append({'kind': 'http_response', 'page_id': 'background',
                      'text': 'unrelated telemetry ' + str(index)})
    sent = store.append({'kind': 'websocket_sent', 'page_id': 'chat',
                         'connection_id': 'socket-7', 'text': 'hello DEMO-unique'})
    reply = store.append({'kind': 'websocket_received', 'page_id': 'chat',
                          'connection_id': 'socket-7', 'text': 'answer ' * 1000})
    reopened = RecordingStore(tmp_path / 'recording')
    found = reopened.query_recording(query='DEMO-unique', limit=5)
    assert [item['event_id'] for item in found['events']] == [sent]
    assert 'text' not in found['events'][0]
    related = reopened.query_recording(connection_id='socket-7', after=1501)
    assert [item['event_id'] for item in related['events']] == [reply]
    first = reopened.read_material(reply, limit=100)
    assert first['complete'] is False and first['next_offset'] == 100
    assert len(first['text']) == 100 and first['total_chars'] > 6000


@pytest.fixture
def chat_service():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'''<textarea id="question"></textarea><button id="send">Send</button><div id="answer"></div>
            <script>send.onclick=async()=>{answer.textContent='';const r=await fetch('/chat',{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:question.value})});
            answer.textContent=(await r.json()).answer;};</script>'''
            self.send_response(200)
            self.send_header('Set-Cookie', 'sid=recorded-login; Path=/')
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'sid=recorded-login' not in self.headers.get('Cookie', ''):
                self.send_error(401)
                return
            received.append(value['question'])
            reply = {'answer': 'Exact reply: ' + value['question']}
            if value['question'] == 'changed schema':
                reply = {'answer': 'intermediate progress', 'final_answer': 'Exact reply: changed schema'}
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}', received
    server.shutdown()
    server.server_close()


HTTP_ADAPTER = '''
import json
def adapt(payload):
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': payload['state']['endpoint'],
            'method': 'POST', 'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'question': payload['question']}), 'submit': True}]}
    if payload['phase'] != 'receive':
        return {'complete': False}
    events = payload['events']
    parts = [e for e in events if e['kind'] == 'http_body']
    ends = [e for e in events if e['kind'] == 'http_end']
    if not ends:
        return {'complete': False}
    return {'complete': True, 'answer': json.loads(parts[0]['text'])['answer']}
'''


TWO_ACTION_ADAPTER = '''
import json
def adapt(payload):
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'url': payload['state']['endpoint'],
            'method': 'POST', 'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'question': payload['question']}), 'submit': True}]}
    bodies = [event for event in payload['events'] if event['kind'] == 'http_body']
    ends = [event for event in payload['events'] if event['kind'] == 'http_end']
    if not bodies or not ends:
        return {'complete': False}
    return {'complete': True, 'answer': json.loads(bodies[-1]['text'])['answer']}
'''


def test_two_action_adapter_sends_and_receives_over_real_http_without_browser(
        tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter

    origin, received = chat_service
    adapter = IndependentAdapter(source=TWO_ACTION_ADAPTER, state={
        'endpoint': origin + '/chat',
        'cookies': [{'name': 'sid', 'value': 'recorded-login',
                     'domain': '127.0.0.1', 'path': '/'}],
    }, allowed_origins=[origin], workspace=tmp_path)
    try:
        assert adapter.run('first new question', 'trial-1')['answer'] == 'Exact reply: first new question'
        assert adapter.run('second new question', 'trial-2')['answer'] == 'Exact reply: second new question'
        assert received == ['first new question', 'second new question']
    finally:
        adapter.close()


def test_verified_trial_does_not_rearchive_events_into_full_demonstration_store(
        tmp_path, chat_service, monkeypatch):
    from lladar.independent_target import IndependentTarget
    from lladar.independent_adapter import IndependentAdapter

    endpoint, received = chat_service
    target = IndependentTarget(page_url=endpoint, workspace=tmp_path,
        confirmed=True, conversation_policy='demonstrated',
        output_fn=lambda _: None)
    target.state = {'endpoint': endpoint + '/chat', 'allowed_origins': [endpoint],
        'cookies': [{'name': 'sid', 'value': 'recorded-login',
                     'domain': '127.0.0.1', 'path': '/'}]}
    target.adapter = IndependentAdapter(source=TWO_ACTION_ADAPTER,
        state=target.state, allowed_origins=[endpoint],
        workspace=tmp_path / 'runtime')
    target.ledger.approve()
    monkeypatch.setattr(target, '_verify', lambda _turn: 'checked_reference')
    monkeypatch.setattr(target.store, 'append',
                        lambda _event: pytest.fail('trial must not fill demonstration recording'))
    try:
        turn = target._run('fresh question', 'probe-1', 'probe')
        assert turn['answer'] == 'Exact reply: fresh question'
        assert target.adapter.turn('probe-1')['verified'] is True
        assert received == ['fresh question']
    finally:
        target.close()


def test_two_action_adapter_can_replace_encoded_demonstration_question(
        tmp_path):
    from urllib.parse import parse_qs, quote_plus
    from lladar.independent_adapter import IndependentAdapter

    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length'])).decode()
            question = parse_qs(raw)['question'][0]
            received.append(question)
            body = json.dumps({'answer': 'Reply: ' + question}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}/chat'
    demonstration = 'old question [DEMO-abc]'
    source = '''
import json
def adapt(p):
    if p['phase'] == 'send':
        body = p['state']['selected_request']['body'].replace(
            p['demonstration_forms']['urlencoded_plus'],
            p['question_forms']['urlencoded_plus'])
        return {'actions': [{'kind': 'http', 'url': p['state']['endpoint'],
            'method': 'POST', 'body': body, 'submit': True}]}
    bodies = [e for e in p['events'] if e['kind'] == 'http_body']
    if not bodies:
        return {'complete': False}
    return {'complete': True, 'answer': json.loads(bodies[-1]['text'])['answer']}
'''
    adapter = IndependentAdapter(source=source, state={
        'endpoint': endpoint, 'demonstration_question': demonstration,
        'selected_request': {'body': 'question=' + quote_plus(demonstration)}},
        allowed_origins=[endpoint], workspace=tmp_path)
    try:
        assert adapter.run('請假的流程', 'first')['answer'] == 'Reply: 請假的流程'
        assert adapter.run('another fresh question', 'second')['answer'] == 'Reply: another fresh question'
        assert received == ['請假的流程', 'another fresh question']
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()


def test_adapter_sends_new_questions_using_saved_login_without_a_browser(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter
    origin, received = chat_service
    adapter = IndependentAdapter(source=HTTP_ADAPTER, state={
        'endpoint': origin + '/chat',
        'cookies': [{'name': 'sid', 'value': 'recorded-login', 'domain': '127.0.0.1', 'path': '/'}],
    }, allowed_origins=[origin], workspace=tmp_path)
    assert adapter.run('first new question', 'trial-1')['answer'] == 'Exact reply: first new question'
    assert adapter.run('second new question', 'trial-2')['answer'] == 'Exact reply: second new question'
    assert received == ['first new question', 'second new question']
    assert adapter.turn('trial-2')['status'] == 'completed'
    adapter.close()


def test_independent_adapter_rejects_demonstration_conversation_before_submission(tmp_path):
    from lladar.independent_adapter import IndependentAdapter, IndependentAdapterError

    endpoint = 'http://127.0.0.1:1'
    source = '''
import json
def adapt(payload):
    if payload['phase'] == 'prepare':
        return {'ready': True}
    if payload['phase'] == 'send':
        return {'actions': [{'kind': 'http', 'method': 'POST',
            'url': payload['state']['endpoint'] + '/v3/directline/conversations/demo-123/activities',
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'text': payload['question']}), 'submit': True}]}
    return {'complete': False}
'''
    adapter = IndependentAdapter(source=source, state={
        'endpoint': endpoint,
        'session_storage': [{'items': {'chatConversationId': 'demo-123'}}],
    }, allowed_origins=[endpoint], workspace=tmp_path, timeout=1)
    with pytest.raises(IndependentAdapterError, match='conversation_policy_violation'):
        adapter.run('a new question', 'trial-1')
    assert adapter.turn('trial-1')['status'] == 'not_submitted'
    assert adapter.turn('trial-1')['events'] == []
    adapter.close()


def test_binary_websocket_collects_correlated_tail_while_connection_stays_open(tmp_path):
    import base64
    from websockets.sync.server import serve
    from lladar.independent_adapter import IndependentAdapter
    def chat(socket):
        for wire in socket:
            value = json.loads(wire)
            for part in [{'id': 'background', 'text': 'wrong', 'done': True},
                         {'id': value['id'], 'text': '完整回答：', 'done': False},
                         {'id': value['id'], 'text': value['question'], 'done': True}]:
                socket.send(json.dumps(part, ensure_ascii=False).encode('utf-8'))
    server = serve(chat, '127.0.0.1', 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = 'ws://127.0.0.1:' + str(server.socket.getsockname()[1])
    source = '''
import json
import base64
def adapt(p):
    if p['phase'] == 'send':
        return {'actions':[{'kind':'ws_open','url':p['state']['endpoint']},
            {'kind':'ws_send','text':json.dumps({'id':p['request_id'],'question':p['question']}),'submit':True}]}
    answer = ''
    for e in p['events']:
        if e['kind'] != 'websocket_received':
            continue
        v=json.loads(base64.b64decode(e['binary_b64']).decode('utf-8'))
        if v['id'] != p['request_id']:
            continue
        answer += v['text']
        if v['done']:
            return {'complete':True,'answer':answer}
    return {'complete':False,'actions':[{'kind':'ws_receive'}]}
'''
    try:
        adapter = IndependentAdapter(source=source, state={'endpoint': endpoint},
            allowed_origins=[endpoint], workspace=tmp_path)
        assert adapter.run('新問題', 'binary-1')['answer'] == '完整回答：新問題'
        adapter.close()
    finally:
        server.shutdown()


def test_reloaded_adapter_recovers_submitted_answer_without_resending(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter, IndependentAdapterError
    origin, received = chat_service
    state = {'endpoint': origin + '/chat', 'cookies': [
        {'name': 'sid', 'value': 'recorded-login', 'domain': '127.0.0.1', 'path': '/'}]}
    incomplete = HTTP_ADAPTER.replace("if not ends:", "if payload['phase'] == 'receive':")
    adapter = IndependentAdapter(source=incomplete, state=state, allowed_origins=[origin], workspace=tmp_path)
    with pytest.raises(IndependentAdapterError, match='completion_unconfirmed'):
        adapter.run('submitted once', 'trial-recover')
    adapter.close()
    repaired = IndependentAdapter(source=HTTP_ADAPTER, state=state, allowed_origins=[origin], workspace=tmp_path)
    answer = repaired.recover('trial-recover')
    assert answer['answer'] == 'Exact reply: submitted once'
    assert received == ['submitted once']
    repaired.close()


def test_repaired_collector_reassembles_unverified_answer_from_original_material(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter
    endpoint, received=chat_service
    options=dict(state={'endpoint':endpoint+'/chat','cookies':[
        {'name':'sid','value':'recorded-login','domain':'127.0.0.1','path':'/'}]},allowed_origins=[endpoint],workspace=tmp_path)
    wrong=HTTP_ADAPTER.replace("json.loads(parts[0]['text'])['answer']", "'missing tail'")
    first=IndependentAdapter(source=wrong,**options)
    first.run('recover full text','unverified-turn')
    first.close()
    repaired=IndependentAdapter(source=HTTP_ADAPTER,**options)
    try:
        assert repaired.recover('unverified-turn')['answer']=='Exact reply: recover full text'
        assert received==['recover full text']
    finally:
        repaired.close()


def test_host_derives_evidence_ids_instead_of_trusting_generated_refs(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter
    endpoint, received = chat_service
    forged = HTTP_ADAPTER.replace(
        "json.loads(parts[0]['text'])['answer']}",
        "json.loads(parts[0]['text'])['answer'], 'source_refs': [events[0]['event_id']]}")
    adapter = IndependentAdapter(source=forged, state={'endpoint': endpoint + '/chat',
        'cookies': [{'name': 'sid', 'value': 'recorded-login', 'domain': '127.0.0.1', 'path': '/'}]},
        allowed_origins=[endpoint], workspace=tmp_path)
    turn = adapter.run('fresh question', 'forged')
    assert turn['answer'] == 'Exact reply: fresh question'
    assert turn['source_refs'] != [turn['events'][0]['event_id']]
    assert all(ref in {event['event_id'] for event in turn['events']}
               for ref in turn['source_refs'] + turn['completion_refs'])
    adapter.close()


def test_rejected_destination_does_not_mark_question_as_submitted(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter, IndependentAdapterError
    endpoint, received = chat_service
    adapter = IndependentAdapter(source=HTTP_ADAPTER, state={'endpoint': endpoint + '/chat'},
        allowed_origins=['http://unrelated.test'], workspace=tmp_path)
    with pytest.raises(IndependentAdapterError, match='adapter_origin_not_authorized'):
        adapter.run('must not send', 'not-sent')
    assert received == []
    assert adapter.turn('not-sent')['status'] == 'not_submitted'
    adapter.close()


def test_corrupted_saved_turns_are_rejected_before_reuse(tmp_path, chat_service):
    from lladar.independent_adapter import IndependentAdapter, IndependentAdapterError
    endpoint, _ = chat_service
    options = dict(source=HTTP_ADAPTER, state={'endpoint': endpoint + '/chat', 'cookies': [
        {'name': 'sid', 'value': 'recorded-login', 'domain': '127.0.0.1', 'path': '/'}]},
        allowed_origins=[endpoint], workspace=tmp_path)
    adapter=IndependentAdapter(**options)
    adapter.run('recorded question','saved-turn')
    adapter.close()
    saved=json.loads((tmp_path/'turns.json').read_text(encoding='utf-8'))
    saved['saved-turn']['answer']='invented replacement'
    (tmp_path/'turns.json').write_text(json.dumps(saved),encoding='utf-8')
    with pytest.raises(IndependentAdapterError,match='checkpoint_integrity_failed'):
        IndependentAdapter(**options)


def test_persistent_http_stream_finishes_on_application_marker_before_eof(tmp_path):
    from lladar.independent_adapter import IndependentAdapter
    release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for text in ['data: first part\n\n','data: last part\n\n','data: [DONE]\n\n']:
                raw=text.encode()
                self.wfile.write(raw)
                self.wfile.flush()
            release.wait(20)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}'
    source='''
import base64
def adapt(p):
    if p['phase']=='send':
        return {'actions':[{'kind':'http','url':p['state']['endpoint'],'method':'POST','body':p['question'],'submit':True}]}
    chunks=[e for e in p['events'] if e['kind']=='http_chunk']
    wire=''.join([base64.b64decode(e['binary_b64']).decode('utf-8') for e in chunks])
    if 'data: [DONE]\\n\\n' not in wire:
        return {'complete':False}
    return {'complete':True,'answer':'first part\\nlast part'}
'''
    adapter=IndependentAdapter(source=source,state={'endpoint':endpoint},allowed_origins=[endpoint],workspace=tmp_path,timeout=5)
    try:
        assert adapter.run('stream question','stream-1')['answer']=='first part\nlast part'
        assert not release.is_set()
    finally:
        adapter.close()
        release.set()
        server.shutdown()
        server.server_close()


def test_reloaded_adapter_uses_rotated_cookie_in_saved_state(tmp_path):
    from lladar.independent_adapter import IndependentAdapter
    accepted=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            wanted='sid=initial' if not accepted else 'sid=rotated'
            if wanted not in self.headers.get('Cookie',''):
                self.send_error(401)
                return
            question=json.loads(self.rfile.read(int(self.headers['Content-Length'])))['question']
            accepted.append(question)
            body=json.dumps({'answer':'Exact reply: '+question}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Set-Cookie','sid=rotated; Path=/')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}'
    options=dict(source=HTTP_ADAPTER,state={'endpoint':endpoint,'cookies':[
        {'name':'sid','value':'initial','domain':'127.0.0.1','path':'/'}]},allowed_origins=[endpoint],workspace=tmp_path)
    try:
        adapter=IndependentAdapter(**options)
        adapter.run('before rotation','rotation-1')
        adapter.close()
        restored=IndependentAdapter(**options)
        assert restored.run('after rotation','rotation-2')['answer']=='Exact reply: after rotation'
        restored.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.browser
def test_large_recorded_script_does_not_prevent_completed_demonstration(tmp_path, monkeypatch):
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.adapter_material_packet import build_adapter_material_packet

    monkeypatch.setenv('LLADAR_MAX_CAPTURE_BYTES', '550000')
    monkeypatch.setenv('LLADAR_MAX_RECORDING_BYTES', '550000')
    script = b"const authTokenHelper = 'recorded';\n" + b'/' + b'*' + b'x' * 350000 + b'*' + b'/'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = script if self.path == '/app.js' else (
                b'<textarea id="question"></textarea><button id="send">Send</button>'
                b'<div id="answer"></div><script src="/app.js"></script>'
                b'<script>send.onclick=async()=>{const r=await fetch("/chat",{method:"POST",'
                b'headers:{"Content-Type":"application/json"},'
                b'body:JSON.stringify({question:question.value})});'
                b'answer.textContent=(await r.json()).answer;}</script>'
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript' if self.path == '/app.js' else 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            question = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['question']
            body = json.dumps({'answer': 'Answer: ' + question}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    recorder = DemonstrationRecorder(page_url=endpoint, workspace=tmp_path, headless=True)
    question = 'Show the process [DEMO-large-script]'

    def human(_prompt):
        recorder.browser.page.locator('#question').fill(question)
        recorder.browser.page.locator('#send').click()
        recorder.browser.page.wait_for_function("document.querySelector('#answer').textContent.startsWith('Answer:')")
        return ''

    try:
        state = recorder.capture(question, human, lambda _message: None)
        metadata = json.loads((tmp_path / 'recording' / 'demonstration.json').read_text(encoding='utf-8'))
        packet = build_adapter_material_packet(recorder.store, state, question, metadata['submission_refs'])
        assert metadata['submission_refs']
        assert packet['client_code_regions']
        final_dom = recorder.store.query_recording(kind='demonstration_final_dom', limit=1)['events']
        assert len(final_dom) == 1
        structure = recorder.store.event(final_dom[0]['event_id'])
        assert any(node.get('attributes', {}).get('id') == 'answer'
                   for frame in structure['frames'] for node in frame['nodes'])
        assert recorder.closed
        (tmp_path / 'recording' / 'demonstration.json').unlink()
        assert recorder.recover_completed_capture(state) == metadata
    finally:
        recorder.close()
        server.shutdown()
        server.server_close()


@pytest.mark.browser
def test_one_demonstration_closes_browser_before_agent_and_formal_questions(tmp_path, chat_service, monkeypatch):
    monkeypatch.setenv('LLADAR_BROWSER_DISCOVERY_REPAIRS', '2')
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.independent_target import IndependentTarget
    origin, received = chat_service
    recorder = DemonstrationRecorder(page_url=origin, workspace=tmp_path, headless=True)
    def human(prompt):
        if 'YES' in prompt:
            return 'YES'
        recorder.browser.page.locator('#question').fill(recorder.question)
        recorder.browser.page.locator('#send').click()
        recorder.browser.page.wait_for_function("document.querySelector('#answer').textContent.startsWith('Exact reply:')")
        return ''
    model = http_evidence_agent(recorder, incomplete_first=True)
    target = IndependentTarget(page_url=origin, workspace=tmp_path, recorder=recorder,
        agent_factory=model, input_fn=human, output_fn=lambda _: None,
        reference_reader=lambda turn: 'Exact reply: ' + turn['question'])
    target.prepare(interactive=True, request_count=2)
    assert target.evidence['browser_closed'] is True
    assert len(target.evidence['verification']) == 2
    assert target.answer('formal first', 'formal-1') == 'Exact reply: formal first'
    assert target.answer('formal second', 'formal-2') == 'Exact reply: formal second'
    assert len(received) == 7  # demonstration, rejected probe, accepted probe, two held-out, two formal
    target.close()


@pytest.mark.browser
def test_cli_writes_formal_answers_after_one_demonstration(tmp_path, chat_service):
    from lladar.cli import main
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.independent_target import IndependentTarget
    from fixture_extraction import browser_cli_terminal
    endpoint, _ = chat_service
    dataset = tmp_path / 'dataset.jsonl'
    output = tmp_path / 'responses.jsonl'
    dataset.write_text(json.dumps({'question':'formal CLI question', 'expected_answer':'evaluation only',
                                  'actual_response':None}) + '\n', encoding='utf-8')
    recorder = DemonstrationRecorder(page_url=endpoint, workspace=tmp_path / 'website', headless=True)
    def human(prompt):
        if 'YES' in prompt:
            return 'YES'
        recorder.browser.page.locator('#question').fill(recorder.question)
        recorder.browser.page.locator('#send').click()
        recorder.browser.page.wait_for_function("document.querySelector('#answer').textContent.startsWith('Exact reply:')")
        return ''
    model = http_evidence_agent(recorder)
    def target_factory(**options):
        return IndependentTarget(**options, workspace=tmp_path / 'website', recorder=recorder,
            input_fn=human, agent_factory=model, output_fn=lambda _:None,
            reference_reader=lambda turn:'Exact reply: '+turn['question'])
    with browser_cli_terminal():
        code=main(['run-agent',str(dataset),'--page-url',endpoint,'--demo-question','Show capabilities',
                   '--output',str(output),'--no-verbose'],browser_target_factory=target_factory)
    assert code==0
    assert json.loads(output.read_text(encoding='utf-8'))['actual_response']=='Exact reply: formal CLI question'


@pytest.mark.browser
def test_same_agent_repairs_formal_collection_without_sending_question_twice(tmp_path, chat_service):
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.independent_target import IndependentTarget
    endpoint, received = chat_service
    recorder = DemonstrationRecorder(page_url=endpoint, workspace=tmp_path, headless=True)
    attempts = []
    def human(prompt):
        if 'YES' in prompt:
            return 'YES'
        recorder.browser.page.locator('#question').fill(recorder.question)
        recorder.browser.page.locator('#send').click()
        recorder.browser.page.wait_for_function("document.querySelector('#answer').textContent.startsWith('Exact reply:')")
        return ''
    model = http_evidence_agent(recorder, repair_collection=True, attempts=attempts)
    target=IndependentTarget(page_url=endpoint,workspace=tmp_path,recorder=recorder,
        input_fn=human,output_fn=lambda _:None,agent_factory=model,
        reference_reader=lambda turn:'Exact reply: '+turn['question'])
    target.prepare(interactive=True,request_count=1)
    assert target.answer('changed schema','repair-formal')=='Exact reply: changed schema'
    assert received.count('changed schema')==1
    assert len(attempts)==2
    target.close()


@pytest.mark.browser
def test_reused_adapter_refreshes_expired_login_then_closes_browser(tmp_path, chat_service):
    from lladar.independent_target import IndependentTarget
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.work_session import atomic_json, digest
    endpoint, received = chat_service
    state={'page_url':endpoint,'allowed_origins':[endpoint], 'cookies':[
        {'name':'sid','value':'expired','domain':'127.0.0.1','path':'/'}]}
    manifest={'schema_version':1,'mode':'independent','page_url':endpoint,'dependencies':[],
        'source':HTTP_ADAPTER.replace("payload['state']['endpoint']",repr(endpoint+'/chat')),'basis':'Previously verified fixture'}
    manifest['sha256']=digest(manifest)
    path=tmp_path/'saved'/'manifest.json'
    atomic_json(path,manifest)
    atomic_json(path.parent/'state.json',state)
    atomic_json(path.parent/'state.integrity.json',{'sha256':digest(state)})
    prompts=[]
    def human(prompt):
        prompts.append(prompt)
        return ''
    target=IndependentTarget(page_url=endpoint,workspace=tmp_path/'reuse',adapter_path=path,
        recorder=DemonstrationRecorder(page_url=endpoint,workspace=tmp_path/'reuse',headless=True),
        confirmed=True,input_fn=human,output_fn=lambda _:None,
        agent_factory=lambda **_:pytest.fail('A valid reused adapter should not need generation'),
        reference_reader=lambda turn:'Exact reply: '+turn['question'])
    try:
        target.prepare(interactive=True,request_count=1)
        assert prompts and 'Sign in again' in prompts[0]
        assert target.recorder.browser.context is None
        assert target.answer('after login refresh','login-formal')=='Exact reply: after login refresh'
        assert received.count('after login refresh')==1
    finally:
        target.close()


@pytest.mark.browser
@pytest.mark.live_gemini
@pytest.mark.skipif(os.environ.get('LLADAR_LIVE_INDEPENDENT') != '1', reason='explicit real-model opt-in')
def test_real_agent_generates_adapter_for_randomized_service_after_browser_shutdown(tmp_path):
    from lladar.demonstration_recorder import DemonstrationRecorder
    from lladar.independent_target import IndependentTarget
    from lladar.answer_extraction import ExtractionOptions
    route, field, answer_field = ['x' + secrets.token_hex(6) for _ in range(3)]
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f'''<textarea id="q"></textarea><button id="s">Send</button><pre id="a"></pre>
            <script>s.onclick=async()=>{{a.textContent='';const r=await fetch('/{route}',{{method:'POST',
            headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{{field}:q.value}})}});
            a.textContent=(await r.json()).{answer_field};}};</script>'''.encode()
            self.send_response(200)
            self.send_header('Set-Cookie','sid=randomized-session; Path=/; HttpOnly')
            self.send_header('Content-Type','text/html')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if self.path != '/' + route or 'sid=randomized-session' not in self.headers.get('Cookie',''):
                self.send_error(401)
                return
            question=json.loads(self.rfile.read(int(self.headers['Content-Length'])))[field]
            received.append(question)
            body=json.dumps({answer_field:'完整原文：'+question+'\n最後一段。'},ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}'
    recorder=DemonstrationRecorder(page_url=endpoint,workspace=tmp_path,headless=True)
    def human(prompt):
        if 'YES' in prompt:
            return 'YES'
        recorder.browser.page.locator('#q').fill(recorder.question)
        recorder.browser.page.locator('#s').click()
        recorder.browser.page.wait_for_function("document.querySelector('#a').textContent.endsWith('最後一段。')")
        return ''
    target=IndependentTarget(page_url=endpoint,workspace=tmp_path,recorder=recorder,
        input_fn=human,confirmed=True,timeout=60,
        extraction_options=ExtractionOptions(model_destination='gemini:gemini-3.8-flash',max_output_tokens=8192),
        reference_reader=lambda turn:'完整原文：'+turn['question']+'\n最後一段。')
    try:
        target.prepare(interactive=True,request_count=5)
        assert recorder.closed
        for index in range(5):
            question=f'正式問題 {index+1}'
            assert target.answer(question,f'real-formal-{index+1}')=='完整原文：'+question+'\n最後一段。'
        target.close()
        restored=IndependentTarget(page_url=endpoint,workspace=tmp_path/'reloaded',confirmed=True,
            adapter_path=tmp_path/'adapter'/'manifest.json',
            extraction_options=ExtractionOptions(model_destination='gemini:gemini-3.8-flash',max_output_tokens=8192),
            reference_reader=lambda turn:'完整原文：'+turn['question']+'\n最後一段。')
        try:
            restored.prepare(interactive=True,request_count=1)
            assert restored.recorder.browser.context is None
            assert restored.answer('重載後的新問題','reloaded-formal')=='完整原文：重載後的新問題\n最後一段。'
        finally:
            restored.close()
        assert len(received)>=13
    finally:
        target.close()
        server.shutdown()
        server.server_close()


def http_evidence_agent(recorder, *, incomplete_first=False, repair_collection=False, attempts=None):
    """Fixture model follows separate material/code calls; the host runs trials."""
    code_calls = []
    def model(**options):
        def execute(prompt):
            prompt = options['system_prompt']
            assert recorder.closed
            tools = options['tools']
            if 'select_material' in tools:
                catalog = json.loads(prompt.split('Indexed recording: ', 1)[1].split('\nPreviously selected: ', 1)[0])
                refs = [item['event_id'] for item in catalog if item['kind'] in {'http_request', 'http_body'}]
                assert json.loads(tools['select_material'](refs, 'Observed request and complete reply'))['status'] == 'selected'
                return
            packet = json.loads(prompt.split('Selected evidence packet: ', 1)[1].split('\nLast trial: ', 1)[0])
            requests = [json.loads(item['preview']['text']) for item in packet
                        if item['kind'] == 'http_request' and item['preview']['complete']]
            request = next(item for item in requests if item.get('method') == 'POST')
            source = HTTP_ADAPTER.replace("payload['state']['endpoint']", repr(request['url']))
            code_calls.append(True)
            if attempts is not None:
                attempts.append('code role')
            if incomplete_first and len(code_calls) == 1:
                source = source.replace("if not ends:", "if payload['phase'] == 'receive':")
            if repair_collection and len(code_calls) > 1:
                source = source.replace("json.loads(parts[0]['text'])['answer']", "json.loads(parts[0]['text']).get('final_answer') or json.loads(parts[0]['text'])['answer']")
            tools['submit_adapter'](source, 'Selected HTTP request and response evidence')
        return execute
    return model
