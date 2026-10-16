"""Isolated Akasha execution; browser tools and accounting stay on the owner."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from functools import wraps
import json
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time

from .resource_limits import resource_limit
from .live_status import status_activity

_HOST_REASONS = frozenset({
    'discovery_budget_exhausted', 'discovery_time_exhausted', 'checkpoint_failed', 'checkpoint_integrity_failed',
    'submission_already_attempted', 'adapter_invalid', 'adapter_output_invalid', 'adapter_setup_incomplete',
    'calibration_required',
    'manual_calibration_not_observed','manual_calibration_response_missing',
    'adapter_integrity_failed',
    'adapter_origin_mismatch', 'adapter_runtime_mismatch', 'adapter_missing', 'adapter_not_qualified',
    'sensitive_protocol_output', 'sensitive_response', 'unqualified_submission', 'unmarked_submission',
    'submission_phase_invalid', 'probe_session_not_isolated', 'conversation_policy_violation',
    'submission_not_attempted', 'submission_action_invalid', 'browser_action_page_not_active',
    'tool_material_limit', 'sensitive_resource_url', 'sensitive_adapter_source',
    'response_size_limit', 'completion_unconfirmed', 'submission_unconfirmed', 'request_identity_mismatch',
    'response_material_missing', 'response_source_unconfirmed', 'formal_session_lost',
    'pending_turn_observation_unavailable', 'reference_mismatch', 'consent_required',
    'backend_origin_unobserved', 'event_page_invalid', 'browser_authentication_required',
    'browser_inspection_snapshot_stale','browser_inspection_snapshot_invalid',
    'browser_inspection_cursor_invalid','browser_inspection_frame_unknown','browser_inspection_field_unknown',
    'browser_inspection_limit_invalid','browser_inspection_limit_too_small',
    'browser_url_invalid', 'browser_timeout_invalid', 'browser_resource_limit_invalid',
    'browser_owner_thread_required', 'browser_not_open', 'browser_request_budget_exhausted',
    'browser_connection_lost',
    'browser_request_budget_already_enabled', 'browser_request_budget_invalid', 'browser_open_failed',
    'browser_dom_limit', 'browser_turn_id_invalid', 'browser_turn_page_lost', 'browser_turn_unknown',
    'browser_event_unknown', 'browser_page_limit_invalid', 'browser_client_resource_not_observed',
    'browser_submission_without_turn', 'browser_turn_already_submitted', 'browser_wait_invalid',
    'browser_origin_not_authorized', 'browser_page_not_owned', 'browser_page_unknown',
    'browser_action_unknown', 'browser_ref_stale', 'browser_private_control',
    'browser_action_failed', 'browser_action_failed_after_submission', 'browser_request_ref_invalid',
    'browser_submission_invalid', 'browser_request_start_failed', 'browser_request_start_failed_after_submission',
    'browser_websocket_page_not_active', 'browser_websocket_ambiguous', 'browser_websocket_unknown',
    'browser_binary_invalid', 'browser_websocket_send_failed_after_submission',
    'browser_binding_json_invalid', 'browser_binding_path_invalid', 'browser_binding_path_missing',
    'browser_body_binding_value_invalid', 'browser_body_bindings_invalid',
    'browser_value_reference_invalid', 'browser_value_reference_scope_invalid',
    'browser_agent_time_exhausted',
    'browser_agent_budget_exhausted',
    'browser_agent_no_progress',
    'semantic_review_invalid',
    'protocol_source_invalid', 'protocol_source_limit', 'protocol_payload_invalid', 'protocol_input_limit',
    'protocol_output_invalid', 'protocol_output_limit', 'protocol_compile_error', 'protocol_execution_error',
    'protocol_access_denied', 'protocol_memory_limit', 'protocol_timeout', 'protocol_runtime_unavailable',
    'protocol_runtime_failed', 'protocol_resource_url_invalid', 'protocol_resource_private_address',
    'protocol_resource_redirect_invalid', 'protocol_resource_unavailable',
    'protocol_resource_encoding_unsupported', 'protocol_resource_limit', 'protocol_resource_redirect_limit',
    'protocol_resource_not_text', 'protocol_dependency_invalid', 'protocol_dependency_integrity_failed',
    'protocol_dependency_limit', 'protocol_dependency_unavailable', 'protocol_package_asset_unavailable',
    'protocol_resource_page_invalid',
    'protocol_projection_input_invalid','protocol_projection_limit','protocol_projection_unavailable',
})
_FATAL_HOST_REASONS = frozenset({
    'discovery_budget_exhausted', 'discovery_time_exhausted', 'checkpoint_failed', 'checkpoint_integrity_failed',
    'browser_request_budget_exhausted', 'browser_authentication_required',
    'browser_connection_lost',
    'browser_agent_time_exhausted',
    'browser_agent_no_progress',
    'semantic_review_invalid',
    'extraction_budget_exhausted', 'extraction_time_exhausted',
    'provider_destination_changed', 'provider_destination_unconfirmed',
})


class BrowserAgentError(RuntimeError):
    """Only host-defined codes, never provider exceptions or private output."""
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in {
            'browser_agent_failed', 'browser_agent_cancelled', 'browser_agent_time_exhausted',
            'browser_agent_configuration_failed', 'browser_agent_rpc_invalid', 'browser_agent_ipc_limit',
            'browser_agent_destination_changed', 'browser_agent_destination_unconfirmed',
            'browser_agent_budget_exhausted', 'browser_agent_checkpoint_failed',
            'browser_agent_no_progress',
            'browser_authentication_required',
        } else 'browser_agent_failed'
        super().__init__(self.reason)


def _safe_tool_error(error):
    from .answer_extraction import ExtractionError
    from .protocol_runtime import ProtocolRuntimeError
    from .independent_adapter import IndependentAdapterError
    if isinstance(error, IndependentAdapterError):
        return error.reason
    if isinstance(error, ExtractionError):
        return error.reason
    if isinstance(error, ProtocolRuntimeError):
        return error.code if error.code in _HOST_REASONS else 'tool_failed'
    reason = getattr(error, 'reason', getattr(error, 'code', None))
    if reason is None:
        from .browser_discovery_driver import BrowserDiscoveryError
        if isinstance(error, BrowserDiscoveryError) and error.args:
            reason = error.args[0]
    if type(reason) is str and reason in _HOST_REASONS:
        return reason
    return 'tool_failed'


def _pump_owner(pump):
    try:
        pump()
    except Exception as error:
        reason = _safe_tool_error(error)
        if reason in _FATAL_HOST_REASONS:
            raise BrowserAgentError(_failure_reason(_HostCallError(reason))) from None
        raise


_silent_threads = set()
_stream_lock = threading.Lock()


class _ThreadSink:
    """Discard injected test-Agent output while retaining owner-thread output."""
    def __init__(self, stream, binary=False):
        self.stream, self.binary = stream, binary
    def write(self, value):
        if threading.get_ident() in _silent_threads:
            return len(value)
        target = self.stream.buffer if self.binary else self.stream
        return target.write(value)
    def flush(self):
        if threading.get_ident() not in _silent_threads:
            target = self.stream.buffer if self.binary else self.stream
            return target.flush()
    @property
    def buffer(self):
        return _ThreadSink(self.stream, binary=True)
    def __getattr__(self, name):
        return getattr(self.stream, name)


def _silence_thread(start):
    with _stream_lock:
        identity = threading.get_ident()
        if start:
            _silent_threads.add(identity)
            if not isinstance(sys.stdout, _ThreadSink):
                sys.stdout = _ThreadSink(sys.stdout)
            if not isinstance(sys.stderr, _ThreadSink):
                sys.stderr = _ThreadSink(sys.stderr)
        else:
            _silent_threads.discard(identity)
            if not _silent_threads:
                if isinstance(sys.stdout, _ThreadSink):
                    sys.stdout = sys.stdout.stream
                if isinstance(sys.stderr, _ThreadSink):
                    sys.stderr = sys.stderr.stream


@status_activity("Running website adapter model and tools")
def run_browser_agent(*, factory, tools, prompt, model, env_file, profile,
                      max_rounds, timeout, model_started, tool_started, pump,
                       expected_destination=None, trace_output=None, user_task=None):
    """Production uses a killable worker; injected factories use a test thread."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise BrowserAgentError('browser_agent_configuration_failed')
    if factory is not None:
        return _run_injected(factory=factory, tools=tools, prompt=prompt, model=model,
            env_file=env_file, profile=profile, max_rounds=max_rounds, timeout=timeout,
            model_started=model_started, tool_started=tool_started, pump=pump,
            user_task=user_task)
    from langchain_core.tools import create_schema_from_function
    try:
        descriptors = [{
            'name': name, 'description': function.__doc__ or name.replace('_', ' '),
            'schema': create_schema_from_function(name, function).model_json_schema(),
        } for name, function in tools.items()]
    except Exception:
        raise BrowserAgentError('browser_agent_configuration_failed') from None
    config = {'kind': 'start', 'model': model, 'env_file': env_file, 'prompt': prompt,
              'max_input_tokens': profile.max_input_tokens, 'max_output_tokens': profile.max_output_tokens,
              'max_rounds': max_rounds, 'tools': descriptors,
              'expected_destination': expected_destination,
              'verbose': trace_output is not None, 'user_task': user_task}
    return _run_process(config, tools=tools, timeout=timeout, model_started=model_started,
                        tool_started=tool_started, pump=pump,trace_output=trace_output)


