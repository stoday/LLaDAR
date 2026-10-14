"""Host orchestration contract; this does not claim a website accepted the adapter."""
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from lladar.recording_store import RecordingStore
from lladar.work_session import atomic_json


def test_final_dom_preview_keeps_middle_answer_control_when_raw_event_is_large():
    from lladar.browser_two_agent import _preview_final_dom

    nodes = [{'tag': 'div', 'selector': f'.noise-{index}',
              'attributes': {'class': 'x' * 500}, 'text': ''}
             for index in range(100)]
    nodes.insert(50, {'tag': 'div', 'selector': 'html > body > div > div',
                      'attributes': {'data-assistant-markdown-root': ''},
                      'text': ''})
    event = {'kind': 'demonstration_final_dom',
             'frames': [{'url': 'https://example.test/chat', 'nodes': nodes}]}
    preview = _preview_final_dom(event, 60000)
    assert preview['complete'] is True
    assert preview['total_nodes'] == 101
    assert preview['frames'][0]['nodes'][50]['attributes'] == {
        'data-assistant-markdown-root': ''}


def test_code_agent_prose_without_submission_stops_after_two_model_calls(
        tmp_path, monkeypatch):
    from lladar import browser_two_agent
    from lladar.independent_adapter import IndependentAdapterError

    store = RecordingStore(tmp_path / 'recording')
    question = 'Question [DEMO-unique]'
    submission = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                               'method': 'POST', 'body': question})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': question, 'submission_refs': [submission]})
    calls = []
    counts = {'model_calls': 0, 'tool_calls': 0}
    target = SimpleNamespace(workspace=tmp_path, store=store, recording_after=0,
        demo_question=question, policy='demonstrated', state={}, candidate=None,
        _tested_hash=None, ledger=SimpleNamespace(check=lambda: None,
            debit=lambda kind, _limit: counts.__setitem__(kind, counts[kind] + 1),
            data={'counts': counts}), model_call_limit=20, max_tool_calls=20,
        _last_progress=0, output_fn=lambda _: None, agent_factory=object(),
        model='gemini:gemini-3.8-flash', env_file='.env', options=None,
        verbose=False, evidence={}, _save=lambda: None)

    def fake_runner(*, tools, model_started, **_):
        model_started()
        calls.append(set(tools))
        if 'select_material' in tools:
            tools['select_material']([submission], 'Question submission')
        # The code Agent returns only prose. The host must not start another turn.

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    with pytest.raises(IndependentAdapterError, match='adapter_not_submitted'):
        browser_two_agent.discover_adapter(target,
            submit_adapter=lambda *_: pytest.fail('no adapter was submitted'),
            test_live=lambda: pytest.fail('no adapter may be tested'),
            expected_destination=None)
    assert calls == [{'select_material'}, {'submit_adapter'}]
    assert counts['model_calls'] == 2


