"""Trace field provenance in two native browser turns; keep raw credentials in memory."""
import argparse
import asyncio
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import async_playwright
from compare_turns import PROMPTS
from probe import shape

ROOT = Path(__file__).resolve().parent


def scalars(value, path='$'):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from scalars(child, path + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from scalars(child, f'{path}[{index}]')
    elif isinstance(value, str) and value:
        yield path, value
        try:
            nested = json.loads(value)
        except ValueError:
            return
        if isinstance(nested, (dict,list)):
            yield from scalars(nested, path + '<decoded-json>')


def safe_stack(stack):
    # Retain asset paths and source offsets, never URL query values or dynamic eval source.
    return re.findall(r'https://[^\s/)]+(/[^\s?#)]+?\.js):([0-9]+):([0-9]+)', stack)


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key,value in attrs.items():
            if value and (key.startswith('data-') or (tag=='input' and key=='value')):
                self.values.append((f'{tag}[{attrs.get("name",attrs.get("id",""))}].{key}', value))


async def run(timeout):
    out = ROOT / 'artifacts' / ('sources-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    started = time.monotonic()
    captured, candidates, assets, pending = [], [], {}, set()
    initial_dom, snapshots, replies = '', [], []
    turn = 0
    def log(event, **fields):
        row = dict(seconds=round(time.monotonic()-started,3), event=event, **fields)
        with (out/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(row,ensure_ascii=False),flush=True)
    def candidate(value, source, at):
        candidates.append(dict(value=value, source=source, at=at))
    async def observe_request(request, ui_turn):
        path = urlsplit(request.url).path
        if not ('/conversation/' in path or '/sentinel/' in path):
            return
        at = int(time.time()*1000)
        headers = await request.all_headers()
        try:
            payload = request.post_data_json
        except Exception:
            payload = None
        for pointer,value in scalars(payload):
            candidate(value, dict(kind='earlier_request', endpoint=path, pointer=pointer), at)
        if path == '/unauth-mweb/conversation/updates':
            fields = dict(parse_qsl(request.post_data or '',keep_blank_values=True))
            captured.append(dict(turn=ui_turn,at=at,fields=fields,headers=headers,url=request.url))
            log('generation_request',turn=ui_turn,field_names=list(fields))
    async def observe_response(response):
        path=urlsplit(response.url).path
        host=urlsplit(response.url).hostname
        at=int(time.time()*1000)
        if host != 'chatgpt.com':
            return
        for header in await response.headers_array():
            if header['name'].lower()=='set-cookie':
                jar=SimpleCookie()
                jar.load(header['value'])
                for name,morsel in jar.items():
                    candidate(morsel.value,dict(kind='response_header',endpoint=path,header='Set-Cookie',cookie=name),at)
        if '.js' in path and 'javascript' in response.headers.get('content-type',''):
            try:
                assets[path]=await response.text()
            except Exception:
                pass
    def enqueue(fn,*args):
        task=asyncio.create_task(fn(*args))
        pending.add(task)
        task.add_done_callback(pending.discard)

    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(ROOT/'profile'),headless=False,
            viewport={'width':1280,'height':900},service_workers='block')
        await context.add_init_script(path=str(ROOT/'trace_hooks.js'))
        page=context.pages[0] if context.pages else await context.new_page()
        context.on('request',lambda r:enqueue(observe_request,r,turn))
        context.on('response',lambda r:enqueue(observe_response,r))
        log('browser_open',headless=False,version=context.browser.version)
        try:
            await page.goto('https://chatgpt.com',wait_until='domcontentloaded',timeout=timeout*1000)
            initial_dom=await page.content()
            for number,prompt in enumerate(PROMPTS,1):
                turn=number
                before=await page.locator('[data-message-role="assistant"]').count()
                snapshots.append(dict(at=int(time.time()*1000),label=f'before-turn-{number}',
                                      html=await page.content()))
                composer=page.locator('#mobile-composer-prompt')
                await composer.wait_for(state='visible',timeout=timeout*1000)
                await composer.fill(prompt)
                log('prompt_filled',turn=number,prompt=prompt)
                await composer.press('Enter')
                await page.wait_for_function('n=>{const es=document.querySelectorAll("[data-message-role=assistant]");const e=es[es.length-1];return es.length>n && e.hasAttribute("data-message-complete") && e.querySelector("[data-assistant-markdown-root]")?.innerText.trim();}',arg=before,timeout=timeout*1000)
                await page.locator('[data-composer-stop-icon]').first.wait_for(state='hidden',timeout=timeout*1000)
                await page.wait_for_function('n=>{const a=window.__sourceTrace.fetches.filter(x=>x.path==="/unauth-mweb/conversation/updates");return a.length>=n && a[n-1].done;}',arg=number,timeout=timeout*1000)
                await asyncio.sleep(1)
                answer=await page.locator('[data-message-role="assistant"]').last.locator('[data-assistant-markdown-root]').inner_text()
                replies.append(answer)
                log('reply_complete',turn=number,answer=answer)
                await page.screenshot(path=str(out/f'turn-{number}-complete.png'))
                snapshots.append(dict(at=int(time.time()*1000),label=f'after-turn-{number}',html=await page.content()))
            # Let already-started prefetches finish; do not send another message.
            await asyncio.sleep(2)
            traces=[]
            for index,frame in enumerate(page.frames):
                try:
                    data=await frame.evaluate('window.__sourceTrace || null')
                    if data:
                        traces.append(dict(frame=index,**data))
                except Exception:
                    pass
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)
        finally:
            await context.close()
            log('browser_closed')

    for trace in traces:
        for response in trace['fetches']:
            try:
                body=json.loads(response['body'])
                records=[body]
            except ValueError:
                records=[]
                for line in response['body'].splitlines():
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        pass
            for index,record in enumerate(records):
                for pointer,value in scalars(record):
                    candidate(value,dict(kind='response_body',endpoint=response['path'],record=index,pointer=pointer),response.get('finished',response['received']))
                if isinstance(record,dict) and isinstance(record.get('html'),str):
                    parser=Attributes();parser.feed(record['html'])
                    for pointer,value in parser.values:
                        candidate(value,dict(kind='response_html',endpoint=response['path'],record=index,pointer=pointer),response.get('finished',response['received']))
        for generated in trace['random']:
            candidate(generated['value'],dict(kind='browser_random',generator=generated['kind'],
                      frame=trace['frame'],stack=safe_stack(generated['stack'])),generated['at'])
        for call in trace.get('sdk',[]):
            for pointer,value in scalars(call.get('value')):
                candidate(value,dict(kind='browser_sdk_return',method=call['name'],pointer=pointer,
                    frame=trace['frame'],stack=safe_stack(call['stack'])),call['at'])
    for snap in snapshots:
        parser=Attributes();parser.feed(snap['html'])
        for pointer,value in parser.values:
            candidate(value,dict(kind='dom_snapshot',snapshot=snap['label'],pointer=pointer),snap['at'])

    if len(captured)!=2:
        raise RuntimeError('Expected two captured generation requests')
    first,second=captured
    targets=[]
    for name,value in second['fields'].items():
        if first['fields'].get(name)!=value:
            targets.append(('form',name,value))
    for name,value in second['headers'].items():
        if name in ('x-conduit-token','x-oai-turn-trace-id','x-web-mobile-prepare-state') and first['headers'].get(name)!=value:
            targets.append(('header',name,value))
    for name,value in parse_qsl(urlsplit(second['url']).query):
        if dict(parse_qsl(urlsplit(first['url']).query)).get(name)!=value:
            targets.append(('query',name,value))
    for name,morsel in cookies_for(second['headers']).items():
        if cookies_for(first['headers']).get(name)!=morsel:
            targets.append(('cookie',name,morsel))

    findings=[]
    for location,name,value in targets:
        matches=[c['source'] for c in candidates if c['at']<=second['at'] and c['value']==value
                 and not (c['source'].get('kind')=='earlier_request' and c['source'].get('endpoint')=='/unauth-mweb/conversation/updates')]
        uuid=value.removeprefix('assistant-').removeprefix('pending-')
        generated=[c['source'] for c in candidates if c['at']<=second['at'] and c['value']==uuid and c['source']['kind']=='browser_random']
        if generated and not any(m.get('kind')=='browser_random' for m in matches):
            matches.extend({**g,'transform':'pending- prefix' if value.startswith('pending-') else 'assistant- prefix' if value!=uuid else 'exact UUID'} for g in generated)
        nested=[]
        try:
            decoded=json.loads(value)
            if isinstance(decoded,(dict,list)):
                for pointer,child in scalars(decoded):
                    sources=[c['source'] for c in candidates if c['at']<second['at'] and c['value']==child
                             and c['source']['kind'] in ('response_body','browser_random')]
                    nested.append(dict(pointer=pointer,length=len(child),sources=sources[:10]))
        except ValueError:
            pass
        findings.append(dict(location=location,field=name,length=len(value),exact_matches=matches,
                             decoded_shape=shape(decoded) if nested else None,nested_matches=nested))

    symbols=[]
    names=[f['field'] for f in findings]+['getProof','getTurnstile','getTelemetry','getTiming','time_since_loaded','userMessageCount','JSON.stringify','pending-']
    for asset,code in assets.items():
        if any(x in asset for x in ('/sentinel-', 'conversation-followup-dom-controller-', 'conversation-prepare-client-', '/a-Vjvhxgkj.', 'conversation-state-')):
            static=out/'static-source';static.mkdir(exist_ok=True)
            (static/Path(asset).name).write_text(code,encoding='utf-8')
        for name in names:
            for match in list(re.finditer(re.escape(name),code))[:5]:
                # Static public source only; response bodies/credential values are never written.
                snippet=code[max(0,match.start()-180):match.end()+250]
                symbols.append(dict(asset=asset,symbol=name,offset=match.start(),snippet=snippet))
    state_comparisons=[]
    target_state=json.loads(second['fields']['conversationState'])
    for c in candidates:
        if c['source'].get('kind')=='response_body' and c['source'].get('endpoint')=='/unauth-mweb/conversation/updates' and c['source'].get('pointer')=='$.conversationState' and c['at']<second['at']:
            try:
                previous=json.loads(c['value'])
                state_comparisons.append(dict(source=c['source'],semantic_equal=previous==target_state,
                    source_shape=shape(previous),target_shape=shape(target_state),
                    differing_top_level_keys=[k for k in set(previous)|set(target_state) if previous.get(k)!=target_state.get(k)]))
            except ValueError:
                pass
    report=dict(prompts=PROMPTS,answers=replies,fields=findings,source_symbols=symbols,
                state_comparisons=state_comparisons,
                observation_counts=dict(response_candidates=len(candidates),assets=len(assets),frames=len(traces)),
                sdk_methods=sorted({call['name'] for trace in traces for call in trace.get('sdk',[])}))
    report['static_flow_evidence'] = static_flow_evidence(assets)
    (out/'sources.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    log('source_analysis_complete',fields=[dict(field=f['field'],sources=f['exact_matches']) for f in findings],assets=len(assets))
    print('Artifacts: '+str(out),flush=True)


def cookies_for(headers):
    jar=SimpleCookie();jar.load(headers.get('cookie',''))
    return {name:morsel.value for name,morsel in jar.items()}


def static_flow_evidence(assets):
    evidence=[]
    searches=[
        ('proofToken','sentinel-', 'G.getEnforcementToken(e,'),
        ('proofToken','sentinel-', 'async getEnforcementToken(e,'),
        ('turnstileToken','sentinel-', 't.getEnforcementToken(e)'),
        ('chatRequirementsToken','sentinel-', 'chatRequirementsToken:c.token'),
        ('timingToken','sentinel-', 'e.timing?.()'),
        ('assistantMessageId','conversation-followup-dom-controller-', 'assistantMessageId:`pending-`+t()'),
        ('clientContextualInfo','/a-Vjvhxgkj.', 'Math.floor(performance.now()/1e3)'),
    ]
    for field,asset_fragment,symbol in searches:
        for asset,code in assets.items():
            if asset_fragment in asset and symbol in code:
                offset=code.index(symbol)
                evidence.append(dict(field=field,asset=asset,offset=offset,symbol=symbol,
                    snippet=code[max(0,offset-150):offset+len(symbol)+220]))
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args()
    if not 10 <= args.timeout <= 900:
        parser.error('timeout must be 10..900 seconds')
    asyncio.run(run(args.timeout))
