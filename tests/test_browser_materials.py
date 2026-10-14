import base64
import json

import pytest

from lladar.answer_extraction import ExtractionError
from lladar.browser_materials import deidentify_decoded


def test_session_credentials_redacted_with_safe_host_metadata_refs():
    source={"event_id":"reply-1","kind":"http_body","text":'{"metadata":{"session_id":"private-session","csrf":"private-csrf"},"content":"Number 37, not 1."}'}
    value={"data":source["text"],"event_ids":["reply-1"]}
    result=deidentify_decoded(value,(),[source])
    decoded=json.loads(result.value["data"])
    assert decoded["content"]=="Number 37, not 1."
    assert "private-session" not in json.dumps(result.value)
    assert "private-csrf" not in json.dumps(result.value)
    assert set(result.learned_sensitive_values)=={"private-session","private-csrf"}
    assert result.field_refs[0]["value_ref"]=={"event_id":"reply-1","path":["metadata","session_id"]}
    assert "private-session" not in json.dumps(result.field_refs)


def test_known_private_value_in_ordinary_answer_is_refused_not_rewritten():
    with pytest.raises(ExtractionError,match="sensitive_response"):
        deidentify_decoded({"session_id":"private-session","content":"Your private-session is printed here."},())
    with pytest.raises(ExtractionError,match="sensitive_response"):
        deidentify_decoded({"content":"Bearer real-private-credential"},())


@pytest.mark.parametrize('value', [
    {'synthetic-private-key': {'session_id': 'another-private-value', 'content': 'Full answer.'}},
    {'data': json.dumps({'outer': {'synthetic-private-key': {'content': 'Full answer.'}}})},
    {'synthetic\\u002dprivate\\u002dkey': 'Full answer.'},
], ids=['object-key-and-field-ref-path', 'nested-json-key', 'escaped-key'])
def test_private_values_in_object_keys_are_refused_before_material_or_ref_paths_can_leave_host(value):
    with pytest.raises(ExtractionError, match='sensitive_response'):
        deidentify_decoded(value, ('synthetic-private-key',))


@pytest.mark.parametrize('key', ['Bearer synthetic-private-credential', 'eyJsynthetic.eyJsynthetic.signature'])
def test_private_credential_patterns_in_keys_are_refused_without_a_known_secret_list(key):
    with pytest.raises(ExtractionError, match='sensitive_response'):
        deidentify_decoded({'data': json.dumps({key: 'Full answer.'})}, ())


@pytest.mark.parametrize('protected', [(), ('synthetic-private-path',)], ids=['learned-private-value', 'known-private-value'])
def test_private_native_json_key_cannot_escape_through_an_otherwise_safe_value_ref_path(protected):
    source = {'event_id': 'reply-1', 'text': json.dumps({
        'synthetic-private-path': {'session_id': 'synthetic-private-path'},
        'content': 'Full public answer.',
    })}
    with pytest.raises(ExtractionError, match='sensitive_response'):
        deidentify_decoded({'session_id': 'synthetic-private-path', 'content': 'Full public answer.'}, protected, [source])


def test_short_cookie_flags_and_unchanged_json_prose_keep_numbers_and_formatting():
    text='{ "content" : "37, not 1. Keep original paragraphs.\\n\\nTail." }'
    result=deidentify_decoded({"data":text},("1",))
    assert result.value=={"data":text}
    assert result.field_refs==[]


@pytest.mark.parametrize("form",["base64","url-base64","base64-unpadded","url-base64-unpadded","hex","bytes","latin1"])
def test_full_unknown_binary_passthrough_is_not_deidentified(form):
    wire=b'\x01\xffopaque private binary'
    value={"base64":base64.b64encode(wire).decode(),"url-base64":base64.urlsafe_b64encode(wire).decode(),
           "base64-unpadded":base64.b64encode(wire).decode().rstrip('='),
           "url-base64-unpadded":base64.urlsafe_b64encode(wire).decode().rstrip('='),
           "hex":wire.hex(),"bytes":list(wire),"latin1":wire.decode("latin-1")}[form]
    event={"event_id":"frame-1","binary_b64":base64.b64encode(wire).decode()}
    with pytest.raises(ExtractionError,match="sensitive_response"):
        deidentify_decoded({"data":value},(),[event])


def test_complete_decoded_json_fields_are_checked_without_selecting_answer():
    raw=b'{"content":"First\\n\\nTail","session_id":"private-id","progress":"finished"}'
    event={"event_id":"frame","binary_b64":base64.b64encode(raw).decode()}
    result=deidentify_decoded({"data":raw.decode()},(),[event])
    data=json.loads(result.value["data"])
    assert set(data)=={"content","session_id","progress"}
    assert data["content"]=="First\n\nTail"
    assert result.field_refs[0]["source_event_ids"]==["frame"]
    assert "value_ref" not in result.field_refs[0]  # binary-derived binding needs a host extension


def test_labelled_metadata_containers_keep_structure_and_nonprivate_fields():
    value={"session":{"id":"private-id","name":"Dialogue","count":37},"content":"Do not change 37 or negate 1."}
    result=deidentify_decoded(value,())
    assert set(result.value["session"])=={"id","name","count"}
    assert result.value["session"]["count"]==37
    assert result.value["session"]["name"]=="Dialogue"
    assert result.value["content"]==value["content"]
    assert "private-id" not in json.dumps(result.value)


def test_redacted_credential_refs_pass_transfer_screen_but_private_suffix_cannot():
    from lladar.answer_extraction import contains_sensitive_text
    value={'metadata':{'access_token':'real-private-credential'},'content':'37, not 73.\n\nKeep the tail.'}
    clean=deidentify_decoded(value,())
    assert not contains_sensitive_text(json.dumps(clean.value),clean.learned_sensitive_values)
    assert contains_sensitive_text('{"access_token":"[host-private:private-metadata-1] actual-secret"}',())
    assert contains_sensitive_text('{"content":"real-private-credential"}',clean.learned_sensitive_values)