def _run_injected(*, factory, tools, prompt, model, env_file, profile,
                  max_rounds, timeout, model_started, tool_started, pump, user_task=None):
    calls, finished = queue.Queue(), queue.Queue()
    cancelled = threading.Event()
    deadline = time.monotonic() + timeout

    def broker(function, category='tool'):
        @wraps(function)
        def invoke(*args, **kwargs):
            if cancelled.is_set():
                raise BrowserAgentError('browser_agent_cancelled')
            response = queue.Queue()
            calls.put((category, function, args, kwargs, response))
            while not cancelled.is_set():
                try:
                    ok, value, fatal = response.get(timeout=0.05)
                except queue.Empty:
                    continue
                if ok:
                    return value
                if fatal:
                    raise _HostCallError(value)
                return json.dumps({'status': 'needs_revision', 'reason': value})
            raise BrowserAgentError('browser_agent_cancelled')
        return invoke

    bound = {name: broker(function) for name, function in tools.items()}

    def work():
        _silence_thread(True)
        try:
            broker(model_started, 'model')()
            agent = factory(model=model, env_file=env_file, tools=bound,
                system_prompt=prompt, max_round=max_rounds,
                max_input_tokens=profile.max_input_tokens, max_output_tokens=profile.max_output_tokens,
                stream=False, thinking=False, verbose=False, keep_logs=False)
            agent(user_task or 'Analyze the host-recorded manual calibration turn, then build and qualify the integration program.')
            finished.put(None)
        except BaseException as error:
            finished.put(_failure_reason(error))
        finally:
            _silence_thread(False)

    threading.Thread(target=work, daemon=True).start()
    try:
        while finished.empty():
            if time.monotonic() >= deadline:
                raise BrowserAgentError('browser_agent_time_exhausted')
            try:
                category, function, args, kwargs, response = calls.get(timeout=0.05)
            except queue.Empty:
                _pump_owner(pump)
                continue
            try:
                accounting_complete = category == 'model'
                if category == 'tool':
                    tool_started(function.__name__)
                    accounting_complete = True
                response.put((True, function(*args, **kwargs), False))
            except Exception as error:
                reason = _safe_tool_error(error)
                response.put((False, reason, category == 'model' or not accounting_complete or reason in _FATAL_HOST_REASONS))
            _pump_owner(pump)
        error = finished.get()
        if error:
            raise BrowserAgentError(error)
    finally:
        cancelled.set()


