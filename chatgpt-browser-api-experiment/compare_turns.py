"""Observe two real browser turns and compare credentials without saving their values."""
import argparse
import asyncio
import json
import time
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
PROMPTS = ['嗨', '秋天天氣真好']


def compare_fields(first, second):
    return {key: {
        'first_present': key in first,
        'second_present': key in second,
        'same': key in first and key in second and first[key] == second[key],
        'first_empty': first.get(key) == '',
        'second_empty': second.get(key) == '',
        'first_length': len(first[key]) if key in first else None,
        'second_length': len(second[key]) if key in second else None,
    } for key in sorted(first.keys() | second.keys())}


def cookies(header):
    jar = SimpleCookie()
    jar.load(header)
    return {key: value.value for key, value in jar.items()}


async def run(timeout):
    recording = json.loads((ROOT / 'recording.json').read_text(encoding='utf-8'))
    out = ROOT / 'artifacts' / ('compare-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    start = time.monotonic()
    requests = []
    network = []
    tasks = set()
    current_turn = 0
    def save(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    def log(event, **fields):
        row = dict(seconds=round(time.monotonic()-start, 3), event=event, **fields)
        with (out / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(json.dumps(row, ensure_ascii=False), flush=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(ROOT / 'profile'), headless=False, viewport={'width': 1280, 'height': 900},
            service_workers='block')
        page = context.pages[0] if context.pages else await context.new_page()
        # Clone response streams for observation; the original fetch and body go to the website.
        await page.add_init_script("""(() => {
            const originalFetch = window.fetch.bind(window);
            window.__turnResponses = [];
            window.fetch = async (...args) => {
                const response = await originalFetch(...args);
                const path = new URL(response.url, location.href).pathname;
                if (path === '/unauth-mweb/conversation/updates') {
                    const item = {status:response.status, body:'', complete:false};
                    window.__turnResponses.push(item);
                    const reader=response.clone().body.getReader(), decoder=new TextDecoder();
                    (async()=>{try {while(true) {const {value,done}=await reader.read();
                        if(done) {item.body+=decoder.decode();break;}
                        item.body+=decoder.decode(value,{stream:true});}}
                        catch {item.error=true;} finally {item.complete=true;}})();
                }
                return response;
            };
        })();""")

        async def capture_request(request, turn):
            if urlsplit(request.url).path != recording['endpoint_path'] or request.method != 'POST':
                return
            headers = await request.all_headers()
            fields = dict(parse_qsl(request.post_data or '', keep_blank_values=True))
            requests.append(dict(turn=turn, url=request.url, headers=headers, fields=fields))
            log('request_observed', turn=turn, method=request.method,
                path=urlsplit(request.url).path, request_encoding=headers.get('content-type'),
                prompt=fields.get('prompt'), field_names=list(fields))

        def on_request(request):
            task = asyncio.create_task(capture_request(request, current_turn))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        def on_response(response):
            path = urlsplit(response.url).path
            if '/conversation/' in path or '/sentinel/' in path:
                row = dict(seconds=round(time.monotonic()-start, 3), turn=current_turn,
                           path=path, method=response.request.method, status=response.status,
                           content_type=response.headers.get('content-type'))
                network.append(row)
                log('network_response', **{k:v for k,v in row.items() if k != 'seconds'})

        page.on('request', on_request)
        page.on('response', on_response)
        log('browser_open', headless=False, version=context.browser.version)
        replies = []
        raw_streams = []
        try:
            await page.goto(recording['url'], wait_until='domcontentloaded', timeout=timeout*1000)
            # No reload/new chat between the two turns.
            for turn, prompt in enumerate(PROMPTS, 1):
                current_turn = turn
                composer = page.locator(recording['composer'])
                await composer.wait_for(state='visible', timeout=timeout*1000)
                count_before = await page.locator(recording['assistant']).count()
                await composer.fill(prompt)
                log('prompt_filled', turn=turn, prompt=prompt, existing_assistant_messages=count_before)
                await page.screenshot(path=str(out / f'turn-{turn}-typed.png'))
                await composer.press('Enter')
                await page.wait_for_function(
                    'a => {let els=document.querySelectorAll(a.selector);let el=els[els.length-1];let md=el?.querySelector("[data-assistant-markdown-root]");return els.length>a.before && el.hasAttribute("data-message-complete") && md && md.innerText.trim().length>0}',
                    arg={'selector':recording['assistant'], 'before':count_before}, timeout=timeout*1000)
                answer = await page.locator(recording['assistant']).last.locator('[data-assistant-markdown-root]').inner_text()
                await page.wait_for_function('n => window.__turnResponses.length>=n && window.__turnResponses[n-1].complete', arg=turn, timeout=timeout*1000)
                stream = await page.evaluate('n=>window.__turnResponses[n-1]', turn)
                records = []
                for line in stream['body'].splitlines():
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except ValueError:
                            if not stream.get('error'):
                                raise
                raw_streams.append(records)
                log('stream_complete', turn=turn, status=stream['status'],
                    end_type=records[-1].get('type') if records else None,
                    content_statuses=[r.get('status') for r in records if r.get('type')=='content'])
                if stream['status'] != 200:
                    raise RuntimeError('Turn stream did not end successfully')
                if stream.get('error'):
                    log('stream_observer_interrupted', turn=turn,
                        evidence='Use DOM complete, hidden stop icon and stable text for browser completion')
                await page.locator('[data-composer-stop-icon]').first.wait_for(state='hidden', timeout=timeout*1000)
                previous, stable = None, 0
                deadline = time.monotonic() + timeout
                while stable < 3 and time.monotonic() < deadline:
                    answer = await page.locator(recording['assistant']).last.locator('[data-assistant-markdown-root]').inner_text()
                    stable = stable + 1 if answer.strip() and answer == previous else 0
                    previous = answer
                    await asyncio.sleep(0.25)
                if stable < 3:
                    raise RuntimeError('Assistant text did not stabilize')
                replies.append(answer)
                log('reply_complete', turn=turn, answer=answer,
                    evidence='DOM complete + stop icon hidden + stable nonempty markdown',
                    stream_ended=bool(records and records[-1].get('type')=='end'))
                await page.screenshot(path=str(out / f'turn-{turn}-complete.png'))

            if tasks:
                await asyncio.gather(*tasks)
            if len(requests) != 2 or [r['turn'] for r in requests] != [1, 2]:
                raise RuntimeError('Expected exactly one generation request per turn')
            first, second = requests
            credentials = ['chatRequirementsToken', 'proofToken', 'turnstileToken',
                           'telemetryToken', 'timingToken', 'oai-session-id']
            field_comparison = compare_fields(first['fields'], second['fields'])
            header_comparison = compare_fields(first['headers'], second['headers'])
            cookie_comparison = compare_fields(cookies(first['headers'].get('cookie', '')),
                                               cookies(second['headers'].get('cookie', '')))
            query_comparison = compare_fields(dict(parse_qsl(urlsplit(first['url']).query)),
                                              dict(parse_qsl(urlsplit(second['url']).query)))
            previous_states = [r.get('conversationState') for r in raw_streams[0] if r.get('type')=='state']
            conversation_ids = [[r.get('detail', {}).get('conversationId') for r in rs
                                 if r.get('type')=='control' and r.get('detail', {}).get('kind')=='conversation-id']
                                for rs in raw_streams]
            comparison = {
                'prompts': PROMPTS, 'answers': replies,
                'same_browser_and_page': True, 'reload_between_turns': False,
                'same_conversation_id': bool(conversation_ids[0] and conversation_ids[1]) and conversation_ids[0][-1] == conversation_ids[1][-1],
                'second_state_matches_first_final_state': bool(previous_states) and second['fields'].get('conversationState') == previous_states[-1],
                'same_field_names': first['fields'].keys() == second['fields'].keys(),
                'credential_form_fields': {k:field_comparison[k] for k in credentials if k in field_comparison},
                'telemetry_literal_markers': [r['fields'].get('telemetryToken') if r['fields'].get('telemetryToken') in ('true','false','null','0','1') else '<redacted>' for r in requests],
                'prepare_state_markers': [r['headers'].get('x-web-mobile-prepare-state') if r['headers'].get('x-web-mobile-prepare-state') in ('present','prepared','missing','none','skipped','ready') else '<redacted>' for r in requests],
                'cookies': cookie_comparison,
                'headers': header_comparison,
                'query_parameters': query_comparison,
                'all_form_fields': field_comparison,
                'network_timeline': network,
            }
            save('comparison.json', comparison)
            formats = []
            for request in requests:
                formats.append({
                    'turn':request['turn'], 'method':'POST', 'path':urlsplit(request['url']).path,
                    'query':{k:'<redacted>' for k,_ in parse_qsl(urlsplit(request['url']).query)},
                    'content_type':request['headers'].get('content-type'),
                    'header_names':list(request['headers']),
                    'form':{k:v if k=='prompt' else '<empty>' if v=='' else '<redacted>' for k,v in request['fields'].items()},
                    'form_encoded_example':urlencode({k:v if k=='prompt' else '<empty>' if v=='' else '<redacted>' for k,v in request['fields'].items()}),
                })
            save('request-formats.json', formats)
            log('comparison_complete', same_conversation=comparison['same_conversation_id'],
                state_handoff=comparison['second_state_matches_first_final_state'],
                credentials=comparison['credential_form_fields'], cookies=cookie_comparison,
                changed_headers=[k for k,v in header_comparison.items() if not v['same']],
                changed_fields=[k for k,v in field_comparison.items() if not v['same']])
        except Exception as e:
            log('run_failed', error=type(e).__name__)
            await page.screenshot(path=str(out / 'failed.png'))
            raise
        finally:
            await context.close()
            log('browser_closed')
    print('Artifacts: ' + str(out), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 900:
        parser.error('timeout must be 10..900 seconds')
    asyncio.run(run(args.timeout))
