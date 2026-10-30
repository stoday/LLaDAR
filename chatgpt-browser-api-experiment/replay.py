"""Replay the observed endpoint with fresh browser-generated session prerequisites."""
import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent


def parse_stream(body):
    records = [json.loads(line) for line in body.splitlines() if line.strip()]
    failures = [r.get('kind') for r in records if r.get('type') == 'diagnostic']
    if failures:
        raise RuntimeError('API diagnostic: ' + ', '.join(str(x) for x in failures))
    if not records or records[-1].get('type') != 'end':
        raise RuntimeError('NDJSON stream has no final end record')
    return records


async def run(count, timeout):
    recording = json.loads((ROOT / 'recording.json').read_text(encoding='utf-8'))
    out = ROOT / 'artifacts' / ('replay-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True)
    start = time.monotonic()
    def log(event, **fields):
        row = {'seconds': round(time.monotonic() - start, 3), 'event': event, **fields}
        with (out / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(json.dumps(row, ensure_ascii=False), flush=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(ROOT / 'profile'), headless=False, viewport={'width': 1280, 'height': 900},
            service_workers='block')
        page = context.pages[0] if context.pages else await context.new_page()
        log('browser_open', headless=False, version=context.browser.version)
        try:
            for turn in range(1, count + 1):
                # A fresh page generates the current form state and Sentinel prerequisites.
                await page.goto(recording['url'], wait_until='domcontentloaded', timeout=timeout*1000)
                composer = page.locator(recording['composer'])
                await composer.wait_for(state='visible', timeout=timeout*1000)
                result = asyncio.get_running_loop().create_future()

                async def submit_via_api(route):
                    request = route.request
                    try:
                        if request.method != 'POST' or urlsplit(request.url).path != recording['endpoint_path']:
                            raise RuntimeError('Unexpected submission route')
                        log('api_submit', turn=turn, method=request.method,
                            path=recording['endpoint_path'], request_encoding=request.headers.get('content-type'))
                        # route.fetch uses Playwright's HTTP API with the original fresh body.
                        # Session credentials remain inside this browser context.
                        response = await route.fetch(timeout=timeout*1000, max_redirects=0)
                        body = await response.text()
                        if response.status != 200:
                            raise RuntimeError(f'API HTTP {response.status}')
                        if recording['content_type'] not in response.headers.get('content-type', ''):
                            raise RuntimeError('Unexpected API response content type')
                        records = parse_stream(body)
                        log('api_stream_complete', turn=turn, status=response.status,
                            records=len(records), types=list(dict.fromkeys(r.get('type') for r in records)),
                            end_type=records[-1]['type'])
                        await route.fulfill(response=response)
                        result.set_result(True)
                    except Exception as e:
                        if not result.done():
                            result.set_exception(e)
                        await route.abort()

                matches_endpoint = lambda url: urlsplit(str(url)).path == recording['endpoint_path']
                await page.route(matches_endpoint, submit_via_api)
                await composer.fill(recording['prompt'])
                log('prompt_filled', turn=turn, prompt=recording['prompt'])
                await composer.press('Enter')
                await asyncio.wait_for(result, timeout=timeout + 5)
                answer = page.locator(recording['assistant']).last
                await answer.wait_for(state='visible', timeout=timeout*1000)
                await page.wait_for_function(
                    's => {let els=document.querySelectorAll(s);let el=els[els.length-1];let md=el?.querySelector("[data-assistant-markdown-root]");return el && el.hasAttribute("data-message-complete") && md && md.innerText.trim().length>0}',
                    arg=recording['assistant'], timeout=timeout*1000)
                text = await answer.locator('[data-assistant-markdown-root]').inner_text()
                log('reply_complete', turn=turn, answer=text,
                    completion_evidence='API final end record + DOM data-message-complete')
                await page.screenshot(path=str(out / f'turn-{turn}-complete.png'))
                await page.unroute(matches_endpoint, submit_via_api)
            log('run_complete', completed=count)
        except Exception as e:
            log('run_failed', error=type(e).__name__, detail=str(e)[:300])
            await page.screenshot(path=str(out / 'failed.png'))
            raise
        finally:
            await context.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.count <= 20 or not 10 <= args.timeout <= 900:
        parser.error('count must be 1..20; timeout must be 10..900 seconds')
    asyncio.run(run(args.count, args.timeout))