def _encode(value, limit):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
    except (ValueError, TypeError, UnicodeError):
        raise BrowserAgentError('browser_agent_rpc_invalid') from None
    if len(encoded) > limit:
        raise BrowserAgentError('browser_agent_ipc_limit')
    return encoded


def _read(stream, limit):
    raw = stream.readline(limit + 1)
    if not raw or not raw.endswith(b'\n'):
        raise BrowserAgentError('browser_agent_ipc_limit' if len(raw) > limit else 'browser_agent_rpc_invalid')
    if len(raw) > limit:
        raise BrowserAgentError('browser_agent_ipc_limit')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise BrowserAgentError('browser_agent_rpc_invalid') from None
    if type(value) is not dict:
        raise BrowserAgentError('browser_agent_rpc_invalid')
    return value


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for stream in (process.stdin, process.stdout,process.stderr):
        try:
            stream.close()
        except (OSError, AttributeError):
            pass


def _run_process(config, *, tools, timeout, model_started, tool_started, pump,trace_output=None):
    limit = resource_limit('PROVIDER_WORKER_BYTES')
    initial = _encode(config, limit)
    deadline = time.monotonic() + timeout
    notifications, outgoing = queue.Queue(maxsize=2), queue.Queue(maxsize=2)
    stopped = threading.Event()
    try:
        process = subprocess.Popen([sys.executable, '-I', '-m', 'lladar.browser_agent_loop'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'AKASHA_AUTO_CONFIGURE_LOGGING': 'false',
                 'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
                 'PYTHONIOENCODING': 'utf-8'},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except Exception:
        raise BrowserAgentError('browser_agent_configuration_failed') from None
    def notify(value):
        while not stopped.is_set():
            try:
                notifications.put(value, timeout=0.05)
                return
            except queue.Full:
                continue
    def reader():
        try:
            while not stopped.is_set():
                notify(('message', _read(process.stdout, limit)))
        except BrowserAgentError as error:
            notify(('error', error.reason))
        except Exception:
            notify(('error', 'browser_agent_rpc_invalid'))
    def writer():
        try:
            while not stopped.is_set():
                try:
                    data = outgoing.get(timeout=0.05)
                except queue.Empty:
                    continue
                process.stdin.write(data)
                process.stdin.flush()
        except Exception:
            notify(('error', 'browser_agent_rpc_invalid'))
    def trace_reader():
        try:
            while not stopped.is_set():
                raw=process.stderr.readline(limit+1)
                if not raw:
                    return
                if len(raw)>limit:
                    notify(('error','browser_agent_ipc_limit'))
                    return
                notify(('trace',raw.decode('utf-8',errors='replace').rstrip('\r\n')))
        except Exception:
            notify(('error','browser_agent_rpc_invalid'))
    readers = [threading.Thread(target=reader, daemon=True), threading.Thread(target=writer, daemon=True),
               threading.Thread(target=trace_reader, daemon=True)]
    for thread in readers:
        thread.start()
    outgoing.put_nowait(initial)
    next_id = 1
    try:
        while True:
            if time.monotonic() >= deadline:
                raise BrowserAgentError('browser_agent_time_exhausted')
            try:
                category, message = notifications.get(timeout=0.05)
            except queue.Empty:
                _pump_owner(pump)
                continue
            if category == 'error':
                raise BrowserAgentError(message)
            if category == 'trace':
                if trace_output is not None and message:
                    trace_output(message)
                continue
            if message.get('kind') == 'finished':
                if set(message) != {'kind', 'reason'}:
                    raise BrowserAgentError('browser_agent_rpc_invalid')
                if message['reason'] is not None:
                    raise BrowserAgentError(message['reason'])
                return
            if (set(message) != {'kind', 'id', 'category', 'name', 'args', 'kwargs'}
                    or message['kind'] != 'call' or type(message['id']) is not int
                    or message['id'] != next_id or type(message['args']) is not list
                    or type(message['kwargs']) is not dict):
                raise BrowserAgentError('browser_agent_rpc_invalid')
            next_id += 1
            try:
                accounting_complete = message['category'] == 'model'
                if message['category'] == 'model' and message['name'] == 'model_started':
                    if message['args'] or message['kwargs']:
                        raise BrowserAgentError('browser_agent_rpc_invalid')
                    value = model_started()
                elif message['category'] == 'tool' and message['name'] in tools:
                    tool_started(message['name'])
                    accounting_complete = True
                    value = tools[message['name']](*message['args'], **message['kwargs'])
                else:
                    raise BrowserAgentError('browser_agent_rpc_invalid')
                reply = {'id': message['id'], 'ok': True, 'value': value}
            except BrowserAgentError:
                raise
            except Exception as error:
                reason = _safe_tool_error(error)
                reply = {'id': message['id'], 'ok': False, 'reason': reason,
                         'fatal': message['category'] == 'model' or not accounting_complete or reason in _FATAL_HOST_REASONS}
            try:
                outgoing.put_nowait(_encode(reply, limit))
            except queue.Full:
                raise BrowserAgentError('browser_agent_rpc_invalid') from None
            _pump_owner(pump)
    finally:
        stopped.set()
        _stop_process(process)
        for thread in readers:
            thread.join(timeout=1)


class _HostCallError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('host_call_failed')


def _failure_reason(error):
    from .providers.akasha import ModelDestinationError
    if isinstance(error, ModelDestinationError):
        return ('browser_agent_destination_changed' if error.code == 'model_destination_changed'
                else 'browser_agent_destination_unconfirmed')
    if isinstance(error, BrowserAgentError):
        return error.reason
    if isinstance(error, _HostCallError):
        if error.reason in {'browser_agent_time_exhausted', 'discovery_time_exhausted', 'extraction_time_exhausted'}:
            return 'browser_agent_time_exhausted'
        if error.reason == 'browser_authentication_required':
            return error.reason
        if error.reason in {'discovery_budget_exhausted', 'browser_request_budget_exhausted', 'extraction_budget_exhausted'}:
            return 'browser_agent_budget_exhausted'
        if error.reason in {'provider_destination_changed', 'provider_destination_unconfirmed'}:
            return ('browser_agent_destination_changed' if error.reason == 'provider_destination_changed'
                    else 'browser_agent_destination_unconfirmed')
        if error.reason in {'checkpoint_failed', 'checkpoint_integrity_failed'}:
            return 'browser_agent_checkpoint_failed'
        if error.reason == 'browser_agent_no_progress':
            return error.reason
    return 'browser_agent_failed'


def _worker():
    """RPC is the sole stdout writer; the complete SDK process is disposable."""
    limit = resource_limit('PROVIDER_WORKER_BYTES')
    output, incoming = sys.stdout.buffer, sys.stdin.buffer
    logging.disable(logging.CRITICAL)
    rpc_lock = threading.Lock()
    next_id = 0
    def send(value):
        output.write(_encode(value, limit))
        output.flush()
    def rpc(category, name, *args, **kwargs):
        nonlocal next_id
        with rpc_lock:
            next_id += 1
            identity = next_id
            send({'kind': 'call', 'id': identity, 'category': category,
                  'name': name, 'args': list(args), 'kwargs': kwargs})
            response = _read(incoming, limit)
            if response.get('id') != identity or type(response.get('ok')) is not bool:
                raise BrowserAgentError('browser_agent_rpc_invalid')
            if response['ok']:
                return response.get('value')
            reason = response.get('reason', 'tool_failed')
            if response.get('fatal', True):
                raise _HostCallError(reason)
            return json.dumps({'status': 'needs_revision', 'reason': reason})
    try:
        config = _read(incoming, limit)
        if config.get('kind') != 'start':
            raise BrowserAgentError('browser_agent_rpc_invalid')
        trace_stream=sys.stderr
        with redirect_stdout(trace_stream), redirect_stderr(trace_stream):
                import akasha
                from langchain_core.callbacks import BaseCallbackHandler
                from langchain_core.tools import StructuredTool
                from .providers.akasha import copy_model_destination, disable_model_retries, verify_model_destination
                expected = copy_model_destination(config['expected_destination'])
                bound = []
                for descriptor in config['tools']:
                    name = descriptor['name']
                    def remote(*args, _name=name, **kwargs):
                        return rpc('tool', _name, *args, **kwargs)
                    bound.append(StructuredTool(name=name, description=descriptor['description'],
                                                args_schema=descriptor['schema'], func=remote))
                agent = akasha.agents(model=config['model'], env_file=config['env_file'],
                    tools=bound, system_prompt=config['prompt'], max_round=config['max_rounds'],
                    max_input_tokens=config['max_input_tokens'], max_output_tokens=config['max_output_tokens'],
                    stream=False, thinking=False, verbose=config.get('verbose') is True, keep_logs=False)
                client = agent.model_obj
                class ModelBudget(BaseCallbackHandler):
                    raise_error = True
                    def on_chat_model_start(self, *_args, **_kwargs):
                        verify_model_destination(client, model=config['model'], expected_destination=expected,
                                                 max_output_tokens=config['max_output_tokens'])
                        rpc('model', 'model_started')
                client.callbacks = [ModelBudget()]
                disable_model_retries(client)
                agent(config.get('user_task') or 'Analyze the host-recorded manual calibration turn, then build and qualify the integration program.')
        send({'kind': 'finished', 'reason': None})
        return 0
    except BaseException as error:
        reason = _failure_reason(error)
        try:
            send({'kind': 'finished', 'reason': reason})
        except BaseException:
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(_worker())
