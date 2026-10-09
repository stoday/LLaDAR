"""DOM fallback behavior through BrowserTarget with a real public webpage."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import json
import time

import pytest

from lladar.browser_target import BrowserTarget, browser_failure_diagnostic
from lladar.answer_extraction import ExtractionError
from lladar.playwright_driver import PlaywrightBrowserDriver
from fixture_extraction import fixture_options, FixtureTerminal

pytestmark = pytest.mark.browser


@contextmanager
def dom_site(mode="normal"):
    from websockets.sync.server import serve
    socket_server = None
    socket_thread = None
    if mode in {"socket", "binary-socket", "oversized-socket"}:
        def chat(connection):
            for data in connection:
                answer='Answer: ' + json.loads(data)['question']
                connection.send(b'x'*(1024*1024+1) if mode=='oversized-socket' else answer.encode() if mode=='binary-socket' else answer)
        socket_server = serve(chat, '127.0.0.1', 0)
        socket_thread = threading.Thread(target=socket_server.serve_forever, daemon=True)
        socket_thread.start()
    socket_url = f'ws://127.0.0.1:{socket_server.socket.getsockname()[1]}/' if socket_server else ''
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            if self.path=='/rotate':
                body=b'rotated-session-secret'
                self.send_response(200);self.send_header('Set-Cookie','session=rotated-session-secret; Path=/; HttpOnly')
                self.send_header('Content-Type','text/plain');self.end_headers();self.wfile.write(body);return
            if self.path=='/framed':
                body=b'<html><body><iframe title="Chat" name="chat" src="/"></iframe></body></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body);return
            body=("""<!doctype html><html><body>
              <form><label>Question<textarea placeholder="Question"></textarea></label><button type="submit">Send</button></form>
              <section role="log" aria-label="Conversation" aria-busy="false" data-turn-id="0"></section>
              <button type="button" onclick="document.body.append(document.querySelector('[role=log]').cloneNode(true))">Duplicate conversation</button>
              <script>
              const form=document.querySelector('form'), field=document.querySelector('textarea'), log=document.querySelector('[role=log]');
              let socket;
              if(MODE==='socket' || MODE==='binary-socket' || MODE==='oversized-socket') socket=new WebSocket(SOCKET_URL);
              form.onsubmit = async e => {
                e.preventDefault(); const q=field.value;
                if(MODE==='replace') {log.replaceChildren();log.dataset.turnId=String(Number(log.dataset.turnId)+1);}
                if(MODE==='replace-no-cursor') {const hadMessages=log.childElementCount>0;log.replaceChildren();if(hadMessages) log.removeAttribute('data-turn-id');}
                if(MODE!=='no-busy') log.setAttribute('aria-busy','true');
                const user=document.createElement('article'); user.dataset.role='user';user.textContent=q;log.append(user);
                const reply=document.createElement('article');reply.dataset.role='assistant';reply.textContent='Partial';if(MODE==='hidden') reply.hidden=true;log.append(reply);
                if(MODE==='socket' || MODE==='binary-socket' || MODE==='oversized-socket') {
                  socket.onmessage=async e=>{reply.textContent=MODE==='oversized-socket' ? 'Answer: '+q : typeof e.data==='string' ? e.data : await e.data.text();log.setAttribute('aria-busy','false');};
                  socket.send(JSON.stringify({question:q}));return;
                }
                if(MODE==='rotate' && q==='First') {const response=await fetch('/rotate');reply.textContent=await response.text();log.setAttribute('aria-busy','false');return;}
                if(MODE==='http-custom' || MODE==='auth' || MODE==='http-sse' || MODE==='http-text' || MODE==='http-secret') {
                  const response=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(MODE==='http-secret' ? {question:q,token:'fixture-request-secret'} : {question:q})});
                  if(MODE==='http-sse') {const part=await response.body.getReader().read();reply.textContent=new TextDecoder().decode(part.value).trim().replace('data: ','');}
                  else reply.textContent=await response.text();
                  log.setAttribute('aria-busy','false');return;
                }
                setTimeout(() => {reply.textContent=MODE==='echo' ? 'fixture-session-secret' : 'Answer: '+q;if(MODE!=='stuck' && !(MODE==='first-stuck' && q==='First')) log.setAttribute('aria-busy','false');if(MODE==='loss' && q==='First') document.querySelector('button').remove();},60);
              };
              </script></body></html>""").replace("MODE",json.dumps(mode)).replace("SOCKET_URL",json.dumps(socket_url)).encode()
            self.send_response(200);self.send_header('Set-Cookie','session=fixture-session-secret; Path=/; HttpOnly');self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            body=(data['token'] if mode=='http-secret' else 'Answer: '+data['question']).encode()
            if mode=='http-sse':
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: '+body+b'\n\n');self.wfile.flush();time.sleep(4);return
            self.send_response(401 if mode=='auth' else 200)
            self.send_header('Content-Type','text/plain' if mode=='http-text' else 'application/x-custom-stream')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try: yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown();server.server_close();worker.join(3)
        if socket_server:
            socket_server.shutdown();socket_server.socket.close();socket_thread.join(3)


def target_for(driver,url):
    def respond(prompt):
        if prompt.startswith('Press Enter'):
            driver.page.get_by_label('Question',exact=True).fill('Calibration')
            driver.page.get_by_role('button',name='Send',exact=True).click()
            driver.page.get_by_text('Answer: Calibration',exact=True).wait_for()
            return ''
        return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
    return BrowserTarget(page_url=url,timeout=2,driver=driver,calibration_question='Calibration',verbose=False,
                         extraction_options=fixture_options(),input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())


def test_accessible_chat_selects_dom_and_submits_each_question_once(tmp_path):
    with dom_site() as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='dom'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_missing_submit_binding_stops_before_dispatch_and_blocks_later_questions(tmp_path):
    with dom_site('loss') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First','Second'],interactive=True,request_count=2)
            assert target.answer('First','first')=='Answer: First'
            with pytest.raises(ExtractionError,match='dom_binding_lost') as raised:
                target.answer('Second','second')
            diagnostic=browser_failure_diagnostic(raised.value,target.evidence)
            assert diagnostic['request_attempted'] is False
            with pytest.raises(ExtractionError,match='extraction_blocked'):
                target.answer('Third','third')
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_dom_cookie_echo_is_refused_before_model_transfer(tmp_path):
    with dom_site('echo') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        def respond(prompt):
            if prompt.startswith('Press Enter'):
                driver.page.get_by_label('Question',exact=True).fill('Calibration')
                driver.page.get_by_role('button',name='Send',exact=True).click()
                driver.page.get_by_text('fixture-session-secret',exact=True).wait_for()
                return ''
            return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
        target=BrowserTarget(page_url=url,timeout=2,driver=driver,calibration_question='Calibration',verbose=False,
                             extraction_options=fixture_options(),input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        try:
            with pytest.raises(ExtractionError,match='sensitive_response'):
                target.prepare(['First'],interactive=True,request_count=1)
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
        finally: target.close()


def test_replaced_answer_area_handles_identical_questions_as_new_turns(tmp_path):
    with dom_site('replace') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['Calibration'],interactive=True,request_count=1)
            assert target.answer('Calibration','new-turn')=='Answer: Calibration'
            assert driver.page.get_by_role('log').get_attribute('data-turn-id')=='3'
            assert target.evidence['extraction']['calls']==3
        finally: target.close()


def test_unsupported_http_response_uses_same_calibration_dom_without_an_extra_submission(tmp_path):
    with dom_site('http-custom') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='dom'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_persistent_text_websocket_uses_dom_completion_and_keeps_one_browser_session(tmp_path):
    with dom_site('socket') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            driver.page.wait_for_function('socket.readyState === WebSocket.OPEN')
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='dom'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
            assert driver.page.evaluate('socket.readyState')==1
        finally: target.close()


def test_ambiguous_answer_scope_stops_before_sending_another_question(tmp_path):
    with dom_site('duplicate-log') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First','Second'],interactive=True,request_count=2)
            assert target.answer('First','first')=='Answer: First'
            driver.page.get_by_role('button',name='Duplicate conversation',exact=True).click()
            with pytest.raises(ExtractionError,match='dom_binding_lost') as raised:
                target.answer('Second','second')
            assert browser_failure_diagnostic(raised.value,target.evidence)['request_attempted'] is False
            assert driver.page.locator('[role=log]').first.locator('[data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_persistent_sse_selects_dom_before_verification_instead_of_timing_out_on_replay(tmp_path):
    with dom_site('http-sse') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='dom'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


@pytest.mark.parametrize('mode',['no-busy','stuck'])
def test_text_becoming_stable_is_insufficient_calibration_completion(tmp_path,mode):
    with dom_site(mode) as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=.3,headless=True)
        target=target_for(driver,url)
        try:
            with pytest.raises(TimeoutError,match='Calibration response'):
                target.prepare(['First'],interactive=True,request_count=1)
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
            assert 'extraction' not in target.evidence
        finally: target.close()


def test_authentication_failure_is_not_hidden_by_a_completed_dom_answer(tmp_path):
    from lladar.browser_target import BrowserAuthenticationRequired
    with dom_site('auth') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            with pytest.raises(BrowserAuthenticationRequired):
                target.prepare(['First'],interactive=True,request_count=1)
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
        finally: target.close()


def test_dom_cli_keeps_completed_response_and_marks_unsent_trials_after_binding_loss(tmp_path):
    from pathlib import Path
    from lladar.cli import main
    from lladar.records import read_records
    from fixture_extraction import FixtureProvider,browser_cli_terminal
    with dom_site('loss') as url:
        dataset=tmp_path/'dataset.jsonl';output=tmp_path/'responses.jsonl'
        dataset.write_text(json.dumps({'question':'First','expected_answer':'expected','actual_response':None})+'\n',encoding='utf-8')
        submitted=[]
        def factory(**options):
            driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
            def respond(prompt):
                if prompt.startswith('Press Enter'):
                    driver.page.get_by_label('Question',exact=True).fill('Calibration')
                    driver.page.get_by_role('button',name='Send',exact=True).click()
                    driver.page.get_by_text('Answer: Calibration',exact=True).wait_for()
                    return ''
                return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
            class InspectTarget(BrowserTarget):
                def close(self):
                    submitted.extend(driver.page.locator('[role=log] [data-role=user]').all_text_contents())
                    super().close()
            return InspectTarget(**options,driver=driver,input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        with browser_cli_terminal():
            result=main(['run-agent',str(dataset),'--page-url',url,'--output',str(output),'--calibration-question','Calibration',
                         '--no-verbose','--skill','src/lladar/skill_assets/run-agent-stability'],
                        runs_root=tmp_path/'runs',browser_target_factory=factory,extraction_provider_factory=FixtureProvider)
        assert result==0
        assert read_records(output)[0]['actual_response']=='Answer: First'
        trials=[json.loads(line) for line in Path(str(output)+'.trials.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [t['status'] for t in trials]==['ok','execution_error','execution_error']
        assert trials[1]['diagnostic']['reason']=='dom_binding_lost'
        assert trials[1]['diagnostic']['request_attempted'] is False
        assert trials[2]['diagnostic']['request_attempted'] is False
        run=json.loads(Path(str(output)+'.run.json').read_text(encoding='utf-8'))
        assert run['target']['browser_route']=='dom'
        assert run['target']['extraction']['calls']==3
        assert submitted==['Calibration','Calibration','First']


def test_hidden_answer_is_not_accepted_as_visible_dom_material(tmp_path):
    with dom_site('hidden') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=.3,headless=True)
        def respond(prompt):
            if prompt.startswith('Press Enter'):
                driver.page.get_by_label('Question',exact=True).fill('Calibration')
                driver.page.get_by_role('button',name='Send',exact=True).click()
                driver.page.locator('[data-role=assistant]').wait_for(state='attached')
                return ''
            return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
        target=BrowserTarget(page_url=url,timeout=.3,driver=driver,calibration_question='Calibration',verbose=False,
                             extraction_options=fixture_options(),input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        try:
            with pytest.raises(ExtractionError,match='dom_binding_lost'):
                target.prepare(['First'],interactive=True,request_count=1)
            assert 'extraction' not in target.evidence
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
        finally: target.close()


def test_same_origin_named_iframe_is_calibrated_and_rebound_for_dom_answers(tmp_path):
    with dom_site() as origin:
        url=origin+'framed'
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        def respond(prompt):
            if prompt.startswith('Press Enter'):
                frame=driver.page.frame_locator('iframe[title="Chat"]')
                frame.get_by_label('Question',exact=True).fill('Calibration')
                frame.get_by_role('button',name='Send',exact=True).click()
                frame.get_by_text('Answer: Calibration',exact=True).wait_for()
                return ''
            return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
        target=BrowserTarget(page_url=url,timeout=2,driver=driver,calibration_question='Calibration',verbose=False,
                             extraction_options=fixture_options(),input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.frame_locator('iframe[title="Chat"]').locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
            assert target.evidence['browser_route']=='dom'
        finally: target.close()


def test_binary_websocket_response_can_use_dom_without_decoding_its_protocol(tmp_path):
    with dom_site('binary-socket') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            driver.page.wait_for_function('socket.readyState === WebSocket.OPEN')
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='dom'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_supported_http_route_wins_even_when_dom_completion_is_available(tmp_path):
    with dom_site('http-text') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            assert target.evidence['browser_route']=='transport'
            assert target.answer('First','r')=='Answer: First'
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
        finally: target.close()


def test_formal_dom_timeout_never_extracts_partial_text_or_resubmits_the_question(tmp_path):
    with dom_site('first-stuck') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=.5,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First','Second'],interactive=True,request_count=2)
            with pytest.raises(ExtractionError,match='dom_completion_unconfirmed') as raised:
                target.answer('First','first')
            assert browser_failure_diagnostic(raised.value,target.evidence)['request_attempted'] is True
            with pytest.raises(ExtractionError,match='extraction_blocked'):
                target.answer('Second','second')
            assert target.evidence['extraction']['calls']==2
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()


def test_dom_fallback_retains_known_request_body_secrets_for_echo_screening(tmp_path):
    with dom_site('http-secret') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        def respond(prompt):
            if prompt.startswith('Press Enter'):
                driver.page.get_by_label('Question',exact=True).fill('Calibration')
                driver.page.get_by_role('button',name='Send',exact=True).click()
                driver.page.get_by_text('fixture-request-secret',exact=True).wait_for()
                return ''
            return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
        target=BrowserTarget(page_url=url,timeout=2,driver=driver,calibration_question='Calibration',verbose=False,
                             extraction_options=fixture_options(),input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        try:
            with pytest.raises(ExtractionError,match='sensitive_response'):
                target.prepare(['First'],interactive=True,request_count=1)
            assert 'extraction' not in target.evidence
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration']
        finally: target.close()


def test_oversized_binary_transport_is_not_hidden_by_small_dom_answer(tmp_path):
    from lladar.browser_target import BrowserResponseLimitExceeded
    with dom_site('oversized-socket') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            driver.page.wait_for_function('socket.readyState === WebSocket.OPEN')
            with pytest.raises(BrowserResponseLimitExceeded):
                target.prepare(['First'],interactive=True,request_count=1)
            assert 'extraction' not in target.evidence
        finally: target.close()


def test_replaced_identical_answer_without_new_turn_cursor_cannot_prove_verification(tmp_path):
    with dom_site('replace-no-cursor') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=.5,headless=True)
        target=target_for(driver,url)
        try:
            with pytest.raises(ExtractionError,match='dom_(question_ambiguous|completion_unconfirmed)'):
                target.prepare(['Calibration'],interactive=True,request_count=1)
            assert target.evidence['extraction']['calls']==1
        finally: target.close()


def test_cookie_rotated_after_calibration_is_still_screened_before_dom_model_transfer(tmp_path):
    with dom_site('rotate') as url:
        driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
        target=target_for(driver,url)
        try:
            target.prepare(['First'],interactive=True,request_count=1)
            with pytest.raises(ExtractionError,match='sensitive_response'):
                target.answer('First','r')
            assert target.evidence['extraction']['calls']==2
            assert driver.page.locator('[role=log] [data-role=user]').all_text_contents()==['Calibration','Calibration','First']
        finally: target.close()
