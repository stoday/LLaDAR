"""Autonomous website integration through the target's public interface."""
from __future__ import annotations

from types import SimpleNamespace
import json
import pytest

pytestmark = pytest.mark.usefixtures("qualified_browser_demonstration")

from lladar.browser_target import BrowserTarget
from lladar.answer_extraction import ExtractionOptions


def test_website_and_model_consent_precede_exploration_or_submission(tmp_path):
    actions = []
    class Browser:
        def open(self):
            actions.append('open')
        def inspect(self):
            return {'frames': []}
        def close(self):
            actions.append('close')
    def agent_factory(**kwargs):
        pytest.fail('an unapproved run must not construct a model agent')
    target = BrowserTarget(page_url='https://example.test/chat', driver=Browser(),
        workspace=tmp_path, agent_factory=agent_factory,
        extraction_options=ExtractionOptions(provider_factory=lambda **_: pytest.fail('no extraction')),
        input_fn=lambda _: 'NO', output_fn=lambda _: None)
    with pytest.raises(RuntimeError, match='consent_required'):
        target.prepare([], interactive=True, record_count=1, request_count=1)
    assert actions == ['open']
    assert target.evidence['status'] == 'blocked'
    assert target.evidence['ledger']['counts'].get('submissions', 0) == 0
    target.close()


def test_noop_candidate_returns_probe_feedback_and_agent_can_replace_it(tmp_path):
    import time
    website = Website()
    feedback = []
    noop = 'def adapt(payload):\n    return {"actions": [], "context": payload.get("context", {}), "complete": payload["phase"] == "setup"}\n'
    def agent_factory(**options):
        def agent(_prompt):
            tools = options['tools']
            tools['submit_adapter'](noop, 'Incomplete candidate to exercise failure feedback')
            started = time.monotonic()
            rejected = json.loads(tools['test_adapter']())
            feedback.append(rejected)
            assert rejected['status'] == 'needs_revision'
            assert rejected['reason'] == 'submission_not_attempted'
            assert time.monotonic() - started < 10, 'No-op feedback must not wait for the 600-second website deadline'
            assert website.events_seen == []
            tools['submit_adapter'](PROGRAM, 'Observed request and complete response')
            assert json.loads(tools['test_adapter']())['status'] == 'accepted'
        return agent
    target = BrowserTarget(page_url='https://example.test/chat', driver=website, workspace=tmp_path,
        agent_factory=agent_factory, timeout=600, confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True, provider_factory=lambda **_: SimpleNamespace(
            extract=lambda request, **kwargs: {'result': {'status': 'final', 'text': 'The complete existing website answer.'}, 'usage': None})),
        reference_reader=lambda turn: 'The complete existing website answer.', output_fn=lambda _: None)
    try:
        target.prepare([], interactive=False, record_count=1, request_count=1)
        assert target.evidence['status'] == 'verified' and target.candidate['revision'] == 2
        assert target.ledger.data['turns'][feedback[0]['turn_id']]['status'] == 'not_attempted'
        assert target.answer('Fresh formal question', 'formal-after-repair') == 'The complete existing website answer.'
    finally:
        target.close()


PROGRAM = '''
def adapt(payload):
    if payload["phase"] == "setup":
        return {"complete": True, "context": {}}
    if payload["phase"] == "send":
        return {"actions": [{"kind": "http", "url": "https://example.test/ask", "method": "POST", "body": payload["question"], "submit": True}], "context": {}}
    if payload["phase"] == "observe":
        events = payload["observation"]["events"]
        requests = [e for e in events if e["kind"] == "http_request"]
        ends = [e for e in events if e["kind"] == "fetch_end"]
        responses = [e for e in events if e["kind"] == "fetch_chunk"]
        if requests and ends and responses:
            return {"complete": True, "evidence": {"submission_events": [requests[0]["event_id"]], "completion_events": [ends[0]["event_id"]]}, "materials": [{"event_ids": [responses[0]["event_id"]], "data": responses[0]["text"]}], "context": {}}
    return {"actions": [], "context": {}}
'''


