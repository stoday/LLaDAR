"""Build a second HTTP-only turn after one browser handoff. No challenge solver."""
import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import async_playwright

from detached import capture, receive

ROOT = Path(__file__).resolve().parent


def updated_state(records):
    state = None
    safety = None
    for record in records:
        if record.get('type') == 'state':
            if isinstance(record.get('conversationState'), str):
                state = json.loads(record['conversationState'])
            if isinstance(record.get('safety'), dict):
                safety = record['safety']
    if state is None:
        raise RuntimeError('No returned conversation state')
    if safety is not None:
        state['safety'] = safety
    return state


def followup_request(first, state, prompt):
    fields = dict(parse_qsl(first['body'].decode(), keep_blank_values=True))
    fields.update(conversationState=json.dumps(state, ensure_ascii=False, separators=(',', ':')),
                  assistantMessageId='pending-' + str(uuid.uuid4()), userMessageId=str(uuid.uuid4()),
                  prompt=prompt)
    headers = {k:v for k,v in first['headers'].items() if k not in ('cookie', 'content-length')}
    headers['x-oai-turn-trace-id'] = str(uuid.uuid4())
    headers['accept-encoding'] = 'identity'
    u = urlsplit(first['url'])
    query = [(k, str(uuid.uuid4()) if k == 'operationId' else v)
             for k,v in parse_qsl(u.query, keep_blank_values=True)]
    url = urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),u.fragment))
    return dict(url=url,method=first['method'],headers=headers,body=urlencode(fields).encode())


def assemble_browser_second(packet, expected_prompt):
    fields=dict(parse_qsl(packet['body'].decode(),keep_blank_values=True))
    required=['conversationState','prompt','userMessageId','assistantMessageId',
              'chatRequirementsToken','proofToken','turnstileToken','oai-session-id']
    if any(name not in fields for name in required):
        raise RuntimeError('Second request is missing required fields')
    if fields['prompt']!=expected_prompt:
        raise RuntimeError('Second request prompt does not match')
    state=json.loads(fields['conversationState'])
    if not state.get('backendConversationId') or not state.get('parentMessageId'):
        raise RuntimeError('Second request has no conversation continuation')
    headers={k:v for k,v in packet['headers'].items()
             if not k.startswith(':') and k not in ('cookie','host','content-length')}
    headers['accept-encoding']='identity'
    return dict(url=packet['url'],method='POST',headers=headers,body=urlencode(fields).encode())