@pytest.mark.parametrize('conversation_policy', ['demonstrated', 'independent'])
def test_material_selection_then_adapter_submission_uses_one_model_call_each_and_host_tests(
        tmp_path, monkeypatch, conversation_policy):
    from lladar import browser_two_agent

    store = RecordingStore(tmp_path / 'recording')
    question = 'What can this service do? [DEMO-unique]'
    answer_fragment = 'A distinctive answer fragment from the middle of the response.'
    for index in range(130):
        store.append({'kind': 'dom_mutation', 'text': 'unrelated ' + str(index)})
    setup = store.append({'kind': 'http_request',
                          'url': 'https://example.test/session', 'method': 'GET'})
    for index in range(260):
        store.append({'kind': 'dom_mutation', 'text': 'intervening ' + str(index)})
    submission = store.append({'kind': 'playwright_http_request', 'url': 'https://example.test/chat',
                               'method': 'POST', 'body': 'x' * 10000 +
                               quote(question, safe='') + 'y' * 10000})
    response = store.append({'kind': 'http_body', 'request_event_id': submission,
                             'text': 'a' * 10000 + answer_fragment + 'z' * 10000})
    for index in range(110):
        store.append({'kind': 'dom_mutation', 'text': 'later ' + str(index)})
    later_setup = store.append({'kind': 'http_body', 'url': 'https://example.test/session',
                                'text': '{"token":"later"}'})
    snapshot = store.append({'kind': 'demonstration_final_snapshot',
                             'text': 'Visible reply: ' + answer_fragment})
    final_dom = store.append({'kind': 'demonstration_final_dom',
                              'frames': [{'nodes': [
                                  *[{'selector': f'.noise-{index}', 'attributes':
                                     {'class': 'x' * 200}, 'text': ''}
                                    for index in range(500)],
                                  {'selector': '[data-answer]', 'text': answer_fragment}]}]})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': question, 'submission_refs': [submission]})
    sequence = []
    counts = {'model_calls': 0, 'tool_calls': 0}
    target = SimpleNamespace(workspace=tmp_path, store=store, recording_after=0,
        demo_question=question, policy=conversation_policy, state={}, candidate=None,
        _tested_hash=None, ledger=SimpleNamespace(check=lambda: None,
            debit=lambda kind, _limit: counts.__setitem__(kind, counts[kind] + 1),
            data={'counts': counts}), model_call_limit=2, max_tool_calls=2,
        _last_progress=0, output_fn=lambda _: None, agent_factory=object(),
        model='gemini:gemini-3.8-flash', env_file='.env', options=None,
        verbose=True, evidence={}, _save=lambda: None)

    def fake_runner(*, tools, model_started, prompt, max_rounds, **_):
        assert max_rounds == 1
        assert _['trace_output'] is None
        model_started()
        if 'select_material' in tools:
            sequence.append('material')
            assert 'submit_adapter' not in tools
            assert 'do not assume POST' in prompt
            assert 'originating request and response' in prompt
            assert submission in prompt and response in prompt and snapshot in prompt
            catalog = json.loads(prompt.split('Indexed recording: ', 1)[1]
                                 .split('\nPreviously selected: ', 1)[0])
            assert next(item for item in catalog if item['event_id'] == setup)['method'] == 'GET'
            assert any(item['event_id'] == later_setup for item in catalog)
            assert any(item['event_id'] == final_dom for item in catalog)
            submitted = next(item for item in catalog if item['event_id'] == submission)
            assert submitted['method'] == 'POST'
            assert submitted['marker_match'] is True
            assert quote('[DEMO-unique]', safe='') in json.dumps(submitted['preview'])
            assert json.loads(tools['select_material'](
                [submission, response, snapshot, final_dom],
                'The response belongs to the submitted question'))['status'] == 'selected'
        else:
            sequence.append('code')
            assert 'test_live' not in tools
            assert set(tools) == {'submit_adapter'}
            assert 'Only json and base64 imports are available' in prompt
            assert 'optional prepare phase' in prompt
            assert 'setup responses in payload.events' in prompt
            assert 'payload.events contains only live current-turn events' in prompt
            assert "return {'actions': [action]}" in prompt
            assert "return {'complete': False}" in prompt
            assert 'The HOST records requests and' in prompt
            assert 'The response belongs to the submitted question' in prompt
            packet = json.loads(prompt.split('Selected evidence packet: ', 1)[1]
                                .split('\nLast trial: ', 1)[0])
            dom_preview = next(item['preview'] for item in packet
                               if item['event_id'] == final_dom)
            assert '[data-answer]' in json.dumps(dom_preview)
            body_preview = next(item['preview'] for item in packet
                                if item['event_id'] == response)
            assert answer_fragment in json.dumps(body_preview, ensure_ascii=False)
            request_preview = next(item['preview'] for item in packet
                                   if item['event_id'] == submission)
            assert request_preview['complete'] is True
            assert quote(question, safe='') in request_preview['text']
            assert json.loads(tools['submit_adapter'](
                'def adapt(payload): return {}', 'selected recording'))['status'] == 'candidate'

    def submit_adapter(source, basis):
        sequence.append('submit')
        target.candidate = {'source': source, 'basis': basis, 'sha256': source}
        atomic_json(tmp_path / 'adapter' / 'candidate.json', target.candidate)
        return json.dumps({'status': 'candidate'})

    def test_live():
        sequence.append('test_live')
        target._tested_hash = target.candidate['sha256']
        result = {'status': 'accepted'}
        atomic_json(tmp_path / 'validation' / 'last-test.json', result)
        return json.dumps(result)

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    browser_two_agent.discover_adapter(target,
        submit_adapter=submit_adapter, test_live=test_live,
        expected_destination=None)
    assert sequence == ['material', 'code', 'submit', 'test_live']
    assert counts['model_calls'] == 2
    assert len(target.evidence['generation_rounds']) == 1
    assert quote(question, safe='') in target.state['selected_request']['body']
    assert target.state['demonstration_question'] == question
    attempts = list((tmp_path / 'adapter' / 'attempts').glob('*.json'))
    assert len(attempts) == 1
    assert json.loads(attempts[0].read_text(encoding='utf-8'))['live_test']['status'] == 'accepted'


