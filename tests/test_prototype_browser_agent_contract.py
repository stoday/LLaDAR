"""Offline contract checks against the recorded real EIP demonstration."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from lladar.recording_store import RecordingStore
from scripts.prototype_browser_agent import validate_adapter_source


CAPTURE = (Path(__file__).resolve().parents[1] / '.tmp' /
    'eip-live-retry-20261014' / '20261014-075903' /
    'responses.jsonl.work' / 'browser-discovery')


class RecordedSession:
    def __init__(self, demonstration, state):
        self.demonstration = demonstration
        self.state = state

    def __repr__(self):
        return '<private real EIP recording>'


@pytest.fixture
def recorded_session():
    if not (CAPTURE / 'recording' / 'demonstration.json').is_file():
        pytest.skip('private real EIP recording is unavailable')
    demonstration = json.loads((CAPTURE / 'recording' / 'demonstration.json').read_text(
        encoding='utf-8'))
    state = json.loads((CAPTURE / 'state' / 'initial.json').read_text(encoding='utf-8'))
    store = RecordingStore(CAPTURE / 'recording')
    try:
        request = store.event(demonstration['submission_refs'][0])
    finally:
        store.close()
    state['selected_request'] = {
        key: request.get(key) for key in ('url', 'method', 'headers', 'body')
    }
    return RecordedSession(demonstration, state)


def test_generated_adapter_cannot_read_activities_before_submitting(recorded_session):
    candidate = CAPTURE / 'prototype-agent' / 'candidate.py'
    if not candidate.is_file():
        pytest.skip('previous Agent-generated candidate is unavailable')
    source = candidate.read_text(encoding='utf-8')

    with pytest.raises(ValueError, match='prepare_request_before_submit'):
        validate_adapter_source(source, recorded_session.state,
                                recorded_session.demonstration['question'],
                                same_conversation=True)


def test_generated_adapter_must_use_selected_request_authorization(recorded_session):
    source = '''import json
def adapt(payload):
    if payload["phase"] == "prepare":
        return {"ready": True, "actions": [], "context": {}}
    if payload["phase"] == "send":
        selected = payload["state"]["selected_request"]
        return {"actions": [{"kind": "http", "method": selected["method"],
            "url": selected["url"], "headers": {},
            "body": json.dumps({"text": payload["question"]}), "submit": True}],
            "context": {}}
    return {"complete": False, "actions": [], "context": {}}
'''

    with pytest.raises(ValueError, match='submission_authorization_mismatch'):
        validate_adapter_source(source, recorded_session.state,
                                recorded_session.demonstration['question'],
                                same_conversation=True)


def test_generated_adapter_must_use_fresh_request_identity(recorded_session):
    source = '''import json
def adapt(payload):
    if payload["phase"] == "prepare":
        return {"ready": True, "actions": [], "context": {}}
    if payload["phase"] == "send":
        selected = payload["state"]["selected_request"]
        return {"actions": [{"kind": "http", "method": selected["method"],
            "url": selected["url"], "headers": selected["headers"],
            "body": json.dumps({"text": payload["question"],
                "channelData": {"clientActivityID": "stale-demo-id"}}),
            "submit": True}], "context": {}}
    return {"complete": False, "actions": [], "context": {}}
'''

    with pytest.raises(ValueError, match='submission_request_id_mismatch'):
        validate_adapter_source(source, recorded_session.state,
                                recorded_session.demonstration['question'],
                                same_conversation=True)


def test_selected_request_is_required_before_candidate_submission(recorded_session):
    state = dict(recorded_session.state)
    del state['selected_request']
    source = '''def adapt(payload):
    return {"ready": True, "actions": [], "context": {}}
'''

    with pytest.raises(ValueError, match='session_request_not_selected'):
        validate_adapter_source(source, state,
                                recorded_session.demonstration['question'],
                                same_conversation=True)


def test_selected_token_can_be_used_without_embedding_it_in_source(recorded_session):
    source = '''import json
def adapt(payload):
    if payload["phase"] == "prepare":
        return {"ready": True, "actions": [], "context": {}}
    if payload["phase"] == "send":
        selected = payload["state"]["selected_request"]
        return {"actions": [{"kind": "http", "method": selected["method"],
            "url": selected["url"], "headers": selected["headers"],
            "body": json.dumps({"text": payload["question"],
                "channelData": {"clientActivityID": payload["request_id"]}}),
            "submit": True}], "context": {}}
    return {"complete": False, "actions": [], "context": {}}
'''

    plan = validate_adapter_source(source, recorded_session.state,
                                   recorded_session.demonstration['question'],
                                   same_conversation=True)

    assert plan['ready'] is True


def test_recorded_token_cannot_be_copied_into_generated_source(recorded_session):
    headers = recorded_session.state['selected_request']['headers']
    authorization = next(value for key, value in headers.items()
                         if key.lower() == 'authorization')
    token = authorization.split(' ', 1)[-1]
    source = ('def adapt(payload):\n'
              f'    copied_token = {token!r}\n'
              '    return {"ready": True, "actions": [], "context": {}}\n')

    with pytest.raises(ValueError, match='literal_authorization_in_source'):
        validate_adapter_source(source, recorded_session.state,
                                recorded_session.demonstration['question'],
                                same_conversation=True)


def test_prototype_defaults_to_reusing_recorded_conversation(recorded_session):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'prototype_browser_agent.py'
    result = subprocess.run([sys.executable, str(script), '--capture', str(CAPTURE),
                             '--inspect-search'], capture_output=True, text=True,
                            encoding='utf-8', timeout=30)

    assert result.returncode == 0
    assert json.loads(result.stdout)['selected_session'] is True


def test_retry_rejects_invalid_previous_candidate_without_site_calls(recorded_session, tmp_path):
    candidate = CAPTURE / 'prototype-agent' / 'candidate.py'
    if not candidate.is_file():
        pytest.skip('previous Agent-generated candidate is unavailable')
    (tmp_path / 'candidate.json').write_text(json.dumps({
        'source': candidate.read_text(encoding='utf-8'),
        'selected_events': recorded_session.demonstration['submission_refs']}),
        encoding='utf-8')
    (tmp_path / 'selected-request.json').write_text(json.dumps({
        'event_id': recorded_session.demonstration['submission_refs'][0]}),
        encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'prototype_browser_agent.py'
    result = subprocess.run([sys.executable, str(script), '--capture', str(CAPTURE),
                             '--output', str(tmp_path), '--retry-existing-candidate'], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)

    assert result.returncode == 1
    assert json.loads(result.stdout)['reason'] == 'prepare_request_before_submit'
    assert 'live_test_started' not in result.stdout