async def browser_assisted(timeout,prompt):
    out=ROOT/'artifacts'/('second-assisted-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    start=time.monotonic()
    def log(event,**fields):
        row=dict(seconds=round(time.monotonic()-start,3),event=event,**fields)
        with (out/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(row,ensure_ascii=False),flush=True)
    captured={}
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(ROOT/'profile'),headless=False,
            viewport={'width':1280,'height':900},service_workers='block')
        browser=context.browser
        page=context.pages[0] if context.pages else await context.new_page()
        first_requests=[]
        tasks=set()
        async def observe_first(request):
            if request.method=='POST' and urlsplit(request.url).path=='/unauth-mweb/conversation/updates':
                first_requests.append(dict(parse_qsl(request.post_data or '',keep_blank_values=True)))
        def on_request(request):
            task=asyncio.create_task(observe_first(request));tasks.add(task);task.add_done_callback(tasks.discard)
        page.on('request',on_request)
        log('browser_open',headless=False,version=browser.version,mode='browser-assisted credentials')
        try:
            await page.goto('https://chatgpt.com',wait_until='domcontentloaded',timeout=timeout*1000)
            composer=page.locator('#mobile-composer-prompt')
            await composer.wait_for(state='visible',timeout=timeout*1000)
            await composer.fill('嗨')
            await page.wait_for_function('()=>{let b=document.querySelector("button[data-composer-submit]");return b && !b.disabled && b.getAttribute("aria-disabled")!=="true";}',timeout=timeout*1000)
            await composer.press('Enter')
            await page.wait_for_function('()=>{const e=document.querySelector("[data-message-role=assistant]");return e?.hasAttribute("data-message-complete") && e.querySelector("[data-assistant-markdown-root]")?.innerText.trim();}',timeout=timeout*1000)
            await page.locator('[data-composer-stop-icon]').first.wait_for(state='hidden',timeout=timeout*1000)
            await asyncio.sleep(1)
            answer=await page.locator('[data-message-role="assistant"]').last.locator('[data-assistant-markdown-root]').inner_text()
            if tasks:
                await asyncio.gather(*tasks)
            if len(first_requests)!=1 or not answer.strip():
                raise RuntimeError('First browser turn did not complete')
            log('reply_complete',turn=1,prompt='嗨',answer=answer,transport='browser')
            await page.screenshot(path=str(out/'first-complete.png'))
            page.remove_listener('request',on_request)
            ready=asyncio.get_running_loop().create_future()
            async def intercept(route):
                request=route.request
                captured.update(url=request.url,body=request.post_data_buffer,headers=await request.all_headers())
                await route.abort()
                log('second_request_captured_and_aborted',generation_request_sent=False)
                ready.set_result(True)
            await page.route(lambda url:urlsplit(str(url)).path=='/unauth-mweb/conversation/updates',intercept)
            await composer.fill(prompt)
            await page.wait_for_function('()=>{let b=document.querySelector("button[data-composer-submit]");return b && !b.disabled && b.getAttribute("aria-disabled")!=="true";}',timeout=timeout*1000)
            await composer.press('Enter')
            await asyncio.wait_for(ready,timeout=timeout)
            fields=dict(parse_qsl(captured['body'].decode(),keep_blank_values=True))
            log('second_information_ready',field_names=list(fields),
                changed_fields=[k for k,v in fields.items() if first_requests[0].get(k)!=v],
                provider='normal browser Sentinel and conversation components')
        except Exception as e:
            log('browser_preparation_failed',error=type(e).__name__)
            await page.screenshot(path=str(out/'preparation-failed.png'))
            raise
        finally:
            await context.close()
            log('browser_closed',browser_connected=browser.is_connected())
            if browser.is_connected():
                raise RuntimeError('Browser still connected')
    log('playwright_stopped')
    packet=assemble_browser_second(captured,prompt)
    jar=SimpleCookie();jar.load(captured['headers'].get('cookie',''))
    cookie_jar=httpx.Cookies()
    for name,morsel in jar.items():
        cookie_jar.set(name,morsel.value,domain='chatgpt.com',path='/')
    log('python_second_request_assembled',prompt=prompt,browser_running=False,
        credential_values_saved=False)
    async with httpx.AsyncClient(cookies=cookie_jar,follow_redirects=False) as client:
        result=await receive(client,packet,timeout,2,out,log)
    log('run_complete',completed=1+int(result['success']),requested=2,
        second_http_only_after_browser_closed=result['success'])
    return result['success']


async def renew_sentinel(client,first,second,timeout,log):
    fields=dict(parse_qsl(first['body'].decode(),keep_blank_values=True))
    exchanges=first.get('sentinel_exchanges',[])
    final=next((x for x in exchanges if x['path'].endswith('/finalize') and x['response'].get('token')==fields.get('chatRequirementsToken')),None)
    original=next((x for x in exchanges if final and x['path'].endswith('/prepare') and x['response'].get('prepare_token')==final['body'].get('prepare_token')),None)
    if original is None:
        log('sentinel_refresh_unavailable',reason='no_matching_prepare_exchange')
        return False
    headers=dict(original['headers']);headers['accept-encoding']='identity'
    response=await client.post(original['url'],json=original['body'],headers=headers,timeout=timeout)
    log('sentinel_prepare_response',status=response.status_code,content_type=response.headers.get('content-type'))
    if response.status_code!=200 or 'application/json' not in response.headers.get('content-type',''):
        return False
    requirements=response.json()
    old=original['response']
    proof_matches=old.get('proofofwork')==requirements.get('proofofwork')
    turnstile_matches=old.get('turnstile')==requirements.get('turnstile')
    log('sentinel_requirement_comparison',proof_requirements_same=proof_matches,turnstile_requirements_same=turnstile_matches,
        proof_required=bool(requirements.get('proofofwork',{}).get('required')),
        turnstile_required=bool(requirements.get('turnstile',{}).get('required')))
    if not proof_matches or not turnstile_matches:
        log('sentinel_refresh_unavailable',reason='new_enforcement_requires_browser_component')
        return False
    final_body=dict(final['body']);final_body['prepare_token']=requirements['prepare_token']
    headers=dict(final['headers']);headers['accept-encoding']='identity'
    response=await client.post(final['url'],json=final_body,headers=headers,timeout=timeout)
    log('sentinel_finalize_response',status=response.status_code,content_type=response.headers.get('content-type'))
    if response.status_code!=200 or 'application/json' not in response.headers.get('content-type',''):
        return False
    token=response.json().get('token')
    if not token:
        return False
    fields=dict(parse_qsl(second['body'].decode(),keep_blank_values=True))
    fields['chatRequirementsToken']=token
    second['body']=urlencode(fields).encode()
    log('sentinel_refresh_complete',token_saved_to_disk=False)
    return True


