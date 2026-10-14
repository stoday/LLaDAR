"""Qualify a native JSON full-answer path against three independent references."""
from __future__ import annotations

import json


class NativeJsonReference:
    def __init__(self):
        self.path = None
        self.profile = None
        self.turn_ids = set()

    @staticmethod
    def _body(turn):
        submitted = [e for e in turn['events'] if e['kind'] == 'http_request' and e.get('submit')]
        if len(submitted) != 1:
            return None
        ref = submitted[0]['event_id']
        ended = [e for e in turn['events'] if e['kind'] == 'http_end' and e.get('request_event_id') == ref]
        bodies = [e for e in turn['events'] if e['kind'] == 'http_body' and e.get('request_event_id') == ref]
        if len(ended) != 1 or len(bodies) != 1 or not isinstance(bodies[0].get('text'), str):
            return None
        try:
            return json.loads(bodies[0]['text'])
        except ValueError:
            return None

    def qualify(self, turn):
        """Call only AFTER an independent oracle or human verified exact text."""
        body = self._body(turn)
        profile = self._shape(body)
        matches = []
        def visit(value, path):
            if isinstance(value, str) and value == turn['answer']:
                matches.append(path)
            elif isinstance(value, dict):
                for key, child in value.items():
                    visit(child, (*path, key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, (*path, index))
        visit(body, ())
        if len(matches) != 1:
            return
        if self.path != matches[0] or self.profile != profile:
            self.path, self.profile, self.turn_ids = matches[0], profile, set()
        self.turn_ids.add(turn['request_id'])

    def read(self, turn):
        directline = self._directline_card_answer(turn)
        if directline is not None:
            return directline
        if len(self.turn_ids) < 3:
            return None
        value = self._body(turn)
        if self._shape(value) != self.profile:
            return None
        try:
            for key in self.path:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            return None
        return value if isinstance(value, str) else None

    @staticmethod
    def _directline_card_answer(turn):
        """Read one unambiguous bot answer correlated to a submitted activity ID."""
        events = turn.get('events', [])
        submitted = [event for event in events if event.get('kind') == 'http_request'
                     and event.get('submit') is True]
        if len(submitted) != 1:
            return None
        submission_ref = submitted[0].get('event_id')
        ids = set()
        for event in events:
            if event.get('kind') != 'http_body' or event.get('request_event_id') != submission_ref:
                continue
            try:
                value = json.loads(event.get('text') or '')
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get('id'), str):
                ids.add(value['id'])
        if len(ids) != 1:
            return None
        completed_requests = {event.get('request_event_id') for event in events
                              if event.get('kind') in {'http_end', 'http_stopped'}}
        replies = {}
        for event in events:
            if event.get('kind') != 'http_body' or event.get('request_event_id') == submission_ref:
                continue
            if event.get('request_event_id') not in completed_requests:
                continue
            try:
                value = json.loads(event.get('text') or '')
            except ValueError:
                continue
            if not isinstance(value, dict) or not isinstance(value.get('activities'), list):
                continue
            for activity in value['activities']:
                if isinstance(activity, dict) and activity.get('replyToId') in ids and activity.get('id'):
                    replies[activity['id']] = activity
        cards = [item['content'] for activity in replies.values()
                 for item in (activity.get('attachments') or [])
                 if isinstance(item, dict) and isinstance(item.get('content'), dict)
                 and item['content'].get('type') == 'AdaptiveCard']
        if len(cards) != 1:
            return None
        blocks = []
        def visit(value):
            if isinstance(value, dict):
                if (value.get('type') == 'TextBlock' and isinstance(value.get('text'), str)
                        and value['text'].strip()):
                    blocks.append(value)
                for key, child in value.items():
                    if key not in {'selectAction', 'actions'}:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(cards[0])
        blocks.sort(key=lambda block: len(block['text']), reverse=True)
        if not blocks or len(blocks[0]['text']) < 20 or (len(blocks) > 1
                and len(blocks[0]['text']) < 2 * len(blocks[1]['text'])):
            return None
        longest = blocks[0]
        def path_to(value):
            if value is longest:
                return [value]
            children = (value.values() if isinstance(value, dict) else value
                        if isinstance(value, list) else ())
            for child in children:
                path = path_to(child)
                if path:
                    return [value, *path]
            return None
        def contains_action(value):
            if isinstance(value, dict):
                return ('selectAction' in value or 'actions' in value or
                        any(contains_action(child) for child in value.values()))
            return isinstance(value, list) and any(contains_action(child) for child in value)
        for ancestor in reversed(path_to(cards[0])[:-1]):
            if contains_action(ancestor):
                continue
            grouped = []
            def collect_text(value):
                if isinstance(value, dict):
                    if value.get('type') == 'TextBlock' and isinstance(value.get('text'), str):
                        grouped.append(value['text'].strip())
                    for child in value.values():
                        collect_text(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_text(child)
            collect_text(ancestor)
            if 2 <= len(grouped) <= 3 and all(grouped):
                return '\n\n'.join(grouped)
        return longest['text'].strip()

    @classmethod
    def _shape(cls, value):
        if isinstance(value, dict):
            return ('object', tuple((key, cls._shape(child)) for key, child in sorted(value.items())))
        if isinstance(value, list):
            return ('array', tuple(cls._shape(child) for child in value))
        return type(value).__name__