class Website:
    def __init__(self):
        self.turns = {}
        self.active = None
        self.events_seen = []
    def open(self): pass
    def inspect(self): return {'frames': [], 'client_resources': [], 'active_page_id':'fake-page'}
    def begin_turn(self, turn_id):
        self.active = turn_id
        self.turns[turn_id] = []
        return {'turn_id': turn_id, 'sequence': len(self.events_seen), 'page': self.inspect()}
    def http_request(self, spec, *, submit=False):
        assert submit
        for kind, extra in [
            ('submission_attempted', {'method':'http','observation_source':'browser_host'}),
            ('http_request', {'body': spec['body'], 'url':spec['url'],'method':'POST','observation_source': 'browser_network'}),
            ('fetch_start', {'url':spec['url'],'method':'POST','request_id':self.active}),
            ('fetch_chunk', {'text': 'The complete existing website answer.', 'request_id': self.active}),
            ('fetch_end', {'request_id': self.active})]:
            event = {'event_id': f'event-{len(self.events_seen)+1}', 'sequence': len(self.events_seen)+1,
                     'kind': kind, 'turn_id': self.active, 'page_id':'fake-page', **extra}
            self.events_seen.append(event)
            self.turns[self.active].append(event)
        return {'started': True}
    def observe_turn(self, turn_id):
        return {'turn_id': turn_id, 'submitted': True, 'events': self.turns[turn_id], 'page': self.inspect()}
    def raw_event(self, event_id): return next(e for e in self.events_seen if e['event_id'] == event_id)
    def events(self, **kwargs): return self.events_seen
    def close(self): pass


def test_generated_method_is_independently_qualified_and_reused_for_formal_turns(tmp_path):
    website = Website()
    def agent_factory(**options):
        def agent(_prompt):
            tools = options['tools']
            assert 'PRIVATE_EXPECTED' not in tools['inspect_browser']()
            tools['submit_adapter'](PROGRAM, 'Observed request and complete response')
            tools['test_adapter']()
        return agent
    def provider_factory(**kwargs):
        return SimpleNamespace(extract=lambda request, **_: {
            'result': {'status': 'final', 'text': 'The complete existing website answer.'}, 'usage': None})
    target = BrowserTarget(page_url='https://example.test/chat', driver=website, workspace=tmp_path,
        agent_factory=agent_factory, confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True, provider_factory=provider_factory),
        reference_reader=lambda turn: 'The complete existing website answer.', output_fn=lambda _: None)
    target.prepare(['PRIVATE_FUTURE_QUESTION'], interactive=False, record_count=1, request_count=1)
    assert target.evidence['status'] == 'verified'
    assert len(target.evidence['verification']) == 1
    assert target.answer('New formal question', 'formal-1') == 'The complete existing website answer.'
    assert len(website.turns) == 3  # candidate probe, one fresh verification, one formal turn
    assert 'PRIVATE_FUTURE_QUESTION' not in json.dumps(website.events_seen)
    assert (tmp_path/'adapter.json').is_file()
    assert (tmp_path/'discovery-report.md').is_file()
    assert target.ledger.data['turns']['formal-1']['status'] == 'completed'
    phases=target.ledger.data['phase_counts']
    assert phases['exploration']['model_calls']==1
    assert phases['probe']['model_calls']==1
    assert phases['verification']['model_calls']==1
    assert phases['formal']['model_calls']==1
    assert target.ledger.data['counts']['requests']==3
    target.close()


def test_local_protocol_tool_documents_its_payload_and_retains_safe_repair_diagnostics(tmp_path):
    website=Website()
    website.events_seen=[{'event_id':'observed-codec-input','sequence':1,'kind':'http_body',
        'turn_id':'inspection','text':'{"values":[1,2,3]}','observation_source':'browser_network'}]
    successful='import json\ndef adapt(payload):\n    return {"phase":payload["phase"],"decoded":json.loads(payload["events"][0]["text"])}\n'
    invalid='def adapt(payload):\n    return {"decoded":payload["observation"]["events"]}\n'
    def factory(**settings):
        def agent(_):
            prompt=settings['system_prompt']
            assert '{"phase":"inspect","events":[observed events]}' in prompt
            assert 'Read payload["events"] directly' in prompt
            tools=settings['tools']
            decoded=json.loads(tools['test_protocol'](successful,['observed-codec-input']))
            assert decoded['decoded']=={'phase':'inspect','decoded':{'values':[1,2,3]}}
            rejected=json.loads(tools['test_protocol'](invalid,['observed-codec-input']))
            assert rejected['status']=='needs_revision'
            assert rejected['reason']=='protocol_execution_error'
            assert rejected['diagnostic']['exception_type']=='KeyError'
        return agent
    target=BrowserTarget(page_url='https://example.test/chat',driver=website,workspace=tmp_path,
        agent_factory=factory,confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True),output_fn=lambda _:None)
    try:
        with pytest.raises(RuntimeError,match='adapter_not_qualified'):
            target.prepare([],interactive=False,request_count=1)
        assert sorted(path.read_text(encoding='utf-8') for path in (tmp_path/'protocol-candidates').glob('*.py'))==sorted([successful,invalid])
        row=next(row for row in target.ledger.data['events'] if row.get('reason')=='protocol_execution_error')
        assert row['diagnostic']['exception_type']=='KeyError'
        assert set(row['diagnostic'])<={'exception_type','line','column','end_line','end_column','key'}
        assert len(row['source_sha256'])==64
    finally:
        target.close()


