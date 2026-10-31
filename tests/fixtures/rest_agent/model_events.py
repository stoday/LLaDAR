"""Safe provider diagnostics, independent of the LLaDAR controller installation."""
import json
import os
import sys
from time import monotonic

from langchain_core.callbacks import BaseCallbackHandler
from workflow import record


class ModelEvents(BaseCallbackHandler):
    def __init__(self):
        self.started = {}

    def emit(self, event, **fields):
        row = {'event': event, 'request_id': os.getenv('LLADAR_REQUEST_ID', ''), **fields}
        record('model_' + event, json.dumps(row))
        print('LLADAR_MODEL_EVENT ' + json.dumps(row), file=sys.stderr, flush=True)

    def on_chat_model_start(self, _serialized, _messages, *, run_id, **_kwargs):
        self.started[run_id] = monotonic()
        self.emit('start', timeout_seconds=90, attempts=1)

    def elapsed(self, run_id):
        return round(monotonic() - self.started.pop(run_id, monotonic()), 3)

    def on_llm_end(self, response, *, run_id, **_kwargs):
        fields = {'elapsed_seconds': self.elapsed(run_id)}
        for group in response.generations:
            for generation in group:
                message = getattr(generation, 'message', None)
                usage = getattr(message, 'usage_metadata', None) or {}
                fields['usage'] = {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
                                   if type(usage.get(key)) is int}
                reasoning = (usage.get('output_token_details') or {}).get('reasoning')
                if type(reasoning) is int:
                    fields['thinking_tokens'] = reasoning
                finish = (getattr(message, 'response_metadata', None) or {}).get('finish_reason')
                if finish in {'STOP', 'MAX_TOKENS', 'SAFETY', 'stop', 'length', 'tool_calls'}:
                    fields['finish_reason'] = finish
        self.emit('end', **fields)

    def on_llm_error(self, error, *, run_id, **_kwargs):
        code, seen, current = 'model_error', set(), error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            names = {cls.__name__ for cls in type(current).__mro__}
            status = getattr(current, 'status_code', getattr(current, 'code', None))
            if status in (401, 403):
                code = 'provider_authentication_failed'
            elif status == 404:
                code = 'provider_model_unavailable'
            elif status == 429:
                code = 'provider_rate_limited'
            elif isinstance(status, int) and status >= 500:
                code = 'provider_unavailable'
            elif names & {'TimeoutError', 'TimeoutException', 'APITimeoutError', 'DeadlineExceeded'}:
                code = 'provider_timeout'
            elif names & {'TransportError', 'RemoteProtocolError', 'APIConnectionError', 'ServiceUnavailable'}:
                code = 'provider_transport_error'
            if code != 'model_error':
                break
            current = current.__cause__
        self.emit('error', error_code=code, elapsed_seconds=self.elapsed(run_id))
