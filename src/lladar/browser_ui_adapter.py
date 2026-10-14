"""A site-configured browser adapter for fresh question submission and answers.

The browser opens for one turn and closes after extracting the response. The
site's own client performs any per-turn setup; this module never replays a
recorded challenge, credential, or demonstration question.
"""
from __future__ import annotations

import argparse
import json
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


class BrowserUiAdapter:
    """Run a recording-derived UI plan against a saved browser profile."""

    def __init__(self, plan: dict):
        self.plan = dict(plan)
        required = ('page_url', 'profile_dir', 'input_selector', 'submit', 'answer_selector')
        missing = [key for key in required if not self.plan.get(key)]
        if missing:
            raise ValueError('browser_ui_plan_missing: ' + ', '.join(missing))
        if urlsplit(self.plan['page_url']).scheme not in {'http', 'https'}:
            raise ValueError('browser_ui_url_invalid')
        if self.plan['submit'].get('kind') not in {'click', 'press'}:
            raise ValueError('browser_ui_submit_invalid')
        opener = self.plan.get('open')
        if opener is not None and (type(opener) is not dict or
                                   opener.get('kind') != 'click' or
                                   not isinstance(opener.get('selector'), str) or
                                   not opener['selector']):
            raise ValueError('browser_ui_open_invalid')
        retry = self.plan.get('retry')
        if retry is not None and (type(retry) is not dict or
                                  retry.get('method') != 'POST' or
                                  not isinstance(retry.get('path'), str) or
                                  not retry['path'].startswith('/') or
                                  not isinstance(retry.get('form_question_key'), str) or
                                  not retry['form_question_key'] or
                                  type(retry.get('max_attempts')) is not int or
                                  retry['max_attempts'] != 2 or
                                  type(retry.get('statuses')) is not list or
                                  not retry['statuses'] or
                                  any(type(status) is not int or
                                      not 400 <= status < 500
                                      for status in retry['statuses'])):
            raise ValueError('browser_ui_retry_invalid')

    def _matches_confirmation(self, response, question: str,
                              expected: dict | None = None) -> bool:
        expected = expected or self.plan.get('confirmation') or {}
        if response.request.method != expected.get('method', 'POST'):
            return False
        if urlsplit(response.url).path != expected.get('path'):
            return False
        key = expected.get('form_question_key')
        if not key:
            return True
        body = response.request.post_data or ''
        try:
            posted = parse_qs(body, keep_blank_values=True).get(key, [])
            if not posted and body.startswith('{'):
                value = json.loads(body).get(key)
                posted = [value] if isinstance(value, str) else []
        except ValueError:
            posted = []
        return question in posted

    def run(self, question: str) -> dict:
        if not isinstance(question, str) or not question.strip():
            raise ValueError('browser_ui_question_invalid')
        timeout_ms = int(self.plan.get('timeout_seconds', 90) * 1000)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                self.plan['profile_dir'], headless=self.plan.get('headless', False),
                timeout=min(timeout_ms, 30000))
            try:
                page = context.pages[0] if context.pages else context.new_page()
                post_responses = []
                page.on('response', lambda response: post_responses.append(response)
                        if response.request.method == 'POST' else None)
                page.goto(self.plan['page_url'], wait_until='domcontentloaded',
                          timeout=timeout_ms)
                if self.plan.get('open'):
                    page.locator(self.plan['open']['selector']).click(timeout=timeout_ms)
                composer = page.locator(self.plan['input_selector'])
                composer.wait_for(timeout=timeout_ms)
                answers = page.locator(self.plan['answer_selector'])
                previous_count = answers.count()
                previous_answer = (answers.last.inner_text().strip()
                                   if previous_count else '')
                submit = self.plan['submit']

                def send():
                    if submit['kind'] == 'press':
                        composer.press(submit.get('key', 'Enter'))
                    else:
                        page.locator(submit['selector']).click()

                response_status = None
                retry = self.plan.get('retry')
                attempted_statuses = []
                for attempt in range(retry['max_attempts'] if retry else 1):
                    if attempt and callable(self.plan.get('on_retry')):
                        self.plan['on_retry']()
                    composer.fill(question)
                    expectation = retry or self.plan.get('confirmation')
                    if expectation:
                        with page.expect_response(
                                lambda response: self._matches_confirmation(
                                    response, question, expectation),
                                timeout=timeout_ms) as receipt:
                            send()
                        response = receipt.value
                        response_status = response.status
                        attempted_statuses.append(response_status)
                        if (retry and response_status in retry['statuses'] and
                                attempt + 1 < retry['max_attempts']):
                            page.wait_for_timeout(500)
                            continue
                        if not 200 <= response_status < 300:
                            raise RuntimeError('browser_ui_submission_http_error: ' +
                                               json.dumps({'status': response_status,
                                                   'path': urlsplit(response.url).path,
                                                   'body': response.text()[:1200],
                                                   'attempted_statuses': attempted_statuses},
                                                   ensure_ascii=False))
                    else:
                        send()
                    break

                deadline = time.monotonic() + timeout_ms / 1000
                stable_seconds = float(self.plan.get('stable_seconds', 4))
                last_text, stable_since = '', None
                while time.monotonic() < deadline:
                    current_count = answers.count()
                    text = answers.last.inner_text().strip() if current_count else ''
                    fresh = text and (current_count > previous_count or
                                      text != previous_answer)
                    now = time.monotonic()
                    if fresh and text == last_text:
                        if stable_since is not None and now - stable_since >= stable_seconds:
                            return {
                                'answer': text,
                                'question_visible': question in page.locator('body').inner_text(),
                                'submission_status': response_status,
                                'reference_url': page.url,
                                'reference_path': urlsplit(page.url).path,
                                'completion_signal': 'new_rendered_answer_stable',
                            }
                    else:
                        last_text, stable_since = text, now
                    page.wait_for_timeout(200)
                recent_posts = []
                for response in post_responses[-10:]:
                    item = {'path': urlsplit(response.url).path,
                            'status': response.status}
                    if response.status >= 400:
                        try:
                            item['body'] = response.text()[:1200]
                        except Exception:
                            item['body'] = None
                    recent_posts.append(item)
                detail = {'post_responses': recent_posts,
                          'answer_count': answers.count(),
                          'last_answer': last_text[:500],
                          'visible_text': page.locator('body').inner_text()[-1200:]}
                raise RuntimeError('browser_ui_answer_timeout: ' +
                                   json.dumps(detail, ensure_ascii=False))
            finally:
                context.close()


def main():
    parser = argparse.ArgumentParser(description='Run one saved-profile browser UI adapter turn.')
    parser.add_argument('--config', required=True, help='Recording-derived adapter JSON')
    parser.add_argument('question', help='New question to submit')
    args = parser.parse_args()
    with open(args.config, encoding='utf-8') as stream:
        plan = json.load(stream)
    print(json.dumps(BrowserUiAdapter(plan).run(args.question), ensure_ascii=False))


if __name__ == '__main__':
    main()