def test_restart_reuses_unqualified_program_but_repeats_probe_and_preserves_usage(tmp_path):
    website=Website()
    invalid='def adapt(payload)\n    return {}\n'
    def interrupted_factory(**settings):
        def agent(_):
            assert json.loads(settings['tools']['submit_adapter'](invalid,'Rejected syntax'))['reason']=='protocol_compile_error'
            settings['tools']['submit_adapter'](PROGRAM,'Observed protocol, not tested yet')
        return agent
    options=ExtractionOptions(transfer_approved=True,provider_factory=lambda **_:SimpleNamespace(
        extract=lambda request,**kwargs:{'result':{'status':'final','text':'The complete existing website answer.'},'usage':None}))
    settings=dict(page_url='https://example.test/chat',driver=website,workspace=tmp_path,
        confirmed=True,extraction_options=options,
        reference_reader=lambda turn:'The complete existing website answer.',output_fn=lambda _:None)
    first=BrowserTarget(agent_factory=interrupted_factory,**settings)
    with pytest.raises(RuntimeError,match='adapter_not_qualified'):
        first.prepare([],interactive=False,request_count=1)
    assert first.ledger.data['counts']['model_calls']==1
    assert not website.turns
    first.close()
    restored=[]
    def repaired_factory(**settings):
        def agent(_):
            tools=settings['tools']
            candidate=json.loads(tools['read_candidate']())
            restored.append(candidate)
            assert candidate['source']==PROGRAM
            history=json.loads(tools['read_candidate'](-1))['history']
            assert [row['revision'] for row in history]==[1,2]
            assert history[1]=={'revision':2,'sha256':candidate['sha256'],'failures':[],'reviews':[]}
            assert json.loads(tools['read_candidate'](1))['source']==invalid
            assert json.loads(tools['read_candidate'](2))['source']==PROGRAM
            assert json.loads(tools['read_candidate'](77))['status']=='unavailable'
            assert json.loads(tools['test_adapter']())['status']=='accepted'
        return agent
    target=BrowserTarget(agent_factory=repaired_factory,**settings)
    try:
        target.prepare([],interactive=False,request_count=1)
        assert restored[0]['revision']==2
        assert len(target.evidence['verification'])==1
        assert len(website.turns)==2
        assert target.ledger.data['counts']['model_calls']==4  # Two Agent starts and two extractions.
        assert target.answer('A new formal question after restart','restarted-formal')=='The complete existing website answer.'
        assert target.ledger.data['counts']['requests']==3
    finally:
        target.close()


def test_retesting_a_codec_after_restart_does_not_collide_with_its_private_archive(tmp_path):
    source='def adapt(payload):\n    return {"phase":payload["phase"],"event_count":len(payload["events"])}\n'
    def factory(**settings):
        def agent(_):
            result=json.loads(settings['tools']['test_protocol'](source,[]))
            assert result['decoded']=={'phase':'inspect','event_count':0}
        return agent
    for _ in range(2):
        target=BrowserTarget(page_url='https://example.test/chat',driver=Website(),workspace=tmp_path,
            agent_factory=factory,confirmed=True,extraction_options=ExtractionOptions(transfer_approved=True),
            output_fn=lambda _:None)
        try:
            with pytest.raises(RuntimeError,match='adapter_not_qualified'):
                target.prepare([],interactive=False,request_count=1)
        finally:
            target.close()
    archives=list((tmp_path/'protocol-candidates').glob('*.py'))
    assert len(archives)==2
    assert all(path.read_text(encoding='utf-8')==source for path in archives)