def test_catalog_marks_question_sent_over_websocket_without_http_post(tmp_path):
    from lladar.browser_two_agent import _recording_catalog

    store = RecordingStore(tmp_path / 'recording')
    question = 'Question [DEMO-socket]'
    sent = store.append({'kind': 'websocket_sent', 'connection_id': 'demo-socket',
                         'text': quote(question, safe='')})
    received = store.append({'kind': 'websocket_received',
                             'connection_id': 'demo-socket', 'text': 'An answer'})

    catalog = _recording_catalog(store, 0, '[DEMO-socket]', [sent])
    submission = next(item for item in catalog if item['event_id'] == sent)
    assert submission['kind'] == 'websocket_sent'
    assert submission['marker_match'] is True
    assert quote('[DEMO-socket]', safe='') in json.dumps(submission['preview'])
    assert any(item['event_id'] == received for item in catalog)


def test_code_agent_receives_successful_retry_when_demonstration_first_failed(
        tmp_path, monkeypatch):
    from lladar import browser_two_agent

    store = RecordingStore(tmp_path / 'recording')
    question = 'How does caching work?'
    failed = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                           'method': 'POST', 'body': question, 'headers': {'X-Attempt': 'first'}})
    store.append({'kind': 'http_response', 'request_event_id': failed, 'status': 403})
    succeeded = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                              'method': 'POST', 'body': question, 'headers': {'X-Attempt': 'second'}})
    store.append({'kind': 'http_response', 'request_event_id': succeeded, 'status': 200})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': question, 'submission_refs': [failed, succeeded]})
    counts = {'model_calls': 0, 'tool_calls': 0}
    target = SimpleNamespace(workspace=tmp_path, store=store, recording_after=0,
        demo_question=question, policy='demonstrated', state={}, candidate=None,
        _tested_hash=None, ledger=SimpleNamespace(check=lambda: None,
            debit=lambda kind, _limit: counts.__setitem__(kind, counts[kind] + 1),
            data={'counts': counts}), model_call_limit=2, max_tool_calls=2,
        _last_progress=0, output_fn=lambda _: None, agent_factory=object(),
        model='gemini:gemini-3.8-flash', env_file='.env', options=None,
        verbose=False, evidence={}, _save=lambda: None)

    def fake_runner(*, tools, model_started, **_):
        model_started()
        if 'select_material' in tools:
            tools['select_material']([failed, succeeded], 'First request failed; retry succeeded')
        else:
            assert target.state['selected_request']['headers']['X-Attempt'] == 'second'
            tools['submit_adapter']('def adapt(payload): return {}', 'successful retry')

    def submit_adapter(source, basis):
        target.candidate = {'source': source, 'basis': basis, 'sha256': source}
        return json.dumps({'status': 'candidate'})

    def test_live():
        target._tested_hash = target.candidate['sha256']
        return json.dumps({'status': 'accepted'})

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    browser_two_agent.discover_adapter(target,
        submit_adapter=submit_adapter, test_live=test_live, expected_destination=None)


