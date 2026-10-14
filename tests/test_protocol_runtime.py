"""Public generated-protocol execution, independent of any website codec."""
import pytest

from lladar.protocol_runtime import ProtocolRuntime, ProtocolRuntimeError


def test_runtime_error_never_exposes_an_unknown_private_error_code():
    error = ProtocolRuntimeError("synthetic-private-payload-secret")
    assert error.code == "protocol_runtime_failed"
    assert str(error) == "protocol_runtime_failed"


def test_runtime_diagnostic_points_to_the_failing_line_without_echoing_secret_names_or_values(capsys):
    source = (
        "def synthetic_private_function_name(payload):\n"
        "    visible = payload['private']\n"
        "    raise ValueError(visible)\n"
        "def adapt(payload):\n"
        "    return synthetic_private_function_name(payload)\n"
    )
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(source, {"private": "synthetic-private-payload-value"})
    assert caught.value.code == "protocol_execution_error"
    diagnostic = caught.value.diagnostic
    assert diagnostic["exception_type"] == "ValueError"
    assert diagnostic["line"] == 3
    assert type(diagnostic["column"]) is int and diagnostic["column"] > 0
    assert set(diagnostic) <= {"exception_type", "line", "column", "end_line", "end_column"}
    assert "synthetic" not in repr(diagnostic)
    assert str(caught.value) == "protocol_execution_error"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_runtime_diagnostic_reports_unknown_encoding_without_echoing_input(capsys):
    source = (
        "def adapt(payload):\n"
        "    return {'value': payload['private'].encode('latin1')}\n"
    )
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(source, {"private": "synthetic-private-payload-value"})
    assert caught.value.code == "protocol_execution_error"
    assert caught.value.diagnostic["exception_type"] == "LookupError"
    assert caught.value.diagnostic["line"] == 2
    assert "synthetic" not in repr(caught.value.diagnostic)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_runtime_diagnostic_reports_safe_missing_key_for_adapter_repair():
    from lladar.protocol_runtime import ProtocolRuntime, ProtocolRuntimeError
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(
            "def adapt(payload):\n    return {'answer': payload['events'][0]['missing_field']}\n",
            {'events': [{'kind': 'http_body'}]})
    assert caught.value.diagnostic['exception_type'] == 'KeyError'
    assert caught.value.diagnostic['key'] == 'missing_field'


def test_restricted_runtime_names_unsupported_import_and_missing_builtin():
    with pytest.raises(ProtocolRuntimeError) as import_failure:
        ProtocolRuntime().execute("import urllib.parse\ndef adapt(payload):\n    return {}\n", {})
    assert import_failure.value.code == "protocol_access_denied"
    assert import_failure.value.diagnostic["module"] == "urllib.parse"

    with pytest.raises(ProtocolRuntimeError) as missing_builtin:
        ProtocolRuntime().execute("def adapt(payload):\n    return {'value': bytearray()}\n", {})
    assert missing_builtin.value.code == "protocol_execution_error"
    assert missing_builtin.value.diagnostic["symbol"] == "bytearray"


def test_syntax_diagnostic_preserves_only_safe_numeric_location_and_exception_type(capsys):
    source = "def synthetic_private_function_name(payload)\n    return {'private': 'synthetic-private-source-value'}\n"
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(source, {})
    assert caught.value.code == "protocol_compile_error"
    diagnostic = caught.value.diagnostic
    assert diagnostic["exception_type"] == "SyntaxError"
    assert diagnostic["line"] == 1
    assert type(diagnostic["column"]) is int and diagnostic["column"] > 0
    assert set(diagnostic) <= {"exception_type", "line", "column", "end_line", "end_column"}
    assert "synthetic" not in repr(diagnostic)
    assert str(caught.value) == "protocol_compile_error"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_generated_program_decodes_binary_material_and_retains_complete_text():
    source = '''
import json
import base64

def adapt(payload):
    frame = json.loads(base64.b64decode(payload["frame"]).decode("utf-8"))
    return {
        "phase": "response",
        "materials": [{"message_id": frame["id"], "text": frame["text"]}],
        "context": {"session_id": frame["session"]},
        "actions": [],
    }
'''
    # An independent, recorded UTF-8/base64 frame; no preinstalled protocol codec.
    # Base64 ASCII represents {"id":"m2","session":"s1","text":"complete"}.
    payload = {"frame": "eyJpZCI6Im0yIiwic2Vzc2lvbiI6InMxIiwidGV4dCI6ImNvbXBsZXRlIn0="}
    assert ProtocolRuntime().execute(source, payload) == {
        "phase": "response",
        "materials": [{"message_id": "m2", "text": "complete"}],
        "context": {"session_id": "s1"},
        "actions": [],
    }


