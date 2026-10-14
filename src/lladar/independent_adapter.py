"""Agent-generated pure plans executed through host-owned website I/O."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, quote_plus, unquote, urljoin, urlsplit

import requests

from .protocol_runtime import ProtocolRuntime
from .resource_limits import resource_limit
from .work_session import atomic_json, digest

INDEPENDENT_REASONS = frozenset({
    'adapter_origin_invalid','adapter_origin_not_authorized','adapter_timeout','adapter_capture_limit',
    'adapter_authentication_required','adapter_http_status','adapter_action_invalid','adapter_evidence_invalid',
    'adapter_answer_invalid','adapter_context_invalid','adapter_source_invalid','adapter_execution_failed',
    'demonstration_timeout','demonstration_submission_missing','demonstration_required',
    'recording_capture_limit','recording_browser_not_closed','state_integrity_failed',
    'adapter_runtime_mismatch','adapter_integrity_failed','adapter_origin_mismatch',
    'submission_already_attempted','submission_unconfirmed','completion_unconfirmed',
    'request_identity_mismatch','checkpoint_integrity_failed','adapter_setup_incomplete',
    'adapter_not_qualified','adapter_not_submitted','adapter_evidence_outside_demonstration',
    'pending_turn_observation_unavailable','reference_mismatch','consent_required',
    'conversation_policy_violation',
})


class IndependentAdapterError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if reason in INDEPENDENT_REASONS else 'adapter_execution_failed'
        super().__init__(self.reason)


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https', 'ws', 'wss'} or not parsed.hostname or parsed.username or parsed.password:
        raise IndependentAdapterError('adapter_origin_invalid')
    scheme = {'ws': 'http', 'wss': 'https'}.get(parsed.scheme, parsed.scheme)
    port = parsed.port or (443 if scheme == 'https' else 80)
    return f'{scheme}://{parsed.hostname}:{port}'


def question_forms(value):
    """Host-prepared spellings for recorded templates; no site protocol is assumed."""
    if type(value) is not str:
        value = ''
    return {'raw': value,
            'urlencoded': quote(value, safe=''),
            'urlencoded_plus': quote_plus(value, safe=''),
            'json_escaped': json.dumps(value, ensure_ascii=False)[1:-1],
            'json_ascii_escaped': json.dumps(value, ensure_ascii=True)[1:-1],
            'utf8_b64': base64.b64encode(value.encode()).decode()}


class IndependentAdapter:
    """Public run/recover seam. Generated source has no environmental authority."""
    def __init__(self, *, source, state, allowed_origins, workspace, timeout=60,
                 conversation_policy='independent', debit=None, browser_profile_dir=None):
        self.source = source
        self.state = json.loads(json.dumps(state))
        self.allowed_origins = {origin(url) for url in allowed_origins}
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        saved_state = self.workspace / 'state.json'
        if saved_state.is_file():
            try:
                latest = json.loads(saved_state.read_text(encoding='utf-8'))
                integrity = json.loads(saved_state.with_suffix('.integrity.json').read_text(encoding='utf-8'))
                if integrity['sha256'] != digest(latest):
                    raise ValueError
                self.state = latest
            except (OSError, KeyError, ValueError):
                raise IndependentAdapterError('state_integrity_failed') from None
        self.timeout = timeout
        self.browser_profile_dir = (str(browser_profile_dir)
                                    if browser_profile_dir is not None else None)
        self.policy = conversation_policy
        self.debit = debit or (lambda: None)
        self.vm = ProtocolRuntime()
        self.http = requests.Session()
        self.http.trust_env = False
        for cookie in self.state.get('cookies', []):
            self.http.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', ''),
                                  path=cookie.get('path', '/'), secure=cookie.get('secure', False),
                                  expires=cookie.get('expires') if cookie.get('expires', -1) > 0 else None)
        self.sockets = {}
        self.context = {}
        self.last_diagnostic = None
        self.path = self.workspace / 'turns.json'
        self.turns = json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {}
        if self.path.is_file():
            try:
                integrity = json.loads(self.path.with_suffix('.integrity.json').read_text(encoding='utf-8'))
                if integrity['sha256'] != digest(self.turns):
                    raise ValueError
            except (OSError, KeyError, ValueError):
                self.http.close()
                raise IndependentAdapterError('checkpoint_integrity_failed') from None

    def turn(self, request_id):
        return json.loads(json.dumps(self.turns[request_id]))

    def _save(self):
        atomic_json(self.path, self.turns)
        atomic_json(self.path.with_suffix('.integrity.json'), {'sha256': digest(self.turns)})
        self.state['cookies'] = [dict(name=c.name, value=c.value, domain=c.domain, path=c.path,
                                     secure=c.secure, expires=c.expires or -1) for c in self.http.cookies]
        atomic_json(self.workspace / 'state.json', self.state)
        atomic_json(self.workspace / 'state.integrity.json', {'sha256': digest(self.state)})

    def _event(self, turn, kind, **value):
        event = dict(value, kind=kind, event_id=f"{turn['request_id']}-event-{len(turn['events']) + 1}",
                     request_id=turn['request_id'], monotonic_seconds=time.monotonic(), source='host_transport')
        turn['events'].append(event)
        if len(json.dumps(turn['events']).encode()) > resource_limit('MAX_CAPTURE_BYTES'):
            raise IndependentAdapterError('adapter_capture_limit')
        self._save()
        return event

    def _plan(self, phase, turn):
        plan = self.vm.execute(self.source, {'phase': phase, 'question': turn['question'],
            'request_id': turn['request_id'], 'role': turn['role'], 'state': self.state,
            'context': self.context, 'events': turn['events'], 'conversation_policy': self.policy,
            'question_forms': question_forms(turn['question']),
            'demonstration_forms': question_forms(self.state.get('demonstration_question'))})
        if 'context' in plan:
            if type(plan['context']) is not dict:
                raise IndependentAdapterError('adapter_context_invalid')
            self.context = plan['context']
            turn['context'] = self.context
        return plan

    def _actions(self, plan, turn, deadline, allow_submit):
        for action in plan.get('actions', []):
            if time.monotonic() >= deadline:
                raise IndependentAdapterError('adapter_timeout')
            kind = action.get('kind')
            submit = action.get('submit') is True
            url = action.get('url', '')
            if kind in {'http', 'ws_open'} and origin(url) not in self.allowed_origins:
                raise IndependentAdapterError('adapter_origin_not_authorized')
            if kind not in {'http', 'ws_open', 'ws_send', 'ws_receive', 'browser_ui'} or submit and kind not in {'http','ws_send', 'browser_ui'}:
                raise IndependentAdapterError('adapter_action_invalid')
            if kind == 'browser_ui':
                ui = action.get('ui')
                if not submit or not self.browser_profile_dir or type(ui) is not dict:
                    raise IndependentAdapterError('adapter_action_invalid')
                if action.get('question') != turn['question']:
                    raise IndependentAdapterError('submission_unconfirmed')
                if 'profile_dir' in ui or 'headless' in ui:
                    raise IndependentAdapterError('adapter_action_invalid')
                opener = ui.get('open')
                if opener is not None and (type(opener) is not dict or
                                           opener.get('kind') != 'click' or
                                           type(opener.get('selector')) is not str or
                                           not opener['selector']):
                    raise IndependentAdapterError('adapter_action_invalid')
                if origin(ui.get('page_url', '')) not in self.allowed_origins:
                    raise IndependentAdapterError('adapter_origin_not_authorized')
                if not all(type(ui.get(key)) is str and ui[key] for key in
                           ('input_selector', 'answer_selector')) or type(ui.get('submit')) is not dict:
                    raise IndependentAdapterError('adapter_action_invalid')
            if submit:
                from .browser_target import _wire_contains_question
                if not _wire_contains_question(action, turn['question']):
                    observed = self.state.get('selected_request') or {}
                    self.last_diagnostic = {
                        'reason': 'current_question_absent',
                        'demonstration_body_reused': (
                            isinstance(action.get('body'), str)
                            and action['body'] == observed.get('body')),
                    }
                    raise IndependentAdapterError('submission_unconfirmed')
                if not allow_submit or turn['status'] != 'not_submitted':
                    raise IndependentAdapterError('submission_already_attempted')
                if kind == 'http':
                    proposed = dict(turn, events=[*turn['events'],
                        {'kind': 'http_request', 'url': url, 'submit': True}])
                    self._check_conversation_policy(proposed)
                self.debit()
                turn['status'] = 'submission_unknown'
                self._save()  # durable intent before any network send
            elif kind != 'ws_receive':
                self.debit()
            remaining = max(0.01, deadline - time.monotonic())
            if kind == 'http':
                request = self.http.prepare_request(requests.Request(action.get('method', 'GET'), url,
                    headers=action.get('headers', {}), data=action.get('body')))
                request_event = self._event(turn, 'http_request', url=url, method=request.method, body=request.body, submit=submit)
                with self.http.send(request, timeout=remaining, stream=True, allow_redirects=False) as response:
                    self._event(turn, 'http_response', url=url, status=response.status_code,
                                headers=dict(response.headers), request_event_id=request_event['event_id'])
                    if not 200 <= response.status_code < 300:
                        detail = response.raw.read(16384, decode_content=True)
                        self._event(turn, 'http_body', url=url,
                                    text=detail.decode('utf-8', errors='replace'),
                                    binary_b64=base64.b64encode(detail).decode(),
                                    request_event_id=request_event['event_id'])
                        self._event(turn, 'http_end', url=url, status=response.status_code,
                                    request_event_id=request_event['event_id'])
                    if response.status_code == 401 and submit:
                        raise IndependentAdapterError('adapter_authentication_required')
                    if not 200 <= response.status_code < 300:
                        raise IndependentAdapterError('adapter_http_status')
                    if submit:
                        turn['status'] = 'submitted'
                    body = bytearray()
                    application_stop = False
                    while True:
                        chunk = response.raw.read1(4096, decode_content=True)
                        if not chunk:
                            break
                        if time.monotonic() >= deadline:
                            raise IndependentAdapterError('adapter_timeout')
                        body.extend(chunk)
                        if len(body) > resource_limit('MAX_CAPTURE_BYTES'):
                            raise IndependentAdapterError('adapter_capture_limit')
                        self._event(turn, 'http_chunk', url=url, binary_b64=base64.b64encode(chunk).decode(), request_event_id=request_event['event_id'])
                        # Streaming application completion can be recognized before EOF.
                        if ((submit or turn['status'] in {'submitted', 'collecting'})
                                and self._plan('receive', turn).get('complete') is True):
                            application_stop = True
                            break
                    try:
                        text = body.decode('utf-8')
                    except UnicodeDecodeError:
                        text = None
                    self._event(turn, 'http_body', url=url, text=text, binary_b64=base64.b64encode(body).decode(), request_event_id=request_event['event_id'])
                    self._event(turn, 'http_stopped' if application_stop else 'http_end', url=url, status=response.status_code, request_event_id=request_event['event_id'])
            elif kind == 'ws_open':
                from websockets.sync.client import connect
                key = action.get('connection_id', 'main')
                headers = dict(action.get('headers', {}))
                cookie_request = self.http.prepare_request(requests.Request('GET', url.replace('wss:', 'https:').replace('ws:', 'http:')))
                if cookie_request.headers.get('Cookie') and 'Cookie' not in headers:
                    headers['Cookie'] = cookie_request.headers['Cookie']
                if key in self.sockets:
                    self.sockets.pop(key).close()
                self.sockets[key] = connect(url, additional_headers=headers, origin=action.get('origin'),
                    subprotocols=action.get('subprotocols'), open_timeout=remaining, proxy=None)
                self._event(turn, 'websocket_open', connection_id=key, url=url)
            elif kind == 'ws_send':
                key = action.get('connection_id', 'main')
                value = base64.b64decode(action['binary_b64'], validate=True) if 'binary_b64' in action else action['text']
                self.sockets[key].send(value)
                if submit:
                    turn['status'] = 'submitted'
                self._event(turn, 'websocket_sent', connection_id=key, submit=submit,
                            **({'binary_b64': action['binary_b64']} if isinstance(value, bytes) else {'text': value}))
            elif kind == 'ws_receive':
                key = action.get('connection_id', 'main')
                value = self.sockets[key].recv(timeout=remaining)
                self._event(turn, 'websocket_received', connection_id=key,
                    **({'binary_b64': base64.b64encode(value).decode()} if isinstance(value, bytes) else {'text': value}))
            elif kind == 'browser_ui':
                from .browser_ui_adapter import BrowserUiAdapter
                self._event(turn, 'browser_submission', url=ui['page_url'],
                            question=turn['question'], submit=True)
                browser_plan = dict(ui, profile_dir=self.browser_profile_dir,
                                    headless=False, timeout_seconds=remaining,
                                    on_retry=self.debit)
                try:
                    result = BrowserUiAdapter(browser_plan).run(turn['question'])
                except Exception as error:
                    self.last_diagnostic = {'phase': 'browser_ui',
                                            'exception_type': type(error).__name__,
                                            'reason': str(error)[:4000]}
                    raise IndependentAdapterError('adapter_execution_failed') from error
                status = result.get('submission_status')
                confirmed = bool(ui.get('confirmation') or ui.get('retry')) and \
                    type(status) is int and 200 <= status < 300
                if (type(result.get('answer')) is not str or not result['answer'].strip()
                        or not (result.get('question_visible') is True or confirmed)):
                    self.last_diagnostic = {'phase': 'browser_ui',
                                            'reason': 'question_or_answer_not_visible'}
                    raise IndependentAdapterError('adapter_evidence_invalid')
                if status is not None and not 200 <= status < 300:
                    raise IndependentAdapterError('adapter_http_status')
                reference_url = result.get('reference_url') or urljoin(
                    ui['page_url'], result.get('reference_path', ''))
                if origin(reference_url) not in self.allowed_origins:
                    raise IndependentAdapterError('adapter_origin_not_authorized')
                turn['reference_url'] = reference_url
                turn['status'] = 'submitted'
                self._event(turn, 'browser_answer', text=result['answer'],
                            reference_url=reference_url, submission_status=status,
                            completion_signal=result.get('completion_signal'))
                self._event(turn, 'browser_end', reference_url=reference_url)
            else:
                raise IndependentAdapterError('adapter_action_invalid')
            self._save()

    def _complete(self, plan, turn):
        self.last_diagnostic = None
        self._check_conversation_policy(turn)
        submit_index = next((index for index, event in enumerate(turn['events'])
            if event.get('submit') is True and event['kind'] in
            {'http_request', 'websocket_sent', 'browser_submission'}), None)
        observed = turn['events'][submit_index + 1:] if submit_index is not None else []
        refs = [event['event_id'] for event in observed if event['kind'] in
                {'http_body', 'http_chunk', 'websocket_received', 'browser_answer'}]
        ends = [event['event_id'] for event in observed if event['kind'] in
                {'http_end', 'http_stopped', 'browser_end'}]
        if not ends and observed and observed[-1]['kind'] == 'websocket_received':
            ends = [observed[-1]['event_id']]
        if turn['status'] not in {'submitted', 'collecting'}:
            raise IndependentAdapterError('submission_unconfirmed')
        if not refs or not ends:
            self.last_diagnostic = self._evidence_diagnostic(plan, turn)
            raise IndependentAdapterError('adapter_evidence_invalid')
        answer = plan.get('answer')
        if type(answer) is not str or not answer or len(answer.encode()) > resource_limit('MAX_OUTPUT_BYTES'):
            self.last_diagnostic = self._evidence_diagnostic(plan, turn)
            raise IndependentAdapterError('adapter_answer_invalid')
        turn.update(status='completed', answer=answer, source_refs=refs, completion_refs=ends,
                    context=self.context, adapter_sha256=hashlib.sha256(self.source.encode()).hexdigest())
        if plan.get('reference_url'):
            if origin(plan['reference_url']) not in self.allowed_origins:
                raise IndependentAdapterError('adapter_origin_not_authorized')
            turn['reference_url'] = plan['reference_url']
        self._save()
        return self.turn(turn['request_id'])

    @staticmethod
    def _evidence_diagnostic(plan, turn):
        """Give repair Agents event IDs and kinds, without leaking response bodies."""
        events = {event['event_id']: event for event in turn['events']}
        return {'answer_chars': len(plan['answer']) if isinstance(plan.get('answer'), str) else None,
                'recent_events': [{'event_id': event['event_id'], 'kind': event['kind']}
                    for event in turn['events'][-30:]]}

    @staticmethod
    def _directline_conversation_id(turn):
        submitted = [event for event in turn.get('events', [])
                     if event.get('kind') == 'http_request' and event.get('submit')]
        if len(submitted) != 1:
            return None
        path = urlsplit(submitted[0].get('url', '')).path
        match = re.search(r'/conversations/([^/]+)/activities/?$', path, re.I)
        return unquote(match.group(1)) if match else None

    def _check_conversation_policy(self, turn):
        if self.policy == 'demonstrated':
            return
        conversation_id = self._directline_conversation_id(turn)
        if conversation_id is None:
            return
        demonstrated = {value for entry in self.state.get('session_storage', [])
                        for key, value in entry.get('items', {}).items()
                        if 'conversation' in key.lower() and isinstance(value, str)}
        if conversation_id in demonstrated:
            raise IndependentAdapterError('conversation_policy_violation')
        for previous in self.turns.values():
            if previous['request_id'] == turn['request_id']:
                continue
            earlier_id = self._directline_conversation_id(previous)
            if earlier_id != conversation_id:
                continue
            if self.policy == 'independent' or (turn['role'] == 'formal'
                    and previous['role'] != 'formal'):
                raise IndependentAdapterError('conversation_policy_violation')

    def run(self, question, request_id, role='formal'):
        if request_id in self.turns:
            old = self.turns[request_id]
            if old['question'] != question:
                raise IndependentAdapterError('request_identity_mismatch')
            if old['status'] == 'completed':
                return self.turn(request_id)
            if old['status'] != 'not_submitted':
                raise IndependentAdapterError('submission_already_attempted')
        turn = dict(request_id=request_id, question=question, role=role, status='not_submitted', events=[])
        self.turns[request_id] = turn
        deadline = time.monotonic() + self.timeout
        try:
            if self.policy == 'independent':
                self.context = {}
            if any(quote + 'prepare' + quote in self.source for quote in ("'", '"')):
                for _ in range(resource_limit('INDEPENDENT_TURN_STEPS')):
                    setup = self._plan('prepare', turn)
                    if setup.get('ready') is True:
                        break
                    if not setup.get('actions') or any(action.get('submit') is True
                            for action in setup['actions']):
                        raise IndependentAdapterError('adapter_setup_incomplete')
                    self._actions(setup, turn, deadline, False)
                else:
                    raise IndependentAdapterError('adapter_setup_incomplete')
            self._actions(self._plan('send', turn), turn, deadline, True)
            for _ in range(resource_limit('INDEPENDENT_TURN_STEPS')):
                plan = self._plan('receive', turn)
                if plan.get('complete') is True:
                    return self._complete(plan, turn)
                if not plan.get('actions'):
                    raise IndependentAdapterError('completion_unconfirmed')
                self._actions(plan, turn, deadline, False)
            raise IndependentAdapterError('completion_unconfirmed')
        except BaseException as error:
            turn['error'] = getattr(error, 'reason', getattr(error, 'code', 'adapter_execution_failed'))
            self._save()
            raise

    def close(self):
        for socket in self.sockets.values():
            socket.close()
        self.sockets.clear()
        self.http.close()

    def recover(self, request_id):
        """Collect an existing turn; never repeat its send action."""
        turn = self.turns[request_id]
        if turn['status'] == 'completed' and (turn.get('verified') or
                turn.get('adapter_sha256') == hashlib.sha256(self.source.encode()).hexdigest()):
            return self.turn(request_id)
        if turn['status'] == 'completed':
            turn['status'] = 'collecting'
        self.context = turn.get('context', {})
        deadline = time.monotonic() + self.timeout
        plan = self._plan('receive', turn)
        for _ in range(resource_limit('INDEPENDENT_TURN_STEPS')):
            if plan.get('complete') is True:
                return self._complete(plan, turn)
            if not plan.get('actions'):
                raise IndependentAdapterError('pending_turn_observation_unavailable')
            self._actions(plan, turn, deadline, False)
            plan = self._plan('receive', turn)
        raise IndependentAdapterError('completion_unconfirmed')

    def mark_verified(self, request_id, method):
        """Host-only fidelity receipt; preserve successful text across repairs."""
        self.turns[request_id].update(verified=True, reference_method=method)
        self._save()
