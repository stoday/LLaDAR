"""Real Streamlit transport boundary, not a replacement HTTP/WS mock."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

pytest.importorskip("streamlit", reason="Install the streamlit-test extra for this real-framework test")

from lladar.playwright_driver import PlaywrightBrowserDriver

pytestmark = pytest.mark.browser
APP = Path(__file__).resolve().parents[1] / "example_project" / "streamlit_qa" / "app.py"


@contextmanager
def streamlit_site(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "streamlit.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(APP),
             "--server.address", "127.0.0.1", "--server.port", str(port),
             "--server.headless", "true", "--browser.gatherUsageStats", "false", "--server.fileWatcherType", "none"],
            cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Streamlit exited before the local server became ready")
                try:
                    with urlopen(url + "/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    pass
                time.sleep(.1)
            else:
                pytest.fail("Streamlit did not become ready within 45 seconds")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_real_streamlit_chat_works_but_page_url_cannot_capture_binary_session(tmp_path):
    with streamlit_site(tmp_path) as url:
        driver = PlaywrightBrowserDriver(page_url=url, profile_dir=tmp_path / "profile", timeout=1, headless=True)
        traffic = {"sent_binary": 0, "received_binary": 0, "closed": 0, "streamlit_sockets": 0}
        def observe_socket(ws):
            if "/_stcore/stream" not in ws.url:
                return
            traffic["streamlit_sockets"] += 1
            def sent(payload):
                traffic["sent_binary"] += int(isinstance(payload, bytes))
            def received(payload):
                traffic["received_binary"] += int(isinstance(payload, bytes))
            ws.on("framesent", sent)
            ws.on("framereceived", received)
            ws.on("close", lambda _: traffic.update(closed=traffic["closed"] + 1))
        try:
            driver.page.on("websocket", observe_socket)
            driver.page.reload(wait_until="domcontentloaded", timeout=30_000)
            input_box = driver.page.get_by_placeholder("請輸入問題")
            input_box.wait_for(timeout=30_000)
            input_box.fill("你好")
            input_box.press("Enter")
            driver.page.get_by_text("Answer: 你好", exact=True).wait_for(timeout=30_000)
            sent_before = traffic["sent_binary"]
            received_before = traffic["received_binary"]
            closed_before = traffic["closed"]
            marker = 'LLaDAR Streamlit 校正 "中文"'
            def submit(question):
                input_box.fill(question)
                input_box.press("Enter")
                driver.page.get_by_text("Answer: " + question, exact=True).wait_for(timeout=30_000)
                return True
            # A working UI is insufficient: protobuf frames on a persistent
            # session cannot become a completed text question/answer exchange.
            with pytest.raises(TimeoutError, match="Calibration response"):
                driver.capture_calibration(marker, submit)
            assert traffic["streamlit_sockets"] >= 1
            assert traffic["sent_binary"] > sent_before
            assert traffic["received_binary"] > received_before
            assert traffic["closed"] == closed_before
            driver.page.get_by_text("Answer: 你好", exact=True).wait_for(timeout=1000)
        finally:
            driver.close()


def test_streamlit_target_selects_dom_and_answers_two_questions_without_resubmitting_calibration(tmp_path):
    from lladar.browser_target import BrowserTarget
    from fixture_extraction import fixture_options, FixtureTerminal
    with streamlit_site(tmp_path) as url:
        driver = PlaywrightBrowserDriver(page_url=url, profile_dir=tmp_path/"profile", timeout=2, headless=True)
        def respond(prompt):
            if prompt.startswith("Press Enter"):
                field = driver.page.get_by_placeholder("請輸入問題")
                field.wait_for(timeout=30_000)
                field.fill("Calibration")
                field.press("Enter")
                driver.page.get_by_text("Answer: Calibration",exact=True).wait_for(timeout=30_000)
                return ""
            if prompt.startswith("Type MATCH"):
                return "MATCH"
            return "YES"
        target = BrowserTarget(page_url=url,timeout=2,driver=driver,calibration_question="Calibration",
                               input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal(),
                               extraction_options=fixture_options(),verbose=False)
        try:
            target.prepare(["First","Second"],interactive=True,request_count=2)
            assert target.evidence["browser_route"] == "dom"
            assert target.answer("First","first") == "Answer: First"
            assert target.answer("Second","second") == "Answer: Second"
            # UI is the public target interface: calibration + verification + two trials.
            assert driver.page.get_by_test_id("stChatMessageAvatarUser").count() == 4
            assert driver.page.get_by_text("Answer: Calibration",exact=True).count() == 2
            assert target.evidence["extraction"]["calls"] == 4
        finally:
            target.close()


def test_streamlit_cli_writes_responses_and_safe_dom_route_evidence(tmp_path):
    import json
    from lladar.browser_target import BrowserTarget
    from lladar.cli import main
    from lladar.records import read_records
    from fixture_extraction import FixtureProvider, FixtureTerminal, browser_cli_terminal
    with streamlit_site(tmp_path) as url:
        dataset=tmp_path/'dataset.jsonl'
        output=tmp_path/'responses.jsonl'
        dataset.write_text('\n'.join(json.dumps({'question':q,'expected_answer':'private expected answer','actual_response':None}) for q in ['First','Second'])+'\n',encoding='utf-8')
        def factory(**options):
            driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=2,headless=True)
            def respond(prompt):
                if prompt.startswith('Press Enter'):
                    field=driver.page.get_by_placeholder('請輸入問題')
                    field.wait_for(timeout=30_000);field.fill('Calibration');field.press('Enter')
                    driver.page.get_by_text('Answer: Calibration',exact=True).wait_for(timeout=30_000)
                    return ''
                return 'MATCH' if prompt.startswith('Type MATCH') else 'YES'
            return BrowserTarget(**options,driver=driver,input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        with browser_cli_terminal():
            assert main(['run-agent',str(dataset),'--page-url',url,'--output',str(output),'--calibration-question','Calibration',
                         '--no-verbose','--skill','src/lladar/skill_assets/run-agent-random-sample'],
                        runs_root=tmp_path/'runs',browser_target_factory=factory,extraction_provider_factory=FixtureProvider)==0
        assert {r['question']:r['actual_response'] for r in read_records(output)}=={'First':'Answer: First','Second':'Answer: Second'}
        run=json.loads(Path(str(output)+'.run.json').read_text(encoding='utf-8'))
        assert run['target']['browser_route']=='dom'
        assert run['target']['dom_binding_version']==1
        assert run['target']['completion_observation']=='busy_to_idle'
        assert run['target']['extraction']['calls']==4
        assert all(json.loads(line)['status']=='ok' for line in Path(str(output)+'.trials.jsonl').read_text(encoding='utf-8').splitlines())
        assert '請輸入問題' not in Path(str(output)+'.run.json').read_text(encoding='utf-8')


@pytest.mark.live_gemini
@pytest.mark.skipif(os.environ.get("LLADAR_LIVE_GEMINI") != "1", reason="Explicit LLADAR_LIVE_GEMINI=1 required for paid live API calls")
def test_streamlit_cli_with_real_gemini_preserves_unicode_answers_and_call_budget(tmp_path):
    import json
    from dataclasses import replace
    from lladar.browser_target import BrowserTarget
    from lladar.cli import main
    from lladar.records import read_records
    from lladar.response_capture import ObservedAnswer
    from lladar.extraction_provider import GeminiExtractionProvider
    from lladar.answer_extraction import DEFAULT_EXTRACTION_MODEL
    from fixture_extraction import FixtureTerminal, browser_cli_terminal

    model=os.environ.get('LLADAR_LIVE_MODEL',DEFAULT_EXTRACTION_MODEL)
    requests=[]
    questions=['今天可以吃什麼？','不要加糖，份量是 250 克。']
    class LiveProvider(GeminiExtractionProvider):
        def __init__(self,**options):
            options['timeout_seconds']=180
            options['env_file']=Path(os.environ.get('LLADAR_LIVE_ENV_FILE',str(APP.parents[2]/'.env'))).resolve()
            super().__init__(**options)
        def extract(self,request,**options):
            requests.append(dict(request))
            return super().extract(request,**options)
    with streamlit_site(tmp_path) as url:
        dataset=tmp_path/'dataset.jsonl';output=tmp_path/'responses.jsonl'
        dataset.write_text('\n'.join(json.dumps({'question':q,'expected_answer':'PRIVATE_EXPECTED_SENTINEL','actual_response':None},ensure_ascii=False) for q in questions)+'\n',encoding='utf-8')
        def factory(**options):
            driver=PlaywrightBrowserDriver(page_url=url,profile_dir=tmp_path/'profile',timeout=60,headless=True)
            def reference(_response,request_id):
                messages=driver.page.get_by_test_id('stChatMessage').filter(has=driver.page.get_by_test_id('stChatMessageAvatarAssistant'))
                text='\n'.join(messages.last.get_by_test_id('stMarkdownContainer').all_inner_texts())
                assert text=='Answer: Calibration'
                return ObservedAnswer(request_id,text,True)
            extraction_options=replace(options.pop('extraction_options'),reference_reader=reference)
            def respond(prompt):
                if prompt.startswith('Press Enter'):
                    field=driver.page.get_by_placeholder('請輸入問題')
                    field.wait_for(timeout=60_000);field.fill('Calibration');field.press('Enter')
                    driver.page.get_by_text('Answer: Calibration',exact=True).wait_for(timeout=60_000)
                    return ''
                return 'YES'
            return BrowserTarget(**options,driver=driver,extraction_options=extraction_options,
                                 input_fn=respond,output_fn=lambda _:None,review_stream=FixtureTerminal())
        with browser_cli_terminal():
            exit_code=main(['run-agent',str(dataset),'--page-url',url,'--output',str(output),
                           '--calibration-question','Calibration','--model',model,'--timeout','60',
                           '--confirm-browser-run','--allow-response-model-transfer','--no-verbose',
                           '--skill','src/lladar/skill_assets/run-agent-random-sample'],
                          runs_root=tmp_path/'runs',browser_target_factory=factory,extraction_provider_factory=LiveProvider)
        assert exit_code==0
        assert {r['question']:r['actual_response'] for r in read_records(output)}=={q:'Answer: '+q for q in questions}
        run=json.loads(Path(str(output)+'.run.json').read_text(encoding='utf-8'))
        assert run['target']['browser_route']=='dom'
        assert run['target']['verification']=='independent_local_reference'
        assert run['target']['extraction']['calls']==4
        assert all(e['status']=='final' for e in run['target']['extraction']['events'])
        assert all(e['usage'] and e['usage']['promptTokenCount']>0 for e in run['target']['extraction']['events'])
        assert len(requests)==4
        assert [r['body'] for r in requests[:2]]==['Answer: Calibration','Answer: Calibration']
        assert {r['body'] for r in requests[2:]}=={'Answer: '+q for q in questions}
        assert all(set(r)=={'content_type','body'} and r['content_type']=='text/plain' for r in requests)
        assert 'PRIVATE_EXPECTED_SENTINEL' not in json.dumps(requests)
        trials=[json.loads(line) for line in Path(str(output)+'.trials.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [r['status'] for r in trials]==['ok','ok']
        print(json.dumps({'live_model':model,'website_route':'dom','calls':4,'exact_answers':2,
                          'provider_usage':[e['usage'] for e in run['target']['extraction']['events']]},ensure_ascii=False))
