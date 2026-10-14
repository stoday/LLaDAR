"""Execute Agent-generated protocol programs in a capability-free Monty VM.

Programs define ``adapt(payload)`` and return JSON material, context and proposed
actions. They never receive host objects, browser storage, environment variables,
filesystem mounts, network tools or an external Python import hook. The caller
must independently validate their interpretation before using a candidate.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
import json
import math
from pathlib import Path
import re

from .resource_limits import resource_limit


_DIAGNOSTIC_EXCEPTION_TYPES = frozenset({
    'AssertionError', 'AttributeError', 'Exception', 'FileNotFoundError', 'ImportError',
    'IndexError', 'KeyError', 'LookupError', 'MemoryError', 'ModuleNotFoundError', 'NameError',
    'NotImplementedError', 'OSError', 'OverflowError', 'PermissionError', 'RecursionError',
    'RuntimeError', 'SyntaxError', 'TimeoutError', 'TypeError', 'UnboundLocalError',
    'UnicodeDecodeError', 'UnicodeEncodeError', 'UnicodeError', 'ValueError', 'ZeroDivisionError',
})


def _safe_diagnostic(value):
    if type(value) is not dict:
        return {}
    result = {key: value[key] for key in ('line', 'column', 'end_line', 'end_column')
              if type(value.get(key)) is int and value[key] > 0}
    exception_type = value.get('exception_type')
    if type(exception_type) is str and exception_type in _DIAGNOSTIC_EXCEPTION_TYPES:
        result['exception_type'] = exception_type
    for key in ('module', 'symbol', 'key'):
        name = value.get(key)
        if type(name) is str and len(name) <= 120 and re.fullmatch(
                r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', name, flags=re.ASCII):
            result[key] = name
    return result


def _monty_diagnostic(error, source=''):
    """Never read Monty's rendered message, source, filename or function name."""
    result = {}
    try:
        inner = error.exception()
        exception_type = type(inner).__name__
        if exception_type in _DIAGNOSTIC_EXCEPTION_TYPES:
            result['exception_type'] = exception_type
        if exception_type == 'KeyError' and getattr(inner, 'args', ()):
            result['key'] = inner.args[0]
        frames = error.traceback()
        if frames:
            frame = frames[-1]
            for key in ('line', 'column', 'end_line', 'end_column'):
                value = getattr(frame, key)
                if type(value) is int and value > 0:
                    result[key] = value
        line, column = result.get('line'), result.get('column')
        if line and column and source and exception_type in {
                'ModuleNotFoundError', 'NameError'}:
            parsed = ast.parse(source)
            if exception_type == 'ModuleNotFoundError':
                for node in ast.walk(parsed):
                    if isinstance(node, ast.Import) and node.lineno == line:
                        result['module'] = node.names[0].name
                        break
                    if isinstance(node, ast.ImportFrom) and node.lineno == line:
                        result['module'] = node.module
                        break
            else:
                for node in ast.walk(parsed):
                    if (isinstance(node, ast.Name) and node.lineno == line
                            and node.col_offset + 1 == column):
                        result['symbol'] = node.id
                        break
    except Exception:
        pass
    return _safe_diagnostic(result)


class ProtocolRuntimeError(RuntimeError):
    """A safe, stable code without candidate text or provider/browser secrets."""

    def __init__(self, code: str, *, diagnostic: dict | None = None):
        self.code = code if type(code) is str and code in {
            'protocol_source_invalid', 'protocol_source_limit', 'protocol_payload_invalid', 'protocol_input_limit',
            'protocol_runtime_unavailable', 'protocol_compile_error', 'protocol_execution_error',
            'protocol_access_denied', 'protocol_memory_limit', 'protocol_timeout', 'protocol_runtime_failed',
            'protocol_output_invalid', 'protocol_output_limit',
        } else 'protocol_runtime_failed'
        self.diagnostic = _safe_diagnostic(diagnostic)
        super().__init__(self.code)