async def run(timeout, prompt, renew):
    out = ROOT/'artifacts'/('second-turn-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    started=time.monotonic()
    def log(event,**fields):
        row=dict(seconds=round(time.monotonic()-started,3),event=event,**fields)
        with (out/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(row,ensure_ascii=False),flush=True)
    recording=json.loads((ROOT/'recording.json').read_text(encoding='utf-8'))
    first=await capture(recording,timeout,out,log)
    jar=SimpleCookie();jar.load(first['headers'].get('cookie',''))
    cookie_jar=httpx.Cookies()
    for name,morsel in jar.items():
        cookie_jar.set(name,morsel.value,domain='chatgpt.com',path='/')
    first['headers'].pop('cookie',None)
    first['headers']['accept-encoding']='identity'
    async with httpx.AsyncClient(cookies=cookie_jar,follow_redirects=False) as client:
        result=await receive(client,first,timeout,1,out,log)
        if not result['success']:
            log('run_complete',completed=0,requested=2)
            return False
        state=updated_state(result['records'])
        second=followup_request(first,state,prompt)
        prep=first.get('prepare')
        if prep:
            prep_fields=dict(parse_qsl(prep['body'].decode(),keep_blank_values=True))
            prep_fields['conversationState']=json.dumps(state,ensure_ascii=False,separators=(',',':'))
            prep_headers={k:v for k,v in prep['headers'].items() if k not in ('cookie','content-length')}
            prep_headers['accept-encoding']='identity'
            response=await client.request(prep['method'],prep['url'],content=urlencode(prep_fields).encode(),headers=prep_headers,timeout=timeout)
            log('prepare_response',status=response.status_code,content_type=response.headers.get('content-type'))
            if response.status_code!=200 or 'application/json' not in response.headers.get('content-type',''):
                log('run_complete',completed=1,requested=2,reason='prepare_failed')
                return False
            token=response.json().get('conduit_token')
            if token:
                second['headers']['x-conduit-token']=token
        else:
            second['headers'].pop('x-conduit-token',None)
        if renew and not await renew_sentinel(client,first,second,timeout,log):
            log('run_complete',completed=1,requested=2,reason='fresh_sentinel_unavailable')
            return False
        log('second_request_prepared',prompt=prompt,browser_reopened=False,
            changes=['state with safety','pending-prefixed assistant ID','new user/operation/trace IDs','HTTP cookie jar','fresh conduit when available'],
            sentinel_tokens='reused; no regeneration claimed')
        result=await receive(client,second,timeout,2,out,log)
        log('run_complete',completed=1+int(result['success']),requested=2,
            second_success=result['success'],status=result['status'])
        return result['success']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=int,default=90)
    parser.add_argument('--prompt',default='秋天天氣真好')
    parser.add_argument('--renew-sentinel',action='store_true')
    parser.add_argument('--mode',choices=['detached','browser-assisted'],default='browser-assisted')
    args=parser.parse_args()
    if not 10<=args.timeout<=900 or not args.prompt.strip():
        parser.error('timeout must be 10..900 seconds; prompt must be nonempty')
    operation=browser_assisted(args.timeout,args.prompt) if args.mode=='browser-assisted' else run(args.timeout,args.prompt,args.renew_sentinel)
    raise SystemExit(0 if asyncio.run(operation) else 1)
