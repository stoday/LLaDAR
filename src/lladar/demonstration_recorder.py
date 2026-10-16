"""One user demonstration, durable observations, scoped state, then shutdown."""
from __future__ import annotations

import builtins
from contextvars import copy_context
import hashlib
import json
from pathlib import Path
import queue
import threading
import time

from .browser_discovery_driver import BrowserDiscoveryError, GenericBrowserDriver
from .browser_target import _wire_contains_question
from .independent_adapter import IndependentAdapterError, origin
from .recording_store import RecordingStore
from .resource_limits import resource_limit
from .language import tr
from .live_status import read_user_input, status_user_input
from .work_session import atomic_json, digest


def demonstration_search_text(question):
    """Use a legacy tag when present; otherwise match the exact visible question."""
    start = question.rfind('[DEMO-')
    if start >= 0 and question.endswith(']'):
        return question[start:]
    return question


class DemonstrationRecorder:
    def __init__(self, *, page_url, workspace, profile_dir=None, headless=False):
        self.workspace = Path(workspace)
        self.store = RecordingStore(self.workspace / 'recording')
        self.browser = GenericBrowserDriver(page_url=page_url,
            profile_dir=profile_dir or self.workspace / 'browser-profile', headless=headless,
            timeout=resource_limit('MANUAL_VERIFICATION_TIMEOUT_SECONDS'), recording_store=self.store)
        self.page_url = page_url
        self.closed = False
        self.question = ''

    @status_user_input()
    def prompt(self, message, input_fn):
        """The browser owner keeps pumping while terminal input waits."""
        if input_fn is not builtins.input:
            return read_user_input(message, input_fn=input_fn)
        replies = queue.Queue()
        def read():
            try:
                replies.put((True, read_user_input(message, input_fn=input_fn)))
            except BaseException as error:
                replies.put((False, error))
        threading.Thread(target=copy_context().run, args=(read,), daemon=True).start()
        deadline = time.monotonic() + resource_limit('MANUAL_VERIFICATION_TIMEOUT_SECONDS')
        while replies.empty():
            if time.monotonic() >= deadline:
                raise IndependentAdapterError('demonstration_timeout')
            self.browser.page.wait_for_timeout(100)
        ok, value = replies.get()
        if not ok:
            raise value
        return value

    def capture(self, question, input_fn, output_fn):
        self.question = question
        output_fn(tr('Complete demonstration question (use it after the browser opens):'))
        output_fn(question)
        with self.browser.manual_interaction():
            try:
                self.browser.open()
                if self.browser._overflow:
                    raise IndependentAdapterError('recording_capture_limit')
                output_fn(tr('Please operate the browser: sign in, finish verification, enter the service, open a new conversation, and submit the complete demonstration question below.'))
                output_fn(question)
                marker = demonstration_search_text(question)
                while True:
                    self.prompt(tr('Wait for the complete answer, then return to this terminal and press Enter. Do not close the browser yet; the program saves state and closes it. Ctrl+C cancels: '), input_fn)
                    sends = []
                    # Native callbacks can still be queued when terminal input arrives.
                    deadline = time.monotonic() + 10
                    while not sends and time.monotonic() < deadline:
                        self.browser.page.wait_for_timeout(250)
                        if self.browser._overflow:
                            raise IndependentAdapterError('recording_capture_limit')
                        batch = self.store.query_recording(query=marker, limit=200)
                        while True:
                            sends.extend(e for e in batch['events'] if
                                e['kind'] in {'http_request', 'playwright_http_request', 'websocket_sent'} and
                                _wire_contains_question(self.store.event(e['event_id']), marker))
                            if not batch['has_more']:
                                break
                            batch = self.store.query_recording(query=marker, after=batch['next_after'], limit=200)
                    if sends:
                        break
                    output_fn(tr('No recorded submission matches the complete demonstration question. The recording browser remains open; no Agent or automatic question has started.'))
                    for page in self.browser.context.pages:
                        if page.url.startswith(('https://', 'http://')):
                            output_fn(f'{self.browser._page_id(page)}: {origin(page.url)}')
                    output_fn(tr('Check that the conversation is in this recording browser and that the complete question including its marker was sent. Press Enter to check again, or Ctrl+C to cancel.'))
                page_ids = {e['page_id'] for e in sends}
                pages = [(page, self.browser._page_id(page)) for page in self.browser.context.pages
                         if self.browser._page_id(page) in page_ids]
                if len(pages) != 1:
                    for i, (page, _) in enumerate(pages):
                        output_fn(f'{i + 1}. {page.title()} {page.url}')
                    selection = int(self.prompt(tr('Multiple demonstration tabs match. Enter the conversation tab number: '), input_fn)) - 1
                    selected, page_id = pages[selection]
                else:
                    selected, page_id = pages[0]
                allowed = {origin(self.page_url), origin(selected.url)}
                events = self.store.query_recording(page_id=page_id, limit=200)
                while True:
                    for item in events['events']:
                        if item['kind'] in {'http_request', 'websocket_created'}:
                            event = self.store.event(item['event_id'])
                            if event.get('resource_type') in {'fetch', 'xhr'} or item['kind'] == 'websocket_created':
                                allowed.add(origin(event['url']))
                    if not events['has_more']:
                        break
                    events = self.store.query_recording(page_id=page_id, after=events['next_after'], limit=200)
                snapshot = selected.locator('body').inner_text()
                self.store.append({'kind': 'demonstration_final_snapshot', 'page_id': page_id,
                                   'text': snapshot, 'question': question, 'manual_complete': True})
                try:
                    inspected = self.browser.inspect()
                    final_dom = {'kind': 'demonstration_final_dom', 'page_id': page_id,
                                 'frames': [frame for frame in inspected['frames']
                                            if frame.get('page_id') == page_id],
                                 'manual_complete': True}
                except BrowserDiscoveryError as error:
                    final_dom = {'kind': 'demonstration_final_dom', 'page_id': page_id,
                                 'frames': [], 'unavailable': error.reason}
                try:
                    self.store.append(final_dom)
                except ValueError as error:
                    if str(error) != 'recording_capture_limit':
                        raise
                    self.store.append({'kind': 'demonstration_final_dom',
                                       'page_id': page_id, 'frames': [],
                                       'unavailable': 'recording_capture_limit'})
                storage = self.browser.context.storage_state(indexed_db=True)
                storage['origins'] = [item for item in storage.get('origins', []) if origin(item['origin']) in allowed]
                storage['cookies'] = [c for c in storage['cookies'] if any(
                    host.split('://', 1)[1].rsplit(':', 1)[0] == c['domain'].lstrip('.') or
                    host.split('://', 1)[1].rsplit(':', 1)[0].endswith('.' + c['domain'].lstrip('.')) for host in allowed)]
                storage['session_storage'] = []
                for page in self.browser.context.pages:
                    for frame in page.frames:
                        if frame.url.startswith(('https://', 'http://')) and origin(frame.url) in allowed:
                            storage['session_storage'].append({'origin': origin(frame.url),
                                'items': frame.evaluate('Object.fromEntries(Object.entries(sessionStorage))')})
                storage.update(page_url=selected.url, captured_at=time.time(), allowed_origins=sorted(allowed))
                atomic_json(self.workspace / 'state' / 'initial.json', storage)
                atomic_json(self.workspace / 'state' / 'initial.integrity.json', {'sha256': digest(storage)})
                atomic_json(self.workspace / 'recording' / 'demonstration.json', {
                    'question': question, 'page_id': page_id, 'submission_refs': [e['event_id'] for e in sends],
                    'snapshot_sha256': hashlib.sha256(snapshot.encode()).hexdigest()})
                for url, resource in self.browser._resources.items():
                    if origin(url) in allowed and resource['event_id']:
                        try:
                            self.store.append({'kind': 'client_resource', 'url': url,
                                'source_event_id': resource['event_id'], 'page_id': page_id,
                                'content_type': resource.get('content_type', ''),
                                'bytes': resource.get('bytes', 0)})
                        except ValueError as error:
                            if str(error) != 'recording_capture_limit':
                                raise
                            break
                return storage
            finally:
                self.close()

    def close(self):
        self.browser.close(strict=True)
        self.closed = self.browser.context is None
        atomic_json(self.workspace / 'recording' / 'shutdown.json', {'closed': self.closed, 'time': time.time()})

    def recover_completed_capture(self, state):
        """Finish metadata for a recorded, manually completed demo after a save failure."""
        if not (self.workspace / 'state' / 'initial.json').is_file():
            raise IndependentAdapterError('demonstration_required')
        latest = None
        batch = self.store.query_recording(kind='demonstration_final_snapshot', limit=200)
        while True:
            for item in batch['events']:
                event = self.store.event(item['event_id'])
                if event.get('manual_complete') is True and event.get('question'):
                    latest = event
            if not batch['has_more']:
                break
            batch = self.store.query_recording(kind='demonstration_final_snapshot',
                after=batch['next_after'], limit=200)
        if latest is None or latest.get('page_id') is None:
            raise IndependentAdapterError('demonstration_required')
        question = latest['question']
        marker = demonstration_search_text(question)
        if not marker.strip():
            raise IndependentAdapterError('demonstration_required')
        sends = []
        batch = self.store.query_recording(query=marker, limit=200)
        while True:
            sends.extend(item['event_id'] for item in batch['events'] if
                item['page_id'] == latest['page_id'] and
                item['kind'] in {'http_request', 'playwright_http_request', 'websocket_sent'} and
                _wire_contains_question(self.store.event(item['event_id']), marker))
            if not batch['has_more']:
                break
            batch = self.store.query_recording(query=marker, after=batch['next_after'], limit=200)
        if not sends or origin(state.get('page_url', '')) not in set(state.get('allowed_origins', [])):
            raise IndependentAdapterError('demonstration_required')
        metadata = {'question': question, 'page_id': latest['page_id'],
            'submission_refs': sends,
            'snapshot_sha256': hashlib.sha256(latest['text'].encode()).hexdigest()}
        atomic_json(self.workspace / 'recording' / 'demonstration.json', metadata)
        return metadata

    def refresh_state(self, previous, input_fn, output_fn):
        """Human sign-in only; never repeat a demonstration or submitted question."""
        allowed = set(previous['allowed_origins'])
        with self.browser.manual_interaction():
            try:
                self.browser.open(previous['page_url'])
                output_fn(tr('Authentication expired. Dispatch is paused; a temporary browser is open.'))
                self.prompt(tr('Sign in again and return to the service. Press Enter when ready; the program saves updated state and closes the browser. Do not send the question again: '), input_fn)
                storage = self.browser.context.storage_state(indexed_db=True)
                from urllib.parse import urlsplit
                hosts = {urlsplit(url).hostname for url in allowed}
                storage['cookies'] = [cookie for cookie in storage['cookies'] if any(
                    host == cookie['domain'].lstrip('.') or host.endswith('.' + cookie['domain'].lstrip('.')) for host in hosts)]
                storage['origins'] = [item for item in storage['origins'] if origin(item['origin']) in allowed]
                updated = {**previous, **storage, 'captured_at': time.time()}
                updated['session_storage'] = []
                for page in self.browser.context.pages:
                    for frame in page.frames:
                        if frame.url.startswith(('http://','https://')) and origin(frame.url) in allowed:
                            updated['session_storage'].append({'origin': origin(frame.url),
                                'items': frame.evaluate('Object.fromEntries(Object.entries(sessionStorage))')})
                atomic_json(self.workspace / 'state' / 'initial.json', updated)
                atomic_json(self.workspace / 'state' / 'initial.integrity.json', {'sha256': digest(updated)})
                return updated
            finally:
                self.close()