@pytest.mark.parametrize('first_failure,expected_phases,expected_selected', [
    ('adapter_evidence_invalid', 5, 3),
    ('submission_unconfirmed', 3, 2),
    ('completion_unconfirmed', 4, 2),
])
def test_repair_routes_evidence_failures_to_material_and_code_failures_to_adapter(
        tmp_path, monkeypatch, first_failure, expected_phases, expected_selected):
    from lladar import browser_two_agent

    store = RecordingStore(tmp_path / 'recording')
    question = 'What can this service do? [DEMO-unique]'
    submission = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                               'method': 'POST', 'headers': {'Authorization': 'Bearer private'},
                               'body': question})
    first_reply = store.append({'kind': 'http_body', 'request_event_id': submission,
                                'text': '{"answer":"first"}'})
    more_evidence = store.append({'kind': 'http_body', 'request_event_id': submission,
                                  'text': '{"answer":"final"}'})
    atomic_json(tmp_path / 'recording' / 'demonstration.json',
                {'question': question, 'submission_refs': [submission]})
    counts = {'model_calls': 0, 'tool_calls': 0}
    def debit(kind, _limit):
        counts[kind] += 1
    target = SimpleNamespace(workspace=tmp_path, store=store,
        recording_after=0, demo_question=question, policy='demonstrated',
        state={'allowed_origins': ['https://example.test']}, candidate=None,
        _tested_hash=None, ledger=SimpleNamespace(check=lambda: None,
            debit=debit, data={'counts': counts}), model_call_limit=20,
        max_tool_calls=30, _last_progress=0, output_fn=lambda _: None,
        agent_factory=object(), model='gemini:gemini-3.8-flash', env_file='.env',
        options=None, verbose=False, evidence={}, _save=lambda: None)
    phases = []
    trials = []
    requested_more = []

    def fake_runner(*, tools, model_started, tool_started, prompt, max_rounds, **_):
        assert max_rounds == 1
        model_started()
        phases.append(set(tools))
        if 'select_material' in tools:
            assert 'submit_adapter' not in tools
            picked = [submission, first_reply] if not requested_more else [more_evidence]
            assert json.loads(tools['select_material'](picked, 'Useful send and reply evidence'))['status'] == 'selected'
        else:
            assert set(tools) == ({'submit_adapter'} if not trials or requested_more or
                                  phases[-2] == {'select_material'}
                                  else {'submit_adapter', 'request_more_material'})
            if (first_failure == 'adapter_evidence_invalid' and len(trials) == 1
                    and not requested_more):
                requested_more.append(True)
                assert 'adapter_evidence_invalid' in prompt
                assert json.loads(tools['request_more_material'](
                    'Missing answer evidence for this request'))['status'] == 'requested'
                return
            if len(trials) == 1:
                assert first_failure in prompt
            assert json.loads(tools['submit_adapter']('source-' + str(len(trials)), 'evidence'))['status'] == 'candidate'

    def submit_adapter(source, basis):
        target.candidate = {'source': source, 'basis': basis, 'sha256': source}
        atomic_json(tmp_path / 'adapter' / 'candidate.json', target.candidate)
        return json.dumps({'status': 'candidate'})

    def test_live():
        trials.append(target.candidate['source'])
        accepted = len(trials) == 2
        if accepted:
            target._tested_hash = target.candidate['sha256']
        result = {'status': 'accepted' if accepted else 'rejected',
                  'reason': None if accepted else first_failure,
                  'diagnostic': {'missing_refs': ['missing-event']}}
        atomic_json(tmp_path / 'validation' / 'last-test.json', result)
        return json.dumps(result)

    monkeypatch.setattr(browser_two_agent, 'run_browser_agent', fake_runner)
    browser_two_agent.discover_adapter(target,
        submit_adapter=submit_adapter, test_live=test_live,
        expected_destination=None)
    assert len(trials) == 2
    assert len(phases) == expected_phases
    assert counts['model_calls'] == expected_phases
    assert json.loads((tmp_path / 'adapter' / 'selected-material.json').read_text(
        encoding='utf-8'))['event_ids'] == [submission, first_reply, more_evidence][:expected_selected]
    assert len(target.evidence['generation_rounds']) == 2
    assert target.evidence['generation_rounds'][-1]['accepted'] is True
    assert target.state['selected_request']['headers']['Authorization'] == 'Bearer private'


