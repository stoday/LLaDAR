import asyncio
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts'


def shape(value, depth=0):
    if depth > 5:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__


async def main():
    OUT.mkdir(exist_ok=True)
    start = time.monotonic()
    def log(event, **data):
        row = dict(seconds=round(time.monotonic()-start, 3), event=event, **data)
        with (OUT / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(json.dumps(row, ensure_ascii=False), flush=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(ROOT / 'profile'), headless=False, viewport={'width': 1280, 'height': 900})
        page = context.pages[0] if context.pages else await context.new_page()
        tasks = set()
        captured = {}
        async def response(r):
            u = urlsplit(r.url)
            if u.hostname != 'chatgpt.com' or r.request.resource_type not in ('fetch', 'xhr'):
                return
            data = dict(method=r.request.method, path=u.path, status=r.status,
                        content_type=r.headers.get('content-type', ''))
            try:
                payload = r.request.post_data_json
                if payload is not None:
                    data['request_shape'] = shape(payload)
            except Exception:
                pass
            log('network_response', **data)
            if u.path == '/unauth-mweb/conversation/updates':
                captured['url'] = r.url
                captured['payload'] = r.request.post_data_json
                captured['headers'] = {k: v for k, v in (await r.request.all_headers()).items()
                                       if not k.startswith(':') and k not in ('cookie', 'content-length', 'host')}
                log('api_request_encoding', content_type=captured['headers'].get('content-type'))
            if 'conversation' in u.path and r.request.method == 'POST':
                try:
                    body = await r.text()
                    # Only schema and protocol markers; never save raw request headers/tokens.
                    markers = [line[:160] for line in body.splitlines()
                               if line.startswith('event:') or line == 'data: [DONE]']
                    log('conversation_body_complete', path=u.path, bytes=len(body), markers=markers[-30:])
                    (OUT / 'stream-schema.json').write_text(json.dumps(
                        [shape(json.loads(line[6:])) for line in body.splitlines()
                         if line.startswith('data: {')][:15], ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception as e:
                    log('body_unavailable', error=type(e).__name__)
        def schedule(r):
            task = asyncio.create_task(response(r))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        page.on('response', schedule)
        log('browser_open', headless=False, browser_version=context.browser.version)
        try:
            await page.goto('https://chatgpt.com', wait_until='domcontentloaded', timeout=120000)
            log('navigation_complete', title=await page.title())
            await page.screenshot(path=str(OUT / 'opened.png'))
            (OUT / 'initial-dom.json').write_text(json.dumps(await page.locator(
                'button,textarea,[contenteditable="true"],input').evaluate_all(
                '(els)=>els.map(e=>({tag:e.tagName,id:e.id,role:e.getAttribute("role"),label:e.getAttribute("aria-label"),placeholder:e.getAttribute("placeholder"),text:e.tagName==="BUTTON"?e.innerText.slice(0,100):null}))'),
                ensure_ascii=False, indent=2), encoding='utf-8')
            log('waiting_for_composer', instruction='If login or human verification appears, complete it in the visible browser.')
            composer = page.locator('#mobile-composer-prompt, #prompt-textarea').filter(visible=True).first
            await composer.wait_for(state='visible', timeout=900000)
            await composer.fill('嗨')
            await page.screenshot(path=str(OUT / 'typed.png'))
            log('prompt_filled', selector=await composer.get_attribute('id'), prompt='嗨')
            await composer.press('Enter')
            log('prompt_submitted')
            await asyncio.sleep(10)
            await page.screenshot(path=str(OUT / 'after-submit.png'))
            (OUT / 'after-submit-dom.json').write_text(json.dumps(await page.locator('main').evaluate_all('(els)=>els.map(e=>e.outerHTML)'), ensure_ascii=False, indent=2), encoding='utf-8')
            answer = page.locator('[data-message-role="assistant"], [data-message-author-role="assistant"]').last
            await answer.wait_for(state='visible', timeout=180000)
            previous, stable = '', 0
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                text = await answer.inner_text()
                complete = await answer.get_attribute('data-message-complete')
                stop = await page.locator('[data-testid="stop-button"]:visible, [data-composer-primary="stop"]:visible').count()
                stable = stable + 1 if text and text == previous and not stop else 0
                previous = text
                if stable >= 5 and complete in (None, 'true', ''):
                    log('reply_complete', answer=text, completion_evidence='data-message-complete='+str(complete)+'; text stable 5 seconds; no stop button')
                    await page.screenshot(path=str(OUT / 'completed.png'))
                    (OUT / 'final-dom.json').write_text(json.dumps(await page.locator(
                        'button,[data-message-author-role]').evaluate_all(
                        '(els)=>els.map(e=>({tag:e.tagName,testid:e.getAttribute("data-testid"),role:e.getAttribute("data-message-author-role"),label:e.getAttribute("aria-label"),text:e.innerText.slice(0,150)}))'), ensure_ascii=False, indent=2), encoding='utf-8')
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError('Reply did not finish within 180 seconds')
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if captured:
                payload = dict(captured['payload'])
                for key in ('assistantMessageId', 'userMessageId'):
                    payload[key] = str(uuid.uuid4())
                log('api_replay_started', path=urlsplit(captured['url']).path)
                encoding = {'form': payload} if 'application/x-www-form-urlencoded' in captured['headers'].get('content-type', '') else {'data': payload}
                r = await context.request.post(captured['url'], **encoding,
                                               headers=captured['headers'], timeout=180000)
                body = await r.text()
                content_type = r.headers.get('content-type', '')
                log('api_replay_response', status=r.status, content_type=content_type, bytes=len(body))
                lines = []
                for line in body.splitlines():
                    try:
                        lines.append(json.loads(line))
                    except ValueError:
                        pass
                (OUT / 'api-response-schema.json').write_text(json.dumps([shape(x) for x in lines[:15]], ensure_ascii=False, indent=2), encoding='utf-8')
                log('api_protocol', records=[{k:v for k,v in x.items() if k in ('type','kind','version')} for x in lines])
                html = '\n'.join(x.get('html','') for x in lines)
                safe_text = await page.evaluate('(html)=>{let d=new DOMParser().parseFromString(html,"text/html");d.querySelectorAll("script,input,style").forEach(e=>e.remove());return d.body.textContent.slice(0,1500)}', html)
                log('api_visible_text', text=safe_text)
        except Exception as e:
            log('blocked_or_failed', error=type(e).__name__, detail=str(e)[:350])
            await page.screenshot(path=str(OUT / 'blocked.png'))
            raise
        finally:
            await context.close()


if __name__ == '__main__':
    asyncio.run(main())