def test_non_json_keys_are_rejected_before_silent_key_conversion():
    with pytest.raises(ProtocolRuntimeError, match="^protocol_payload_invalid$"):
        ProtocolRuntime().execute("def adapt(payload):\n    return payload", {1: "one", "1": "string"})


@pytest.mark.parametrize("source", [
    "import socket\ndef adapt(payload):\n    return {'x': socket.create_connection(('127.0.0.1', 1))}",
    "import subprocess\ndef adapt(payload):\n    return {'x': subprocess.check_output(['whoami'])}",
    "import akasha\ndef adapt(payload):\n    return {'x': akasha}",
    "def adapt(payload):\n    return {'x': open(payload['path']).read()}",
    "import os\ndef adapt(payload):\n    return {'x': os.environ['LLADAR_PROTOCOL_TEST_SECRET']}",
    "def adapt(payload):\n    return {'x': ().__class__.__bases__[0].__subclasses__()}",
])
def test_host_files_credentials_network_processes_and_imports_are_inaccessible(
    source, tmp_path, monkeypatch
):
    secret = "must-never-be-returned-credential"
    host_file = tmp_path / "credential.txt"
    host_file.write_text(secret, encoding="utf-8")
    monkeypatch.setenv("LLADAR_PROTOCOL_TEST_SECRET", secret)
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(source, {"path": str(host_file)})
    assert caught.value.code in {"protocol_access_denied", "protocol_execution_error"}
    assert str(caught.value) == caught.value.code
    assert secret not in str(caught.value)
    assert host_file.read_text(encoding="utf-8") == secret


def test_loop_is_interrupted_and_next_call_has_fresh_usable_session():
    runtime = ProtocolRuntime(timeout_seconds=0.05)
    with pytest.raises(ProtocolRuntimeError, match="^protocol_timeout$"):
        runtime.execute("def adapt(payload):\n    while True:\n        pass", {})
    assert runtime.execute("def adapt(payload):\n    return {'working': True}", {}) == {"working": True}


def test_memory_limit_rejects_large_allocation_without_killing_host():
    runtime = ProtocolRuntime(memory_bytes=1024 * 1024)
    with pytest.raises(ProtocolRuntimeError, match="^protocol_memory_limit$"):
        runtime.execute("def adapt(payload):\n    return {'data': 'x' * 10**12}", {})
    assert runtime.execute("def adapt(payload):\n    return {'working': True}", {}) == {"working": True}


def test_full_large_unicode_material_is_preserved_and_overflow_fails_in_full():
    source = "def adapt(payload):\n    return {'material': payload['text']}"
    text = "完整原文\n" * 20000
    assert ProtocolRuntime().execute(source, {"text": text}) == {"material": text}
    with pytest.raises(ProtocolRuntimeError, match="^protocol_output_limit$"):
        ProtocolRuntime(max_output_bytes=100).execute(source, {"text": text})


def test_binary_byte_list_supports_generated_length_prefix_decoder():
    source = '''
def adapt(payload):
    data = bytes.fromhex("".join(format(byte, "02x") for byte in payload["frame"]))
    length = data[0] | (data[1] << 8)
    assert len(data) == length + 2
    return {"materials": [{"text": data[2:].decode("utf-8")}], "actions": []}
'''
    assert ProtocolRuntime().execute(source, {"frame": [5, 0, 104, 101, 108, 108, 111]}) == {
        "materials": [{"text": "hello"}], "actions": [],
    }


