"""Single demonstration -> material Agent -> adapter Agent -> qualified adapter."""
from __future__ import annotations

import ast
from functools import wraps
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import parse_qsl, urlsplit

from .browser_agent_loop import BrowserAgentError, run_browser_agent
from .browser_semantic_review import SemanticReviewError, review_answer
from .browser_target import DiscoveryLedger
from .demonstration_recorder import DemonstrationRecorder
from .independent_adapter import IndependentAdapter, IndependentAdapterError, origin
from .independent_reference import NativeJsonReference
from .model_profiles import resolve_model_profile
from .protocol_runtime import ProtocolRuntime, ProtocolRuntimeError
from .progress import ProgressReporter
from .recording_store import RecordingStore
from .resource_limits import resource_limit
from .language import tr
from .work_session import atomic_json, current_session, digest


def _response_shape(events):
    """Small, content-free protocol hints for a failed live response."""
    message_types, diagnostic_kinds = [], []
    body_count = 0
    for event in events:
        if event.get('kind') != 'http_body' or type(event.get('text')) is not str:
            continue
        body_count += 1
        for line in event['text'][:resource_limit('MAX_INPUT_BYTES')].splitlines()[:200]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if type(item) is not dict:
                continue
            marker = item.get('type')
            if type(marker) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', marker):
                if marker not in message_types:
                    message_types.append(marker)
                if marker == 'diagnostic':
                    kind = item.get('kind')
                    if (type(kind) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', kind)
                            and kind not in diagnostic_kinds):
                        diagnostic_kinds.append(kind)
    return {'body_count': body_count, 'message_types': message_types[:20],
            'diagnostic_kinds': diagnostic_kinds[:20]}


def _recorded_secret_values(store, refs):
    """Find credential-like values in selected evidence for source-literal checks."""
    markers = ('token', 'proof', 'turnstile', 'secret', 'session',
               'authorization', 'credential')
    values = set()

    def visit(value, key=''):
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and len(value) >= 24 and any(
                marker in key.lower() for marker in markers):
            values.add(value)

    for ref in refs:
        event = store.event(ref)
        visit(event.get('headers') or {})
        for key in ('body', 'text'):
            body = event.get(key)
            if not isinstance(body, str) or len(body) > resource_limit('MAX_INPUT_BYTES'):
                continue
            try:
                visit(json.loads(body))
            except ValueError:
                for line in body.splitlines()[:200]:
                    try:
                        visit(json.loads(line))
                    except ValueError:
                        pass
                for name, value in parse_qsl(body, keep_blank_values=True):
                    visit(value, name)
    return values


