"""Large real-page DOM changes stay available without exhausting the event index."""
import json
import base64
import random

from lladar.recording_store import RecordingStore


def test_large_dom_material_is_lossless_and_index_stays_bounded(tmp_path):
    store = RecordingStore(tmp_path / 'recording')
    event = {'event_id': 'capture-one-event-1', 'kind': 'dom_mutation',
             'changes': [{'ref': 'body', 'after': 'lots of content ' * 800000}]}
    event_id = store.append(event)
    assert store.event(event_id)['changes'] == event['changes']
    page = store.read_material(event_id, offset=120000, limit=100)
    assert page['text'] == json.dumps({**event, 'utc_time': store.event(event_id)['utc_time'],
                                      'monotonic_seconds': store.event(event_id)['monotonic_seconds']},
                                     ensure_ascii=False, allow_nan=False)[120000:120100]
    indexed = store.query_recording(kind='dom_mutation')['events'][0]
    assert indexed['size'] < 4 * 1024 * 1024
    assert (tmp_path / 'recording' / 'blobs').is_dir()


def test_fifo_eviction_only_discards_old_dom_payloads(tmp_path, monkeypatch):
    monkeypatch.setenv('LLADAR_MAX_RECORDING_BYTES', '450000')
    store = RecordingStore(tmp_path / 'recording')
    wire = base64.b64encode(random.Random(42).randbytes(200000)).decode()
    first = store.append({'kind': 'dom_mutation', 'changes': [{'after': wire + 'a'}]})
    request = store.append({'kind': 'http_request', 'url': 'https://example.test/chat',
                            'body': 'question [DEMO-one]'})
    second = store.append({'kind': 'dom_mutation', 'changes': [{'after': wire + 'b'}]})
    third = store.append({'kind': 'dom_mutation', 'changes': [{'after': wire + 'c'}]})
    assert store.event(first)['evicted'] is True
    assert store.read_material(first)['evicted'] is True
    assert store.event(request)['body'] == 'question [DEMO-one]'
    assert store.event(second)['changes'][0]['after'].endswith('b')
    assert store.event(third)['changes'][0]['after'].endswith('c')


def test_encoded_question_marker_is_searchable_as_plain_text(tmp_path):
    store = RecordingStore(tmp_path / 'recording')
    ref = store.append({'kind': 'http_request', 'body':
        'question=What%20is%20this%3F%20%5BDEMO-encoded%5D'})
    found = store.query_recording(query='[DEMO-encoded]')
    assert [event['event_id'] for event in found['events']] == [ref]