def test_printed_values_and_candidate_exception_text_never_reach_host_streams(capsys):
    source = '''
def adapt(payload):
    print(payload["private"])
    raise ValueError(payload["private"])
'''
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime().execute(source, {"private": "fixture-private-material"})
    assert caught.value.code == "protocol_execution_error"
    assert str(caught.value) == "protocol_execution_error"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_print_flood_is_bounded_and_not_written_to_stdout(capsys):
    source = "def adapt(payload):\n    print('x' * 2000)\n    return {}"
    with pytest.raises(ProtocolRuntimeError, match="^protocol_output_limit$"):
        ProtocolRuntime(max_output_bytes=100).execute(source, {})
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_runtime_does_not_resolve_executable_from_environment(monkeypatch):
    monkeypatch.setenv("MONTY_BIN", "untrusted-candidate-executable")
    assert ProtocolRuntime().execute("def adapt(payload):\n    return {'ok': True}", {}) == {"ok": True}


def test_payload_mutation_and_global_state_cannot_escape_one_execution():
    payload = {"events": ["first"]}
    source = '''
counter = 0
def adapt(payload):
    global counter
    counter += 1
    payload["events"].append("generated")
    return {"counter": counter, "events": payload["events"]}
'''
    runtime = ProtocolRuntime()
    expected = {"counter": 1, "events": ["first", "generated"]}
    assert runtime.execute(source, payload) == expected
    assert runtime.execute(source, payload) == expected
    assert payload == {"events": ["first"]}


def test_clock_and_random_are_repeatable_and_sleep_cannot_consume_host_time():
    source = '''
import random
import time
def adapt(payload):
    return {"clock": time.time(), "random": random.random()}
'''
    runtime = ProtocolRuntime()
    assert runtime.execute(source, {}) == runtime.execute(source, {})
    with pytest.raises(ProtocolRuntimeError, match="^protocol_execution_error$"):
        runtime.execute("import time\ndef adapt(payload):\n    time.sleep(10**9)\n    return {}", {})


def test_custom_host_objects_are_rejected_without_invoking_their_methods():
    invoked = []
    class DangerousList(list):
        def __iter__(self):
            invoked.append(True)
            return super().__iter__()
    with pytest.raises(ProtocolRuntimeError, match="^protocol_payload_invalid$"):
        ProtocolRuntime().execute("def adapt(payload):\n    return payload", {"items": DangerousList([1])})
    assert invoked == []


@pytest.mark.parametrize("payload", [{"x": float("nan")}, {"x": b"not-json"}, {"x": lambda: None}])
def test_payload_accepts_finite_plain_json_only(payload):
    with pytest.raises(ProtocolRuntimeError, match="^protocol_payload_invalid$"):
        ProtocolRuntime().execute("def adapt(payload):\n    return payload", payload)


@pytest.mark.parametrize("options", [
    {"max_input_bytes": 0}, {"max_output_bytes": True},
    {"timeout_seconds": float("inf")}, {"memory_bytes": -1},
])
def test_disabled_or_invalid_enforcement_budgets_are_rejected(options):
    with pytest.raises(ValueError):
        ProtocolRuntime(**options)


@pytest.mark.parametrize("source,payload,options,code", [
    ("", {}, {}, "protocol_source_invalid"),
    ("def adapt(payload):\n    return {}", {}, {"max_source_bytes": 10}, "protocol_source_limit"),
    ("def adapt(payload):\n    return {}", {"x": "1234567890"}, {"max_input_bytes": 10}, "protocol_input_limit"),
    ("def adapt(payload):\n    return []", {}, {}, "protocol_output_invalid"),
    ("def adapt(:\n    return {}", {}, {}, "protocol_compile_error"),
])
def test_incomplete_or_invalid_candidate_rejected_with_safe_error(source, payload, options, code):
    with pytest.raises(ProtocolRuntimeError) as caught:
        ProtocolRuntime(**options).execute(source, payload)
    assert caught.value.code == code
    assert str(caught.value) == code
