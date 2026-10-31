"""Bound adapter-model requests and retain metadata without prompts or answers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from time import monotonic

from .exceptions import ProviderError

MODEL_EVENT_PREFIX = 'LLADAR_MODEL_EVENT '
PROVIDER_CODES = {'provider_timeout', 'provider_transport_error', 'provider_rate_limited',
                  'provider_authentication_failed', 'provider_model_unavailable', 'provider_unavailable'}


def uses_low_thinking(model: str) -> bool:
    return model.lower().split(':')[-1].split('/')[-1] == 'gemini-3.8-flash'


def provider_error_code(error: BaseException) -> str | None:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        status = getattr(error, 'status_code', None)
        if status is None:
            status = getattr(error, 'code', None)
        if isinstance(status, int):
            if status in (401, 403):
                return 'provider_authentication_failed'
            if status == 404:
                return 'provider_model_unavailable'
            if status == 429:
                return 'provider_rate_limited'
            if status >= 500:
                return 'provider_unavailable'
        names = {cls.__name__ for cls in type(error).__mro__}
        if names & {'TimeoutError', 'TimeoutException', 'APITimeoutError', 'DeadlineExceeded'}:
            return 'provider_timeout'
        if names & {'TransportError', 'RemoteProtocolError', 'APIConnectionError', 'ServiceUnavailable'}:
            return 'provider_transport_error'
        error = error.__cause__
    return None


def configure_agent(agent, *, timeout: float, events: Path, stage: str):
    """Configure the same client captured by Akasha's already-built graph."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('model timeout must be positive and finite')
    client = getattr(agent, 'model_obj', None)
    if client is None:  # Injected offline agents have no SDK client.
        return agent
    from langchain_core.callbacks import BaseCallbackHandler
    from .providers.akasha import disable_model_retries, _client_nodes

    # Gemini 3.8 cannot disable reasoning. False in Akasha only hides thoughts;
    # explicitly set the actual SDK request to its lowest supported effort.
    if 'google' in str(getattr(client, '_llm_type', '')).lower() and uses_low_thinking(str(getattr(client, 'model', ''))):
        field = 'reasoning_effort' if 'reasoning_effort' in type(client).model_fields else 'thinking_level'
        setattr(client, field, 'low')
        client.include_thoughts = False

    configured = False
    for name in ('timeout', 'request_timeout'):
        if hasattr(client, name):
            setattr(client, name, timeout)
            configured = True
            break
    # OpenAI/Anthropic cache SDK clients during model construction, unlike
    # Google's per-request HttpOptions. Update those clients as well.
    for sdk, _depth, _edge in _client_nodes(client):
        if type(sdk).__module__.split('.')[0] in {'openai', 'anthropic', 'httpx', 'httpx2'} and hasattr(sdk, 'timeout'):
            sdk.timeout = timeout
            configured = True
    if not configured:
        raise ProviderError('model_timeout_configuration_unsupported')
    disable_model_retries(client)

    class Events(BaseCallbackHandler):
        def __init__(self):
            self.started = {}

        def write(self, event, run_id, **fields):
            row = {'event': event, 'stage': stage, 'call_id': str(run_id), **fields}
            events.parent.mkdir(parents=True, exist_ok=True)
            with events.open('a', encoding='utf-8') as output:
                output.write(json.dumps(row) + '\n')

        def on_chat_model_start(self, _serialized, _messages, *, run_id, **_kwargs):
            self.started[run_id] = monotonic()
            fields = {'timeout_seconds': timeout}
            level = getattr(client, 'thinking_level', None)
            if level in {'minimal', 'low', 'medium', 'high'}:
                fields['thinking_level'] = level
            if type(getattr(client, 'include_thoughts', None)) is bool:
                fields['include_thoughts'] = client.include_thoughts
            self.write('start', run_id, **fields)

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
            self.write('end', run_id, **fields)

        def on_llm_error(self, error, *, run_id, **_kwargs):
            self.write('error', run_id, elapsed_seconds=self.elapsed(run_id),
                       error_code=provider_error_code(error) or 'model_error')

    client.callbacks = [*(getattr(client, 'callbacks', None) or []), Events()]
    return agent


def call_agent(agent, prompt):
    def failure(error):
        code = provider_error_code(error)
        if code:
            raise ProviderError(code) from error
        raise error
    try:
        response = agent(prompt)
    except Exception as error:
        failure(error)
    if isinstance(response, str):
        return response

    def stream():
        try:
            yield from response
        except Exception as error:
            failure(error)
    return stream()


def target_provider_failure(text: str, request_id: str) -> str | None:
    """Only classify explicit, correlated diagnostics; do not guess from prose."""
    for line in text.splitlines():
        if MODEL_EVENT_PREFIX not in line:
            continue
        try:
            row = json.loads(line.split(MODEL_EVENT_PREFIX, 1)[1])
        except ValueError:
            continue
        if (isinstance(row, dict) and row.get('event') == 'error'
                and row.get('request_id') == request_id and row.get('error_code') in PROVIDER_CODES):
            return 'target_' + row['error_code']
    return None


def stop_unrepairable(result: dict) -> None:
    if result.get('ok'):
        return
    kind = result.get('failure_kind', '')
    if kind == 'target_execution_timeout' or kind.startswith('target_provider_'):
        raise ProviderError(kind)