class ProtocolRuntime:
    """Pure JSON protocol execution with enforced resource budgets.

    Binary input should use a base64 string or a JSON list of byte integers.
    Monty's pure ``json`` and ``base64`` modules and Python byte operations are
    available. Unsupported imports fail; they never load a host Python package.
    Every call starts a fresh session. Results are rejected in full on overflow,
    never shortened, and print output never reaches the application's streams.
    """

    def __init__(
        self,
        *,
        max_source_bytes: int | None = None,
        max_input_bytes: int | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
        memory_bytes: int | None = None,
        max_recursion_depth: int | None = None,
        max_suspensions: int | None = None,
    ):
        self.max_source_bytes = self._positive(
            max_source_bytes, "PROTOCOL_SOURCE_BYTES"
        )
        self.max_input_bytes = self._positive(max_input_bytes, "PROTOCOL_INPUT_BYTES")
        self.max_output_bytes = self._positive(max_output_bytes, "PROTOCOL_OUTPUT_BYTES")
        self.memory_bytes = self._positive(memory_bytes, "PROTOCOL_MEMORY_BYTES")
        self.max_recursion_depth = self._positive(
            max_recursion_depth, "PROTOCOL_RECURSION_DEPTH"
        )
        self.max_suspensions = self._positive(max_suspensions, "PROTOCOL_SUSPENSIONS")
        timeout = (
            resource_limit("PROTOCOL_TIMEOUT_SECONDS")
            if timeout_seconds is None else timeout_seconds
        )
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        self.timeout_seconds = float(timeout)

    @staticmethod
    def _positive(value: int | None, name: str) -> int:
        value = resource_limit(name) if value is None else value
        if type(value) is not int or value <= 0:
            raise ValueError(name + " must be a positive integer")
        return value

    @staticmethod
    def _validate_json(value: object, ancestors: set[int] | None = None) -> None:
        """Accept data only, without invoking methods on host-side subclasses."""
        kind = type(value)
        if kind in (str, int, bool) or value is None:
            return
        if kind is float:
            if not math.isfinite(value):
                raise ValueError
            return
        if kind not in (dict, list):
            raise ValueError
        ancestors = set() if ancestors is None else ancestors
        identity = id(value)
        if identity in ancestors:
            raise ValueError
        ancestors.add(identity)
        try:
            if kind is dict:
                for key, item in value.items():
                    if type(key) is not str:
                        raise ValueError
                    ProtocolRuntime._validate_json(item, ancestors)
            else:
                for item in value:
                    ProtocolRuntime._validate_json(item, ancestors)
        finally:
            ancestors.remove(identity)

    def execute(self, source: str, payload: dict) -> dict:
        """Run ``adapt(payload)`` with no external authority; return full JSON."""
        if type(source) is not str or not source.strip():
            raise ProtocolRuntimeError("protocol_source_invalid")
        try:
            source_bytes = len(source.encode("utf-8"))
        except UnicodeError:
            raise ProtocolRuntimeError("protocol_source_invalid") from None
        if source_bytes > self.max_source_bytes:
            raise ProtocolRuntimeError("protocol_source_limit")
        if type(payload) is not dict:
            raise ProtocolRuntimeError("protocol_payload_invalid")
        try:
            self._validate_json(payload)
            encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False,
                                 separators=(",", ":"))
            input_bytes = len(encoded.encode("utf-8"))
            # A private copy prevents candidates from mutating caller state.
            clean_payload = json.loads(encoded)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ProtocolRuntimeError("protocol_payload_invalid") from None
        if input_bytes > self.max_input_bytes:
            raise ProtocolRuntimeError("protocol_input_limit")

        try:
            from pydantic_monty import Monty, MontyCrashedError, MontyRuntimeError, MontySyntaxError
            runtime = distribution("pydantic-monty-runtime")
            binary = next(
                Path(runtime.locate_file(file)).resolve()
                for file in runtime.files or ()
                if file.name in ("monty", "monty.exe")
            )
            if not binary.is_file():
                raise FileNotFoundError
        except (ImportError, PackageNotFoundError, StopIteration, FileNotFoundError):
            raise ProtocolRuntimeError("protocol_runtime_unavailable") from None

        code = source + (
            "\nimport json as __lladar_json\n"
            "__lladar_json.dumps(adapt(__lladar_payload), ensure_ascii=False, "
            "allow_nan=False, separators=(',', ':'))\n"
        )
        printed_bytes = 0
        print_overflow = False

        def discard_print(_stream: str, text: str) -> None:
            nonlocal printed_bytes, print_overflow
            printed_bytes += len(text.encode("utf-8"))
            if printed_bytes > self.max_output_bytes:
                print_overflow = True
                raise MemoryError("protocol_print_limit")

        limits = {
            "max_feed_duration_secs": self.timeout_seconds,
            "max_turn_duration_secs": self.timeout_seconds,
            "max_memory": self.memory_bytes,
            "max_recursion_depth": self.max_recursion_depth,
            "max_suspensions": self.max_suspensions,
            "max_total_sleep_secs": 0,
        }
        try:
            # Use the installed runtime binary, never MONTY_BIN or PATH. The
            # pure Monty interpreter supplies the sandbox; subprocesses also
            # contain native runtime crashes and enforce the hard deadline.
            with Monty(
                binary_path=binary,
                min_processes=1,
                max_processes=1,
                request_timeout=self.timeout_seconds + 1,
                checkout_timeout=self.timeout_seconds + 1,
                feed_duration_limit_grace=0.5,
                turn_duration_limit_grace=0.5,
            ) as pool:
                with pool.checkout(
                    script_name="protocol.py", limits=limits,
                    os_policy={
                        "datetime": datetime(1970, 1, 1, tzinfo=timezone.utc),
                        "timezone": "utc",
                        "random_start": {"seed": 0},
                        "sleep": "call_host",
                        "process_time": "zero",
                    },
                ) as session:
                    result = session.feed_run(
                        code, inputs={"__lladar_payload": clean_payload},
                        external_lookup={}, mount=None, os=None,
                        print_callback=discard_print,
                    )
        except MontySyntaxError as error:
            raise ProtocolRuntimeError("protocol_compile_error", diagnostic=_monty_diagnostic(error, source)) from None
        except MontyRuntimeError as error:
            inner = error.exception()
            if print_overflow:
                failure = "protocol_output_limit"
            elif isinstance(inner, TimeoutError):
                failure = "protocol_timeout"
            elif isinstance(inner, MemoryError):
                failure = "protocol_memory_limit"
            elif isinstance(inner, (PermissionError, ImportError)):
                failure = "protocol_access_denied"
            else:
                failure = "protocol_execution_error"
            raise ProtocolRuntimeError(failure, diagnostic=_monty_diagnostic(error, source)) from None
        except MontyCrashedError as error:
            failure = "protocol_timeout" if error.timed_out else "protocol_runtime_failed"
            raise ProtocolRuntimeError(failure) from None
        except (RuntimeError, OSError):
            raise ProtocolRuntimeError("protocol_runtime_failed") from None

        if type(result) is not str:
            raise ProtocolRuntimeError("protocol_output_invalid")
        if len(result.encode("utf-8")) > self.max_output_bytes:
            raise ProtocolRuntimeError("protocol_output_limit")
        try:
            output = json.loads(result)
        except (ValueError, RecursionError):
            raise ProtocolRuntimeError("protocol_output_invalid") from None
        if type(output) is not dict:
            raise ProtocolRuntimeError("protocol_output_invalid")
        return output
