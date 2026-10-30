"""Capture an unsent request, close Chromium, then send it with Python HTTP."""
import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import async_playwright

from probe import shape

ROOT = Path(__file__).resolve().parent


def reconstruct_answer(records):
    text, revision, message_id, complete = '', None, None, False
    for r in records:
        if r.get('type') != 'content':
            continue
        if message_id is not None and r.get('messageId') != message_id:
            raise RuntimeError('Unexpected message change in response stream')
        message_id = r.get('messageId')
        if r.get('mode') == 'replace':
            text = r.get('markdown', '')
        elif r.get('mode') == 'append':
            if r.get('baseRevision') != revision:
                raise RuntimeError('Missing or reordered content revision')
            text += r.get('markdown', '')
        else:
            raise RuntimeError('Unsupported content mode')
        revision = r.get('revision')
        complete = r.get('status') == 'complete'
    return text, complete


class Fragment(HTMLParser):
    """Extract response text and form state without logging hidden credentials."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.inputs = {}
        self.assistant_complete = False
        self.has_markdown = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            self.inputs[attrs['name']] = attrs.get('value', '')
        if attrs.get('data-message-role') == 'assistant' and 'data-message-complete' in attrs:
            self.assistant_complete = True
        markdown = 'data-assistant-markdown-root' in attrs or 'data-assistant-markdown' in attrs
        self.has_markdown |= markdown
        active = markdown or (bool(self.stack) and self.stack[-1][1])
        if tag in ('script', 'style'):
            active = False
        if tag not in ('input', 'br', 'img', 'hr', 'meta', 'link', 'area', 'source', 'wbr'):
            self.stack.append((tag, active))
        if active and tag in ('p', 'br', 'li'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, text):
        if self.stack and self.stack[-1][1]:
            self.text.append(text)


async def capture(recording, timeout, out, log):
    captured = {}
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(ROOT / 'profile'), headless=False, viewport={'width': 1280, 'height': 900},
            service_workers='block')
        browser = context.browser
        page = context.pages[0] if context.pages else await context.new_page()
        preparation_tasks = set()
        async def remember_preparation(response):
            if urlsplit(response.url).path in ('/unauth-mweb/sentinel/chat-requirements/prepare', '/unauth-mweb/sentinel/chat-requirements/finalize') and response.status == 200:
                req=response.request
                captured.setdefault('sentinel_exchanges',[]).append(dict(path=urlsplit(response.url).path,
                    url=req.url,body=req.post_data_json,response=await response.json(),
                    headers={k:v for k,v in (await req.all_headers()).items()
                             if not k.startswith(':') and k not in ('host','content-length','cookie')}))
            if urlsplit(response.url).path != '/unauth-mweb/conversation/prepare':
                return
            if response.status != 200:
                return
            req = response.request
            data = await response.json()
            captured['prepare'] = dict(url=req.url, method=req.method, body=req.post_data_buffer,
                headers={k:v for k,v in (await req.all_headers()).items()
                         if not k.startswith(':') and k not in ('host', 'content-length')})
            captured['prepare_data'] = data
            log('prepare_observed', response_shape=shape(data))
        def schedule_preparation(response):
            task = asyncio.create_task(remember_preparation(response))
            preparation_tasks.add(task)
            task.add_done_callback(preparation_tasks.discard)
        page.on('response', schedule_preparation)
        log('browser_open', headless=False, version=browser.version)
        try:
            await page.goto(recording['url'], wait_until='domcontentloaded', timeout=timeout*1000)
            composer = page.locator(recording['composer'])
            await composer.wait_for(state='visible', timeout=timeout*1000)
            ready = asyncio.get_running_loop().create_future()

            async def intercept(route):
                request = route.request
                try:
                    captured.update(url=request.url, method=request.method,
                                    body=request.post_data_buffer,
                                    headers={k: v for k, v in (await request.all_headers()).items()
                                             if not k.startswith(':') and k not in ('host', 'content-length')})
                    token = captured.get('prepare_data', {}).get('conduit_token')
                    bindings = []
                    if token:
                        for key, value in captured['headers'].items():
                            if value == token:
                                bindings.append(['header', key])
                        for key, value in parse_qsl(urlsplit(request.url).query):
                            if value == token:
                                bindings.append(['query', key])
                        for key, value in parse_qsl(captured['body'].decode()):
                            if value == token:
                                bindings.append(['form', key])
                    captured['conduit_bindings'] = bindings
                    log('request_structure', header_names=list(captured['headers']),
                        query_names=[k for k, _ in parse_qsl(urlsplit(request.url).query)],
                        conduit_bindings=bindings)
                    # Abort, rather than route.fetch: the generation request is still unsent.
                    await route.abort()
                    log('request_captured_and_aborted', method=request.method,
                        path=urlsplit(request.url).path,
                        content_type=captured['headers'].get('content-type'),
                        form_fields=[k for k, _ in parse_qsl(captured['body'].decode())])
                    ready.set_result(True)
                except Exception as e:
                    if not ready.done():
                        ready.set_exception(e)
                    await route.abort()

            await page.route(lambda url: urlsplit(str(url)).path == recording['endpoint_path'], intercept)
            await composer.fill(recording['prompt'])
            await page.screenshot(path=str(out / 'before-handoff.png'))
            log('prompt_filled', prompt=recording['prompt'])
            await page.wait_for_function('()=>{let b=document.querySelector("button[data-composer-submit]");return b && !b.disabled && b.getAttribute("aria-disabled")!=="true";}', timeout=timeout*1000)
            await composer.press('Enter')
            try:
                await asyncio.wait_for(ready, timeout=timeout)
            except Exception:
                await page.screenshot(path=str(out / 'capture-failed.png'))
                raise
            if preparation_tasks:
                await asyncio.gather(*preparation_tasks)
        finally:
            await context.close()
            log('browser_closed', browser_connected=browser.is_connected())
            if browser.is_connected():
                raise RuntimeError('Browser is still connected; refusing detached test')
    log('playwright_stopped')
    return captured


async def receive(client, captured, timeout, turn, out, log):
    log('python_http_send', turn=turn, transport='httpx', browser_running=False,
        path=urlsplit(captured['url']).path)
    records = []
    async with client.stream(captured['method'], captured['url'],
                             content=captured['body'], headers=captured['headers'],
                             timeout=timeout) as response:
        ct = response.headers.get('content-type', '')
        log('python_http_response', turn=turn, status=response.status_code, content_type=ct)
        if 'conversation-source+ndjson' not in ct:
            # Do not persist arbitrary challenge pages or credential-bearing error bodies.
            await response.aread()
            return {'success': False, 'status': response.status_code, 'reason': 'unexpected_content_type'}
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        status = response.status_code

    (out / f'turn-{turn}-schema.json').write_text(
        json.dumps([shape(r) for r in records], ensure_ascii=False, indent=2), encoding='utf-8')
    log('protocol_received', turn=turn, records=len(records),
        types=list(dict.fromkeys(r.get('type') for r in records)),
        diagnostics=[r.get('kind') for r in records if r.get('type') == 'diagnostic'],
        final_type=records[-1].get('type') if records else None)
    log('content_protocol', turn=turn, content=[{k:r[k] for k in ('mode','status','revision','baseRevision','markdown') if k in r} for r in records if r.get('type') == 'content'],
        controls=[{k:r['detail'][k] for k in ('kind','terminalKind','transportOnly') if k in r.get('detail', {})} for r in records if r.get('type') == 'control'])
    fragments = []
    state = {}
    for record in records:
        if record.get('type') == 'state' and isinstance(record.get('conversationState'), str):
            state['conversationState'] = record['conversationState']
        if isinstance(record.get('html'), str):
            f = Fragment()
            f.feed(record['html'])
            state.update(f.inputs)
            fragments.append(f)
    answers = [''.join(f.text).strip() for f in fragments if f.has_markdown and ''.join(f.text).strip()]
    content = [r for r in records if r.get('type') == 'content']
    text, content_complete = reconstruct_answer(records)
    if content:
        answers = [text]
    complete = any(f.assistant_complete for f in fragments) or content_complete
    ended = bool(records) and records[-1].get('type') == 'end'
    diagnostic = any(r.get('type') == 'diagnostic' for r in records)
    success = status == 200 and ended and complete and bool(answers) and not diagnostic
    log('detached_result', turn=turn, success=success, assistant_complete=complete,
        stream_ended=ended, answer=answers[-1] if answers else '',
        state_field_names=list(state))
    return {'success': success, 'status': status, 'state': state,
            'answer': answers[-1] if answers else '', 'records': records}


async def run(timeout, count, refresh_prepare):
    recording = json.loads((ROOT / 'recording.json').read_text(encoding='utf-8'))
    out = ROOT / 'artifacts' / ('detached-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    started = time.monotonic()
    def log(event, **fields):
        row = dict(seconds=round(time.monotonic()-started, 3), event=event, **fields)
        with (out / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        captured = await capture(recording, timeout, out, log)
        captured['headers']['accept-encoding'] = 'identity'
        if captured.get('prepare'):
            captured['prepare']['headers']['accept-encoding'] = 'identity'
        # The Chromium process and Playwright runtime have both exited before this client exists.
        async with httpx.AsyncClient(follow_redirects=False) as client:
            result = await receive(client, captured, timeout, 1, out, log)
            completed = int(result['success'])
            for turn in range(2, count + 1):
                if not result['success']:
                    break
                if not result.get('state', {}).get('conversationState'):
                    log('followup_unavailable', reason='no_updated_conversation_state')
                    break
                fields = dict(parse_qsl(captured['body'].decode(), keep_blank_values=True))
                fields['conversationState'] = result['state']['conversationState']
                fields['assistantMessageId'] = str(uuid.uuid4())
                fields['userMessageId'] = str(uuid.uuid4())
                fields['prompt'] = recording['prompt']
                # operationId and the turn trace identify this submission, not the browser session.
                u = urlsplit(captured['url'])
                query = [(k, str(uuid.uuid4()) if k == 'operationId' else v)
                         for k,v in parse_qsl(u.query, keep_blank_values=True)]
                captured['url'] = urlunsplit((u.scheme, u.netloc, u.path, urlencode(query), u.fragment))
                if 'x-oai-turn-trace-id' in captured['headers']:
                    captured['headers']['x-oai-turn-trace-id'] = str(uuid.uuid4())
                if refresh_prepare:
                    prep = captured.get('prepare')
                    if prep is None:
                        log('followup_unavailable', reason='no_observed_prepare_request')
                        break
                    prep_fields = dict(parse_qsl(prep['body'].decode(), keep_blank_values=True))
                    prep_fields['conversationState'] = fields['conversationState']
                    log('python_prepare_send', turn=turn, browser_running=False,
                        request_field_names=list(prep_fields))
                    prepared = await client.request(prep['method'], prep['url'],
                        content=urlencode(prep_fields).encode(), headers=prep['headers'], timeout=timeout)
                    log('python_prepare_response', turn=turn, status=prepared.status_code,
                        content_type=prepared.headers.get('content-type'),
                        content_encoding=prepared.headers.get('content-encoding'))
                    if prepared.status_code != 200 or 'application/json' not in prepared.headers.get('content-type', ''):
                        break
                    prep_data = prepared.json()
                    log('python_prepare_schema', turn=turn, response_shape=shape(prep_data))
                    for key in fields:
                        if key in prep_data and isinstance(prep_data[key], str):
                            fields[key] = prep_data[key]
                    new_token = prep_data.get('conduit_token')
                    if new_token:
                        for where, key in captured['conduit_bindings']:
                            if where == 'header':
                                captured['headers'][key] = new_token
                            elif where == 'form':
                                fields[key] = new_token
                            elif where == 'query':
                                u = urlsplit(captured['url'])
                                query = [(k, new_token if k == key else v) for k,v in parse_qsl(u.query, keep_blank_values=True)]
                                captured['url'] = urlunsplit((u.scheme, u.netloc, u.path, urlencode(query), u.fragment))
                        log('conduit_refreshed', turn=turn, bindings=captured['conduit_bindings'])
                captured['body'] = urlencode(fields).encode()
                log('followup_prepared', turn=turn, browser_reopened=False,
                    updated_fields=['conversationState', 'assistantMessageId', 'userMessageId', 'prompt', 'operationId', 'x-oai-turn-trace-id'],
                    verification_tokens='reused_in_memory')
                result = await receive(client, captured, timeout, turn, out, log)
                completed += int(result['success'])
        log('run_complete', detached_success=completed == count, completed=completed,
            requested=count, status=result['status'])
        return completed == count
    except Exception as e:
        # Do not log arbitrary exception messages: HTTP client errors can include credentials/URLs.
        log('run_failed', error=type(e).__name__)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--refresh-prepare', action='store_true')
    args = parser.parse_args()
    if not 10 <= args.timeout <= 900 or not 1 <= args.count <= 20:
        parser.error('timeout must be 10..900 seconds; count must be 1..20')
    raise SystemExit(0 if asyncio.run(run(args.timeout, args.count, args.refresh_prepare)) else 1)
