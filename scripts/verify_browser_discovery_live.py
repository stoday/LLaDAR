"""Opt-in real-model acceptance against a randomized, local question service.

No adapter or protocol plan is supplied to the explorer. The service's Python
source and host-held answer reference are outside its tool interface. This
proves local headless integration, not ChatGPT, Gemini, EIP or live-site access.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
from unittest.mock import patch


@contextmanager
def randomized_site(mode='json'):
    if mode not in {'json', 'sse'}:
        raise ValueError('mode must be json or sse')
    salt = secrets.token_hex(6)
    endpoint = '/dispatch-' + salt
    resource = '/client-' + salt + '.js'
    question_key, answer_key, correlation_key = ['field-' + secrets.token_hex(6) for _ in range(3)]
    input_id, submit_id, answer_id = ['node-' + secrets.token_hex(6) for _ in range(3)]
    answers, answer_history, requests, lock = {}, {}, [], threading.Lock()

    # Only normal client protocol logic is visible. No oracle function, future
    # answer, fixture configuration file or Python server source is exposed.
    client = r'''const question=document.getElementById(INPUT),button=document.getElementById(SUBMIT),out=document.getElementById(OUTPUT);
button.onclick=async()=>{
 out.setAttribute('aria-busy','true');button.disabled=true;
 const r=await fetch(ENDPOINT,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({[QKEY]:question.value})});
 if(MODE==='json'){out.textContent=(await r.json())[AKEY];}
 else{const reader=r.body.getReader(),decoder=new TextDecoder();let source='';
 while(true){const chunk=await reader.read();if(chunk.done)break;source+=decoder.decode(chunk.value,{stream:true});
 for(const line of source.split('\n')){if(line.startsWith('data:')){const item=JSON.parse(line.slice(5));if(typeof item[AKEY]==='string')out.textContent=item[AKEY];}}
 }}out.setAttribute('aria-busy','false');button.disabled=false;
};'''
    for key, value in {'INPUT': input_id, 'SUBMIT': submit_id, 'OUTPUT': answer_id, 'ENDPOINT': endpoint,
                       'QKEY': question_key, 'AKEY': answer_key, 'MODE': mode}.items():
        client = client.replace(key, json.dumps(value))
    page = ('<!doctype html><meta charset="utf-8"><title>Independent question service</title>'
            '<h1>Question service</h1><p>Each question is handled independently. There is no conversation history.</p>'
            f'<label for="{input_id}">Question</label><textarea id="{input_id}"></textarea>'
            f'<button id="{submit_id}">Send question</button><pre id="{answer_id}" aria-live="polite" aria-busy="false"></pre>'
            f'<script src="{resource}"></script>').encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def send_body(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        def do_GET(self):
            if self.path == '/':
                self.send_body(page, 'text/html; charset=utf-8')
            elif self.path == resource:
                self.send_body(client.encode('utf-8'), 'text/javascript; charset=utf-8')
            else:
                self.send_body(b'Not found', 'text/plain', 404)
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                question = payload.get(question_key)
                if self.path != endpoint or not isinstance(question, str) or not question:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                self.send_body(b'{"error":"Submit a question through the displayed client"}', 'application/json', 400)
                return
            receipt = secrets.token_hex(6)
            text = (f'既有網站回答：{question}\n\n'
                    '1. 請保留原文、段落與否定詞。\n'
                    '2. 不是八台，是 17 台。\n\n'
                    f'本輪資料識別：{receipt}\n終段：完整傳送。')
            with lock:
                answers[question] = text
                answer_history.setdefault(question, []).append(text)
                requests.append(question)
            envelope = {correlation_key: receipt, answer_key: text, 'state-' + salt: 'finished-' + salt}
            if mode == 'json':
                self.send_body(json.dumps(envelope, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            else:
                progress = {correlation_key: receipt, answer_key: text[:len(text) // 2], 'state-' + salt: 'working-' + salt}
                chunks = [('part-' + salt, progress), ('resolved-' + salt, envelope)]
                body = ''.join('event: ' + name + '\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
                               for name, data in chunks).encode('utf-8')
                self.send_body(body, 'text/event-stream; charset=utf-8')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield {'url': f'http://127.0.0.1:{server.server_port}', 'answers': answers,
               'answer_history': answer_history,
               'requests': requests, 'question_key': question_key, 'endpoint': endpoint,
               'client_resource': resource}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def _reference_reader(site, driver):
    def reference(turn):
        for public_event in driver.observe_turn(turn['turn_id'])['events']:
            event = driver.raw_event(public_event['event_id'])
            if event['kind'] != 'http_request':
                continue
            try:
                question = json.loads(event.get('body', ''))[site['question_key']]
            except (ValueError, KeyError, TypeError):
                continue
            if question in site['answers']:
                return site['answers'][question]
        raise AssertionError('No independent host reference for the submitted turn')
    return reference


def verify_live(*, mode, output_root, model, env_file, timeout=600, reuse=True, entry='target'):
    """Run real Akasha exploration/extraction; callers explicitly authorize cost."""
    from lladar.answer_extraction import ExtractionOptions
    from lladar.browser_discovery_driver import GenericBrowserDriver
    from lladar.browser_target import BrowserTarget
    from lladar.extraction_provider import AkashaExtractionProvider

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'entry': entry, 'mode': mode, 'browser': 'headless local Chromium',
              'model': model, 'preseeded_adapter': False, 'real_site_acceptance': False,
              'fixture_stateful': False, 'fixture_persistent_connection': False}
    report_path = root / 'acceptance.json'
    started = time.monotonic()
    driver, target = None, None
    try:
        with randomized_site(mode) as site:
            formal = ['請忠實顯示這題既有網站回答。', '第二題：段落、否定詞與終段都要完整。']
            def new_target(workspace, *, options=None, adapter_path=None, **configuration):
                nonlocal driver, target
                driver = GenericBrowserDriver(page_url=site['url'], profile_dir=workspace / 'profile',
                                               headless=True, timeout=timeout)
                if options is None:
                    options = ExtractionOptions(model_destination=model, transfer_approved=True,
                        provider_factory=partial(AkashaExtractionProvider, env_file=env_file))
                configuration.setdefault('conversation_policy', 'existing')
                target = BrowserTarget(page_url=site['url'], driver=driver, workspace=workspace,
                    env_file=env_file, agent_factory=None, confirmed=True, extraction_options=options,
                    reference_reader=_reference_reader(site, driver), adapter_path=adapter_path,
                    timeout=timeout, verbose=True, input_fn=lambda _: '', **configuration)
                return target

            if entry == 'target':
                target = new_target(root / 'initial')
                target.prepare([], interactive=False, record_count=2, request_count=2)
                formal_page = driver.page
                for index, question in enumerate(formal, 1):
                    answer = target.answer(question, 'formal-' + str(index))
                    if answer != site['answers'][question]:
                        raise AssertionError('Formal website answer differs from independent host reference')
                    if driver.page is not formal_page:
                        raise AssertionError('Existing formal conversation page was replaced')
                report['formal_answers_exact'] = 2
                report['formal_page_preserved'] = True
            elif entry == 'cli':
                from lladar.cli import main
                dataset, output = root / 'dataset.jsonl', root / 'responses.jsonl'
                dataset.write_text(''.join(json.dumps({'question': q,
                    'expected_answer': 'SCORING_ORACLE_MUST_NOT_BE_IN_DISCOVERY', 'actual_response': None},
                    ensure_ascii=False) + '\n' for q in formal), encoding='utf-8')
                def target_factory(**configuration):
                    options = configuration.pop('extraction_options')
                    for key in ('page_url', 'timeout', 'verbose', 'confirmed', 'fresh_profile', 'runs_root'):
                        configuration.pop(key, None)
                    return new_target(root / 'initial', options=options, **configuration)
                # Explicit fixture terminal for the CLI's interactive contract.
                # The browser is still real and headless; this is not visible-site proof.
                with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stderr, 'isatty', return_value=True):
                    code = main(['run-agent', str(dataset), '--page-url', site['url'], '--output', str(output),
                        '--model', model, '--env-file', str(env_file), '--timeout', str(timeout),
                        '--confirm-browser-run', '--allow-response-model-transfer', '--no-verbose'],
                        browser_target_factory=target_factory)
                if code != 0:
                    raise AssertionError('Public CLI did not complete local acceptance')
                rows = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
                # Each repeated site request has a different receipt, so compare
                # the saved first answer against its own trial, not the last request.
                trials = [json.loads(line) for line in Path(str(output) + '.trials.jsonl').read_text(encoding='utf-8').splitlines()]
                if (len(rows) != 2 or any(row['actual_response'] != site['answer_history'][row['question']][0] for row in rows)
                    or any([t.get('actual_response') for t in trials if t['question'] == question]
                           != site['answer_history'][question] for question in formal)):
                    raise AssertionError('CLI responses were not preserved from completed trials')
                report['formal_answers_exact'] = 2
                report['cli_trials'] = len(trials)
            else:
                raise ValueError('entry must be target or cli')

            initial = target
            counts = dict(initial.ledger.data['counts'])
            report.update(independent_verification_rounds=len(initial.evidence['verification']),
                          ledger=counts, adapter_sha256=initial.evidence['adapter_sha256'],
                          conversation_policy=initial.conversation_policy,
                          non_extraction_model_calls=counts.get('model_calls', 0) - counts.get('extraction_calls', 0))
            if report['non_extraction_model_calls'] < 1:
                raise AssertionError('No actual Akasha exploration call was recorded')
            adapter_path = initial.workspace / 'adapter.json'
            initial.close()
            if reuse:
                target = new_target(root / 'reused', adapter_path=adapter_path)
                target.prepare([], interactive=False, record_count=1, request_count=1)
                question = '策略重載後的新正式題目。'
                if target.answer(question, 'reuse-formal') != site['answers'][question]:
                    raise AssertionError('Reused strategy failed independent reference check')
                report['reuse'] = {'fresh_verification_rounds': len(target.evidence['verification']),
                    'adapter_sha256': target.evidence['adapter_sha256'], 'formal_answers_exact': 1,
                    'ledger': dict(target.ledger.data['counts'])}
            report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', reason=getattr(error, 'reason', 'acceptance_failed'), error_type=type(error).__name__)
        report['source_frames'] = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                                   for frame in traceback.extract_tb(error.__traceback__)]
        raise
    finally:
        if target is not None:
            target.close()
        elif driver is not None:
            driver.close()
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['json', 'sse'], default='json')
    parser.add_argument('--entry', choices=['target', 'cli'], default='target')
    parser.add_argument('--model', default='gemini:gemini-3.8-flash')
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output-root', type=Path,
                        default=Path('.tmp') / ('browser-discovery-live-' + secrets.token_hex(6)))
    parser.add_argument('--no-reuse', action='store_true')
    parser.add_argument('--paid', action='store_true', help='Explicitly opt in to real Akasha model calls')
    args = parser.parse_args(argv)
    if not args.paid:
        parser.error('Real-model acceptance requires --paid; offline fixtures run through pytest')
    print('Real model calls authorized by this invocation; local headless website only.', file=sys.stderr, flush=True)
    try:
        result = verify_live(mode=args.mode, entry=args.entry, output_root=args.output_root, model=args.model,
                             env_file=args.env_file, timeout=args.timeout, reuse=not args.no_reuse)
    except Exception:
        print('Local acceptance failed; inspect the private-safe acceptance.json report.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