class IndependentTarget:
    def __init__(self, *, page_url, workspace, timeout=3600, runs_root=None,
                 verbose=True, confirmed=False, fresh_profile=False, probe_question=None,
                 extraction_options=None, env_file='.env', adapter_path=None,
                 max_tool_calls=None, conversation_policy='independent', recorder=None,
                 agent_factory=None, input_fn=input, output_fn=None, reference_reader=None,
                 model_call_limit=None):
        origin(page_url)
        self.page_url, self.workspace = page_url, Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout, self.verbose = timeout, verbose
        self.confirmed, self.options = confirmed, extraction_options
        self.model = getattr(extraction_options, 'model_destination', None) or 'gemini:gemini-3.8-flash'
        self.env_file, self.agent_factory = env_file, agent_factory
        self.input_fn = input_fn
        self.output_fn = output_fn or (lambda message: print(message, file=sys.stderr, flush=True))
        self.reference_reader, self.adapter_path = reference_reader, Path(adapter_path) if adapter_path else None
        self.max_tool_calls = max_tool_calls or resource_limit('BROWSER_DISCOVERY_TOOL_CALLS')
        baseline_model_calls = resource_limit('BROWSER_DISCOVERY_MODEL_CALLS')
        if model_call_limit is not None and (type(model_call_limit) is not int or model_call_limit <= 0):
            raise ValueError('model_call_limit must be a positive integer')
        self.policy = conversation_policy
        key = hashlib.sha256(origin(page_url).encode()).hexdigest()[:20]
        profile = Path(runs_root or '.lladar') / 'browser-profiles' / (key + ('-' + secrets.token_hex(6) if fresh_profile else ''))
        self.recorder = recorder or DemonstrationRecorder(page_url=page_url, workspace=self.workspace, profile_dir=profile)
        self.store = self.recorder.store
        self.demo_question = (probe_question if probe_question is not None else
            'Please describe what assistance this service provides. [DEMO-' +
            secrets.token_hex(8) + ']')
        self.recording_after = 0
        self.ledger = DiscoveryLedger(self.workspace, deadline_seconds=resource_limit('EXTRACTION_TIMEOUT_SECONDS'))
        extensions = self.ledger.data.get('model_call_extensions', [])
        previous_cap = extensions[-1]['to'] if extensions else baseline_model_calls
        if model_call_limit is not None and model_call_limit < previous_cap:
            raise ValueError('model_call_limit cannot decrease on resume')
        self.model_call_limit = model_call_limit or previous_cap
        if self.model_call_limit > baseline_model_calls:
            extensions = self.ledger.data.setdefault('model_call_extensions', [])
            if not extensions or extensions[-1]['to'] != self.model_call_limit:
                extensions.append({'from': max(baseline_model_calls,
                    extensions[-1]['to'] if extensions else baseline_model_calls),
                    'to': self.model_call_limit})
                self.ledger.save()
        self.evidence = {'schema_version': 3, 'stage': 'preparing', 'status': 'discovering',
                         'browser_closed': False, 'verification': [], 'ledger': self.ledger.data}
        self.candidate = None
        self.adapter = None
        self.reference = NativeJsonReference()
        self._qualified = False
        self._tested_hash = None
        self._last_progress = time.monotonic()
        self.progress = ProgressReporter(enabled=verbose)

    def _input(self, prompt):
        with ExitStack() as stack:
            stack.enter_context(self.progress.user_input())
            session = current_session()
            for reporter in session.reporters if session else ():
                stack.enter_context(reporter.user_input())
            return self.input_fn(prompt)

    def _record(self):
        self.evidence['stage'] = 'demonstration'
        with ExitStack() as stack:
            stack.enter_context(self.progress.user_input())
            session = current_session()
            for reporter in session.reporters if session else ():
                stack.enter_context(reporter.user_input())
            self.state = self.recorder.capture(self.demo_question, self.input_fn, self.output_fn)
        self.evidence.update(browser_closed=self.recorder.closed, stage='analyzing_recording')
        self._select_demonstration_recording()
        if not self.recorder.closed:
            raise IndependentAdapterError('recording_browser_not_closed')
        self.output_fn(tr('Browser closed. Please wait: the material Agent will select evidence, then the adapter Agent will generate code and test it.'))

    def _select_demonstration_recording(self):
        metadata = json.loads((self.workspace / 'recording' / 'demonstration.json').read_text(encoding='utf-8'))
        self.demo_question = metadata['question']
        self.recording_after = self.store.recording_start(metadata['submission_refs'][0])

    def _save(self):
        self.evidence['ledger'] = self.ledger.data
        atomic_json(self.workspace / 'discovery.json', self.evidence)
        lines = ['# Website adapter', '', 'Status: ' + self.evidence['status'],
                 'Browser closed: ' + str(self.evidence['browser_closed']),
                 'Conversation policy: ' + self.policy,
                 'Verification scope: ' + self.evidence.get('verification_scope', 'not_verified'),
                 'Verified rounds: ' + str(len(self.evidence['verification'])),
                 'Usage: ' + json.dumps(self.ledger.data['counts']),
                 'This evidence does not establish acceptance of other websites.']
        (self.workspace / 'discovery-report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def _load_candidate(self, path):
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if value.get('schema_version') != 1 or value.get('mode') != 'independent':
            raise IndependentAdapterError('adapter_runtime_mismatch')
        if value.get('sha256') != digest({key: item for key, item in value.items() if key != 'sha256'}):
            raise IndependentAdapterError('adapter_integrity_failed')
        if origin(value['page_url']) != origin(self.page_url):
            raise IndependentAdapterError('adapter_origin_mismatch')
        if value.get('conversation_policy', 'independent') != self.policy:
            raise IndependentAdapterError('adapter_runtime_mismatch')
        self.candidate = value

    def prepare(self, _probes=None, *, interactive=None, record_count=0, request_count=0):
        self.interactive = interactive
        try:
            self.output_fn(tr('Target: {url}; exploration model: {model}; formal trials: {count}; conversation policy: {policy}', url=self.page_url, model=self.model, count=request_count, policy=self.policy))
            self.output_fn(tr('Recording workspace: {path}',path=str(self.workspace.resolve())))
            expected_destination = None
            if self.agent_factory is None:
                from .providers.akasha import resolve_model_destination
                expected_destination = resolve_model_destination(self.model, self.env_file,
                    getattr(self.options, 'output_token_limit', None))
                self.output_fn(tr('Model destination: ') + expected_destination['origin'])
            self.expected_destination = expected_destination
            self.output_fn(tr('Automatic limits: {limits}', limits=json.dumps({
                'model_calls':self.model_call_limit,
                'tool_calls':self.max_tool_calls,'website_requests':resource_limit('BROWSER_DISCOVERY_REQUESTS'),
                'repairs':resource_limit('BROWSER_DISCOVERY_REPAIRS'),
                'automatic_seconds':self.ledger.deadline_seconds,'request_seconds':self.timeout,
                'capture_bytes':resource_limit('MAX_CAPTURE_BYTES'),
                'recording_bytes':resource_limit('MAX_RECORDING_BYTES')})))
            self.output_fn(tr('The demonstration and necessary service cookies, tokens and session state will be saved for model analysis; the browser closes after saving.'))
            if not self.ledger.data.get('approved') and not self.confirmed:
                if self._input(tr('Type YES to approve recording, model analysis, budgeted testing, verification, repair and formal execution; anything else cancels: ')).strip() != 'YES':
                    raise IndependentAdapterError('consent_required')
            manifest = self.adapter_path or self.workspace / 'adapter' / 'manifest.json'
            state_path = self.workspace / 'state' / 'initial.json'
            if self.adapter_path:
                self._load_candidate(manifest)
                state_path = self.adapter_path.parent / 'state.json'
            if state_path.is_file():
                self.state = json.loads(state_path.read_text(encoding='utf-8'))
                integrity = json.loads(state_path.with_suffix('.integrity.json').read_text(encoding='utf-8'))
                if integrity.get('sha256') != digest(self.state):
                    raise IndependentAdapterError('state_integrity_failed')
                demonstration = self.workspace / 'recording' / 'demonstration.json'
                if not demonstration.is_file() and self.adapter_path is None:
                    self.recorder.recover_completed_capture(self.state)
                if demonstration.is_file():
                    self._select_demonstration_recording()
                self.evidence['browser_closed'] = True
                self.output_fn(tr('Saved state loaded. No browser is opened; a fresh question will verify the adapter under the selected conversation policy.'))
            else:
                if not interactive:
                    raise IndependentAdapterError('demonstration_required')
                self._record()
            self.ledger.approve()  # automatic deadline starts after manual demonstration
            if manifest.is_file() and self.candidate is None:
                self._load_candidate(manifest)
            for attempt in range(2):
                try:
                    if self.candidate is None:
                        self._discover(expected_destination)
                    else:
                        self._make_adapter()
                        self._probe()
                    self._qualify()
                    break
                except Exception as error:
                    if getattr(error,'reason',None) not in {'adapter_authentication_required','browser_authentication_required'} or attempt:
                        raise
                    self._refresh_login()
        except BaseException as error:
            self.evidence.update(status='blocked', failure_reason=getattr(error, 'reason', 'independent_target_failed'))
            self.recorder.close()
            self._save()
            raise

    def _refresh_login(self):
        if not self.interactive:
            raise IndependentAdapterError('adapter_authentication_required')
        if self.adapter:
            self.adapter.close()
            self.adapter = None
        started = time.time()
        with ExitStack() as stack:
            stack.enter_context(self.progress.user_input())
            session=current_session()
            for reporter in session.reporters if session else ():
                stack.enter_context(reporter.user_input())
            self.state=self.recorder.refresh_state(self.state,self.input_fn,self.output_fn)
        elapsed = time.time() - started
        self.ledger.data['started_at'] += elapsed
        self.ledger.data['manual_wait_seconds'] = self.ledger.data.get('manual_wait_seconds',0) + elapsed
        self.ledger.save()
        atomic_json(self.workspace/'runtime'/'state.json',self.state)
        atomic_json(self.workspace/'runtime'/'state.integrity.json',{'sha256':digest(self.state)})
        self.evidence['extra_browser_openings']=self.evidence.get('extra_browser_openings',0)+1
        self.output_fn(tr('Updated login state saved; browser closed. Independent verification resumes.'))

    def _make_adapter(self):
        if self.adapter:
            self.state = self.adapter.state
            self.adapter.close()
        browser = getattr(self.recorder, 'browser', None)
        self.adapter = IndependentAdapter(source=self.candidate['source'], state=self.state,
            allowed_origins=self.state['allowed_origins'], workspace=self.workspace / 'runtime',
            timeout=self.timeout, conversation_policy=self.policy,
            browser_profile_dir=getattr(browser, 'profile_dir', None),
            debit=lambda: self.ledger.debit('website_requests', resource_limit('BROWSER_DISCOVERY_REQUESTS')))

    def _verify(self, turn):
        if self.policy == 'demonstrated' or any(event.get('kind') == 'browser_answer'
                                                for event in turn.get('events', [])):
            self.ledger.debit('model_calls', self.model_call_limit)
            verdict = review_answer(turn['question'], turn['answer'], model=self.model,
                env_file=self.env_file, expected_destination=self.expected_destination,
                max_input_tokens=getattr(self.options, 'max_input_tokens', None),
                max_output_tokens=getattr(self.options, 'max_output_tokens', None))
            self.evidence.setdefault('semantic_reviews', []).append({
                'request_id': turn['request_id'], 'answer_chars': len(turn['answer']),
                'reasonable': verdict['reasonable'], 'reason': verdict['reason']})
            if not verdict['reasonable']:
                raise IndependentAdapterError('reference_mismatch')
            return 'model_reasonableness_review'
        automatic = self.reference.read(turn)
        if automatic is not None:
            if automatic != turn['answer']:
                raise IndependentAdapterError('reference_mismatch')
            return 'qualified_native_json_reference'
        if self.reference_reader:
            expected = self.reference_reader(turn)
            if type(expected) is not str or expected != turn['answer']:
                raise IndependentAdapterError('reference_mismatch')
            self.reference.qualify(turn)
            return 'independent_host_reference'
        # Live sites without an independent raw oracle need an explicit same-turn review.
        from .browser_discovery_driver import GenericBrowserDriver
        self.output_fn(tr('Same-turn full-answer review is needed. The program temporarily opens the website; find the preceding question and complete answer. Do not submit it again.'))
        browser = GenericBrowserDriver(page_url=turn.get('reference_url') or self.state['page_url'],
            profile_dir=self.recorder.browser.profile_dir, timeout=self.timeout)
        with browser.manual_interaction():
            try:
                browser.open()
                self.output_fn(tr('Question: ') + turn['question'] + tr('\nCaptured complete answer:\n') + turn['answer'])
                while True:
                    # Reuse the pumping prompt while this temporary browser owns the page.
                    original = self.recorder.browser
                    self.recorder.browser = browser
                    try:
                        with ExitStack() as stack:
                            stack.enter_context(self.progress.user_input())
                            session = current_session()
                            for reporter in session.reporters if session else ():
                                stack.enter_context(reporter.user_input())
                            result = self.recorder.prompt(tr('Compare the complete same-turn website answer: type MATCH if identical, MISMATCH if different, or WAIT if unfinished: '), self.input_fn).strip()
                    finally:
                        self.recorder.browser = original
                    if result == 'WAIT':
                        continue
                    if result != 'MATCH':
                        raise IndependentAdapterError('reference_mismatch')
                    self.reference.qualify(turn)
                    return 'manual_same_turn_review'
            finally:
                browser.close(strict=True)

    def _run(self, question, request_id, role):
        self.ledger.check()
        self.adapter.timeout = min(self.timeout, max(0.1,self.ledger.deadline_seconds-(time.time()-self.ledger.data['started_at'])))
        previous_phase = self.ledger.phase
        self.ledger.phase = role
        try:
            with self.progress.waiting(tr('Website {role} request {request_id}',role=role,request_id=request_id),interval=30):
                existing = self.adapter.turns.get(request_id)
                try:
                    if existing and existing['status'] != 'not_submitted':
                        turn = self.adapter.recover(request_id)
                    else:
                        turn = self.adapter.run(question, request_id, role=role)
                except IndependentAdapterError as error:
                    saved = self.adapter.turns.get(request_id)
                    if (error.reason != 'adapter_timeout' or not saved or
                            saved['status'] not in {'submitted', 'collecting', 'submission_unknown'}):
                        raise
                    self.ledger.check()
                    self.output_fn(tr('The submitted turn timed out; continuing observation without resending it.'))
                    self.adapter.timeout = min(self.timeout,
                        max(0.1, self.ledger.deadline_seconds -
                            (time.time() - self.ledger.data['started_at'])))
                    turn = self.adapter.recover(request_id)
                turn['reference_method'] = self._verify(turn)
                self.adapter.mark_verified(request_id, turn['reference_method'])
                # The runtime turn checkpoint already contains every trial
                # event. Keep the human demonstration recording immutable so
                # a large successful response cannot exhaust its capacity.
                if self._qualified:
                    atomic_json(self.workspace / 'adapter' / 'state.json', self.adapter.state)
                    atomic_json(self.workspace / 'adapter' / 'state.integrity.json', {'sha256': digest(self.adapter.state)})
                self._save()
                return turn
        finally:
            self.ledger.phase = previous_phase

    def _probe(self):
        demonstrated_text = self.demo_question.split(' [DEMO-', 1)[0]
        probe_text = (demonstrated_text + ' [PROBE-' + secrets.token_hex(8) + ']'
                      if demonstrated_text != self.demo_question else
                      demonstrated_text + ' Please give one concrete example.')
        turn = self._run(probe_text,
                         'probe-' + secrets.token_hex(8), 'probe')
        self._tested_hash = self.candidate['sha256']
        return turn

    def _qualify(self):
        if self.candidate is None:
            raise IndependentAdapterError('adapter_not_submitted')
        if self._tested_hash != self.candidate['sha256']:
            raise IndependentAdapterError('adapter_not_qualified')
        if self.policy == 'demonstrated':
            path = self.workspace / 'adapter' / 'manifest.json'
            atomic_json(path, self.candidate)
            atomic_json(path.parent / 'state.json', self.adapter.state)
            atomic_json(path.parent / 'state.integrity.json',
                        {'sha256': digest(self.adapter.state)})
            self.evidence.update(status='qualified', stage='ready',
                adapter_sha256=self.candidate['sha256'], adapter_path=str(path),
                independent_execution=False,
                verification_scope='one_live_probe_in_demonstrated_conversation')
            self._qualified = True
            self.output_fn(tr('Live probe passed in the demonstrated conversation. Formal execution starts; no action is needed from you.'))
            self._save()
            return
        self.evidence['stage'] = 'independent_verification'
        browser_ui = self.candidate.get('transport') == 'browser_ui'
        self.output_fn(tr('Please wait: two fresh verification questions are being sent using the saved browser profile. They do not count as formal results.')
                       if browser_ui else
                       tr('Please wait: two fresh verification questions are being sent without the recording browser. They do not count as formal results.'))
        demonstrated_text = self.demo_question.split(' [DEMO-', 1)[0]
        self.evidence['verification_scope'] = 'demonstrated_question_repeats'
        for index in range(2):
            verify_text = (demonstrated_text + ' [VERIFY-' + secrets.token_hex(8) + ']'
                           if demonstrated_text != self.demo_question else
                           demonstrated_text + (' Please give one concrete example.' if index == 0
                                                else ' Please give another concrete example.'))
            self.evidence['verification'].append(self._run(verify_text,
                'verification-' + secrets.token_hex(8), 'verification')['request_id'])
        path = self.workspace / 'adapter' / 'manifest.json'
        atomic_json(path, self.candidate)
        atomic_json(path.parent / 'state.json', self.adapter.state)
        atomic_json(path.parent / 'state.integrity.json', {'sha256': digest(self.adapter.state)})
        self.evidence.update(status='qualified', stage='ready', adapter_sha256=self.candidate['sha256'],
                             adapter_path=str(path), independent_execution=not browser_ui,
                             verification_transport='browser_ui' if browser_ui else 'protocol')
        self._qualified = True
        self.adapter.context = {}  # fresh formal conversation, no verification history
        self.output_fn(tr('Verification passed: fresh questions work through the saved-profile browser. Formal execution starts; no action is needed from you.')
                       if browser_ui else
                       tr('Verification passed: independent questions work after browser shutdown. Formal execution starts; no action is needed from you.'))
        self._save()

    def _discover(self, expected_destination=None):
        self.evidence['stage'] = 'agent_generation'
        candidate_path = self.workspace / 'adapter' / 'candidate.json'
        resumed_candidate = self.candidate is None and candidate_path.is_file()
        if resumed_candidate:
            self._load_candidate(candidate_path)
            last_test = self.workspace / 'validation' / 'last-test.json'
            if last_test.is_file():
                result = json.loads(last_test.read_text(encoding='utf-8'))
                self.evidence['failure_reason'] = (result.get('reason', '') + '. '
                    + result.get('hint', '')).strip()
        def submit_adapter(source: str, basis: str) -> str:
            """Preflight adapt(payload) locally before saving a candidate. Fix rejected source and resubmit; an accepted candidate still needs test_live."""
            def reject(detail, **diagnostic):
                return json.dumps({'status': 'rejected', 'reason': 'adapter_source_invalid',
                    'diagnostic': {'reason': detail, **diagnostic}})

            if len(source.encode()) > resource_limit('PROTOCOL_SOURCE_BYTES'):
                return reject('source_too_large')
            if not basis.strip():
                return reject('evidence_basis_missing')
            if self.policy == 'demonstrated' and not self.state.get('selected_request'):
                return reject('demonstrated_request_missing')
            try:
                module = ast.parse(source)
            except SyntaxError as error:
                return reject('syntax_error', exception_type='SyntaxError',
                    line=error.lineno, column=error.offset)
            if not any(isinstance(node, ast.FunctionDef) and node.name == 'adapt'
                       for node in module.body):
                return reject('adapt_function_missing')
            missing_phases = [phase for phase in ('send', 'receive')
                if not any(quote + phase + quote in source for quote in ("'", '"'))]
            if missing_phases:
                return reject('phase_literal_missing', phases=missing_phases)
            cookie_secrets = [cookie.get('value', '') for cookie in self.state.get('cookies', [])]
            selected = self.state.get('selected_request') or {}
            header_secrets = [value for key, value in (selected.get('headers') or {}).items()
                if key.lower() in {'authorization', 'cookie', 'x-api-key', 'x-auth-token'}]
            storage_secrets = [value for area in ('local_storage', 'session_storage')
                for entry in self.state.get(area, [])
                for key, value in entry.get('items', {}).items()
                if type(value) is str and any(label in key.lower()
                    for label in ('token', 'authorization', 'secret', 'session'))]
            forbidden = cookie_secrets + header_secrets + storage_secrets
            forbidden += [value.split(' ', 1)[-1] for value in header_secrets]
            if any(len(value) >= 8 and value in source for value in forbidden):
                return reject('private_value_literal')
            selected_path = self.workspace / 'adapter' / 'selected-material.json'
            if selected_path.is_file():
                selected_refs = json.loads(selected_path.read_text(
                    encoding='utf-8')).get('event_ids', [])
                recorded_secrets = _recorded_secret_values(self.store, selected_refs)
                source_literals = [node.value for node in ast.walk(module)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and len(node.value) >= 24]
                if any(literal in secret or secret in literal
                       for literal in source_literals for secret in recorded_secrets):
                    return reject('private_value_literal', hint=(
                        'Derive recorded proof, turnstile, session and other short-lived '
                        'values from current setup responses; do not embed them in source.'))
            if self.policy == 'demonstrated' and self.demo_question in source:
                return reject('demonstration_question_literal')
            try:
                fresh_question = 'Adapter preflight [PROBE-' + secrets.token_hex(8) + ']'
                from .independent_adapter import question_forms
                runtime = ProtocolRuntime()
                payload = {
                    'phase': 'send', 'question': fresh_question,
                    'request_id': 'candidate-preflight', 'role': 'probe',
                    'state': self.state, 'context': {}, 'events': [],
                    'conversation_policy': self.policy,
                    'question_forms': question_forms(fresh_question),
                    'demonstration_forms': question_forms(
                        self.state.get('demonstration_question'))}
                deferred_send = False
                if any(quote + 'prepare' + quote in source for quote in ("'", '"')):
                    setup = runtime.execute(source, dict(payload, phase='prepare'))
                    if setup.get('ready') is not True:
                        actions = setup.get('actions')
                        if (not isinstance(actions, list) or not actions or
                                any(type(action) is not dict or
                                    action.get('kind') not in
                                    {'http', 'ws_open', 'ws_send', 'ws_receive', 'browser_ui'} or
                                    action.get('submit') is True for action in actions)):
                            return reject('prepare_plan_invalid')
                        deferred_send = True
                    else:
                        payload['context'] = setup.get('context', {})
                plan = None if deferred_send else runtime.execute(source, payload)
            except ProtocolRuntimeError as error:
                diagnostic = dict(error.diagnostic)
                if error.code == 'protocol_output_invalid':
                    diagnostic.update(phase='send', hint=(
                        "adapt(payload) must return a JSON object, never a bare list. "
                        "For send, return {'actions': [action]}; for receive, "
                        "return {'complete': False} or "
                        "{'complete': True, 'answer': answer}."))
                return json.dumps({'status': 'rejected', 'reason': error.code,
                    'diagnostic': diagnostic})
            except Exception as error:
                return reject('unexpected_preflight_error',
                    exception_type=type(error).__name__)
            if deferred_send:
                plan = {'actions': []}
            if type(plan) is not dict or not isinstance(plan.get('actions'), list):
                return reject('send_plan_invalid')
            if any(type(action) is not dict or
                   action.get('kind') not in {'http', 'ws_open', 'ws_send', 'ws_receive', 'browser_ui'}
                   for action in plan.get('actions', [])):
                return reject('send_action_invalid')
            submissions = [action for action in plan['actions']
                if action.get('submit') is True]
            if len(submissions) != 1 and not deferred_send:
                return json.dumps({'status': 'rejected', 'reason': 'submission_unconfirmed',
                    'diagnostic': {'reason': 'exactly_one_submit_action_required'}})
            if not deferred_send:
                from .browser_target import _wire_contains_question
                submission = submissions[0]
                if submission.get('kind') == 'browser_ui':
                    ui = submission.get('ui')
                    if (submission.get('question') != fresh_question or type(ui) is not dict or
                            'profile_dir' in ui or 'headless' in ui or
                            type(ui.get('input_selector')) is not str or
                            type(ui.get('answer_selector')) is not str or
                            type(ui.get('submit')) is not dict):
                        return reject('browser_ui_plan_invalid')
                    try:
                        if origin(ui.get('page_url', '')) not in {
                                origin(value) for value in self.state['allowed_origins']}:
                            return reject('adapter_origin_not_authorized')
                    except (IndependentAdapterError, ValueError, TypeError):
                        return reject('adapter_origin_not_authorized')
                if not _wire_contains_question(submission, fresh_question):
                    observed = self.state.get('selected_request') or {}
                    return json.dumps({'status': 'rejected', 'reason': 'submission_unconfirmed',
                        'diagnostic': {'reason': 'current_question_absent',
                            'demonstration_body_reused': (
                                isinstance(submission.get('body'), str)
                                and submission['body'] == observed.get('body'))}})
            value = {'schema_version': 1, 'mode': 'independent', 'page_url': self.page_url,
                     'conversation_policy': self.policy,
                     'transport': ('browser_ui' if not deferred_send and
                                   submissions[0].get('kind') == 'browser_ui' else 'protocol'),
                     'source': source, 'basis': basis, 'dependencies': []}
            value['sha256'] = digest(value)
            self.candidate = value
            self.reference = NativeJsonReference()
            self._tested_hash = None
            atomic_json(self.workspace / 'adapter' / 'candidate.json', value)
            self._make_adapter()
            return json.dumps({'status': 'candidate', 'sha256': value['sha256']})
        def test_live() -> str:
            """Run one fresh host-generated probe with the candidate, without a browser."""
            try:
                turn = self._probe()
                atomic_json(self.workspace / 'validation' / 'last-test.json',
                    {'status': 'accepted', 'request_id': turn['request_id'],
                     'answer_chars': len(turn['answer'])})
                return json.dumps({'status': 'accepted', 'request_id': turn['request_id']})
            except Exception as error:
                if isinstance(error, SemanticReviewError):
                    raise
                self._tested_hash = None
                last_id = next(reversed(self.adapter.turns), '') if self.adapter else ''
                result = {'status': 'rejected', 'reason': getattr(error, 'reason', getattr(error, 'code', 'adapter_test_failed')),
                          'request_id': last_id, 'diagnostic': getattr(self.adapter, 'last_diagnostic', None) or
                          getattr(error, 'diagnostic', {}),
                          'exception_type': type(error).__name__}
                if last_id:
                    result['recent_event_kinds'] = [event['kind'] for event in
                        self.adapter.turns[last_id]['events'][-30:]]
                    result['response_shape'] = _response_shape(
                        self.adapter.turns[last_id]['events'])
                if last_id:
                    responses = [event for event in self.adapter.turns[last_id]['events']
                        if event.get('kind') == 'http_response']
                    if responses:
                        result['http_status'] = responses[-1].get('status')
                        result['request_path'] = urlsplit(responses[-1].get('url', '')).path
                        result['response_event_id'] = responses[-1]['event_id']
                if result['reason'] == 'adapter_authentication_required':
                    from .browser_target import BrowserDiscoveryError
                    raise BrowserDiscoveryError('browser_authentication_required')
                if result['reason'] == 'adapter_action_invalid':
                    result['hint'] = 'Every action needs kind (not type). Valid kinds: http, ws_open, ws_send, ws_receive, browser_ui. A browser_ui submit action needs question=payload.question and a ui plan with page_url, input_selector, submit control and answer_selector.'
                if result['reason'] == 'adapter_setup_incomplete':
                    result['hint'] = ('Prepare did not reach ready:true within its action budget. '
                        'Inspect recorded setup responses and the candidate parser in the restricted '
                        'runtime; do not silently swallow parsing errors or repeat unchanged setup actions.')
                if result['reason'] == 'conversation_policy_violation':
                    result['hint'] = ('The candidate reused a demonstrated or prior conversation. '
                        'Find a fresh-conversation setup operation in observed evidence or public '
                        'documentation for the detected protocol; derive the new ID from its live response.')
                if result['reason'] == 'reference_mismatch' and self.policy == 'demonstrated':
                    reviews = self.evidence.get('semantic_reviews') or []
                    result['hint'] = reviews[-1]['reason'] if reviews else 'Answer was not judged substantive and on topic.'
                atomic_json(self.workspace / 'validation' / 'last-test.json', result)
                return json.dumps(result)
        from .browser_two_agent import discover_adapter
        discover_adapter(self, submit_adapter=submit_adapter, test_live=test_live,
            expected_destination=expected_destination or self.expected_destination)
        if self.candidate is None:
            raise IndependentAdapterError('adapter_not_submitted')
        if self._tested_hash != self.candidate['sha256']:
            raise IndependentAdapterError('adapter_not_qualified')

    def answer(self, question, request_id):
        if not self._qualified:
            raise IndependentAdapterError('adapter_not_qualified')
        self.evidence['stage'] = 'formal'
        while True:
            try:
                return self._run(question, request_id, 'formal')['answer']
            except (KeyboardInterrupt, SystemExit):
                self._save()
                raise
            except Exception as error:
                reason = getattr(error, 'reason', getattr(error, 'code', 'adapter_failed'))
                self.evidence.update(status='repairing', failure_reason=reason)
                for event in self.adapter.turns.get(request_id, {}).get('events', []):
                    try:
                        self.store.event(event['event_id'])
                    except ValueError:
                        self.store.append(event)
                self._save()
                if reason == 'adapter_authentication_required':
                    self._refresh_login()
                    self._make_adapter()
                    self._probe()
                    self._qualify()
                    continue
                if reason in {'pending_turn_observation_unavailable','discovery_budget_exhausted','discovery_time_exhausted'}:
                    self._qualified = False
                    raise
                self.ledger.debit('repairs', resource_limit('BROWSER_DISCOVERY_REPAIRS'))
                self.output_fn(tr('Formal dispatch paused. The material and adapter Agents will examine failure evidence and repair the adapter. Submitted questions will not be sent again.'))
                self._discover()
                self._qualify()

    def close(self):
        if self.adapter:
            self.adapter.close()
        self.recorder.close()
        self.progress.close()
