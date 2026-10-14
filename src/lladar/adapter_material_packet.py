"""Offer a bounded recording index; the Agent interprets website semantics."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .independent_adapter import origin


_BOOTSTRAP = re.compile(r'auth|login|sso|token|register|conversation|session|connect', re.I)
_CLIENT_TERMS = ('conversation', 'token', 'register', 'auth')
_EVENT_KEYS = ('event_id', 'kind', 'url', 'method', 'status', 'headers', 'body',
               'text', 'binary_b64', 'request_event_id', 'connection_id', 'resource_type')


def _events(store, kind, *, after=0):
    cursor = after
    while True:
        batch = store.query_recording(kind=kind, after=cursor, limit=200)
        for item in batch['events']:
            yield item
        if not batch['has_more']:
            return
        cursor = batch['next_after']


def _reduced(event):
    return {key: event[key] for key in _EVENT_KEYS if key in event}


def build_adapter_material_packet(store, state, question, submission_refs, *, max_chars=48000):
    """Return an initial sample around the demonstration and saved state.

    Selection uses timestamps, references, URL validity and size, never a
    website-specific reply schema. The Agent can query/read beyond this sample.
    """
    submissions = set(submission_refs)
    if not submissions:
        raise ValueError('demonstration_submission_missing')
    first_submit = min(store.sequence(ref) for ref in submissions)
    submitted_events = [store.event(ref) for ref in submission_refs]
    allowed = {origin(url) for url in state.get('allowed_origins', [])}
    requests = []
    for item in _events(store, 'http_request'):
        if item['sequence'] > first_submit and item['event_id'] not in submissions:
            continue
        event = store.event(item['event_id'])
        url = urlsplit(event.get('url', ''))
        if not url.scheme or not url.hostname or origin(event['url']) not in allowed:
            continue
        if event.get('method') == 'OPTIONS' or url.path.lower().endswith(
                ('.css', '.ico', '.svg', '.png', '.jpg', '.jpeg', '.woff', '.woff2')):
            continue
        if item['event_id'] in submissions or (_BOOTSTRAP.search(url.path)
                and not url.path.lower().endswith(('.js', '.html', '.aspx'))):
            requests.append((item['sequence'], event))
    # Keep the most recent instance of each bootstrap operation, in recorded order.
    latest = {}
    for sequence, event in requests:
        url = urlsplit(event['url'])
        key = (event.get('method'), url.netloc, url.path)
        latest[key] = (sequence, event)
    chosen = sorted(latest.values(), key=lambda pair: pair[0])[-12:]
    selected = []
    for _, event in chosen:
        selected.append(_reduced(event))
        related = store.query_recording(request_id=event['event_id'], kind='http_body', limit=2)
        for body in related['events']:
            selected.append(_reduced(store.event(body['event_id'])))
    selected_ids = {event['event_id'] for event in selected}
    for event in submitted_events:
        if event['event_id'] not in selected_ids:
            selected.append(_reduced(event))
            selected_ids.add(event['event_id'])

    marker_start = question.rfind('[DEMO-')
    marker = question[marker_start:] if marker_start >= 0 else question
    matches = store.query_recording(query=marker, kind='http_body',
                                    after=first_submit - 1, limit=2)['events']
    for item in matches:
        event = store.event(item['event_id'])
        if event['event_id'] not in selected_ids:
            selected.append(_reduced(event))
            selected_ids.add(event['event_id'])
    # Show the first observations and the largest bounded bodies soon after the
    # marker. The Agent decides whether any of them is a reply.
    window_start = store.sequence(matches[0]['event_id']) if matches else first_submit
    window = []
    for item in _events(store, 'http_body', after=window_start):
        event = store.event(item['event_id'])
        url = urlsplit(event.get('url', ''))
        if url.scheme in {'http', 'https'} and url.hostname and origin(event['url']) in allowed:
            window.append((item['sequence'], event))
        if len(window) == 40:
            break
    ordered = window[:2] + sorted(window[2:], key=lambda pair: -len(pair[1].get('text') or ''))[:4]
    for _, event in sorted(ordered, key=lambda pair: pair[0]):
        if event['event_id'] not in selected_ids:
            selected.append(_reduced(event))
            selected_ids.add(event['event_id'])
    socket_ids = {event.get('connection_id') for event in submitted_events
                  if event.get('kind') == 'websocket_sent' and event.get('connection_id')}
    if socket_ids:
        received = 0
        for item in _events(store, 'websocket_received'):
            if item['sequence'] <= first_submit:
                continue
            event = store.event(item['event_id'])
            if event.get('connection_id') in socket_ids:
                selected.append(_reduced(event))
                received += 1
                if received == 4:
                    break

    client_sources = []
    for item in _events(store, 'client_resource'):
        event = store.event(item['event_id'])
        if event.get('source_event_id') and not event.get('text'):
            event = store.event(event['source_event_id'])
        url = urlsplit(event.get('url', ''))
        source = event.get('text') or ''
        if not url.scheme or not url.hostname or origin(event['url']) not in allowed or not url.path.lower().endswith('.js') or not isinstance(source, str):
            continue
        hits = sum(term in source.lower() for term in _CLIENT_TERMS)
        if hits:
            protocol_hits = sum(term in source.lower() for term in _CLIENT_TERMS)
            name = url.path.rsplit('/', 1)[-1].lower()
            helper = any(term in name for term in ('config', 'initial', 'loader'))
            rank = 0 if re.search(r'chat|bot|conversation', url.path, re.I) else 1
            client_sources.append((rank, -protocol_hits, helper, len(source),
                                   item['sequence'], event, source))
    client_regions = []
    for index, (*_, event, source) in enumerate(sorted(client_sources)[:2]):
        path = urlsplit(event['url']).path
        allowance = 2 if index == 0 else 1
        for term in ('register', 'conversation', 'token', 'auth'):
            match = re.search(term, source, re.I)
            if match:
                start = max(0, match.start() - 800)
                client_regions.append({'event_id': event['event_id'], 'path': path,
                                       'offset': start, 'text': source[start:match.end()+1200]})
                allowance -= 1
                if allowance == 0:
                    break
    packet = {'question': question, 'recorded_events': selected,
              'saved_state': state, 'client_code_regions': client_regions}
    encoded = json.dumps(packet, ensure_ascii=False)
    if len(encoded) > max_chars:
        # Keep recorded traffic and state; client excerpts can be read by tool.
        packet['client_code_regions'] = []
        encoded = json.dumps(packet, ensure_ascii=False)
    if len(encoded) > max_chars:
        raise ValueError('adapter_material_packet_limit')
    return packet
