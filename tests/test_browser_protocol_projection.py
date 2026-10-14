"""Known private bytes never enter the generated protocol VM unmodified."""
import hashlib
import base64
import json
import re
from urllib.parse import quote, quote_plus, unquote_to_bytes

import pytest

from lladar.browser_protocol_projection import BinaryProtocolProjector, ProtocolProjectionError
from lladar.answer_extraction import ExtractionError
from lladar.browser_materials import deidentify_decoded
from lladar.protocol_runtime import ProtocolRuntime


def test_private_utf8_is_masked_before_decode_without_changing_length_delimited_offsets():
    private = "私有-session-value"
    encoded = private.encode()
    wire = b"\x0a" + bytes([len(encoded)]) + encoded + b"\x12\x04tail"
    projector = BinaryProtocolProjector()
    result = projector.project(wire, [private])
    assert result.wire_safe[:2] == wire[:2]
    assert result.wire_safe[-6:] == b"\x12\x04tail"
    assert len(result.wire_safe) == len(wire)
    assert private.encode() not in result.wire_safe
    assert wire == b"\x0a" + bytes([len(encoded)]) + encoded + b"\x12\x04tail"
    assert result.original_sha256 == hashlib.sha256(wire).hexdigest()
    assert result.projection_sha256 == hashlib.sha256(result.wire_safe).hexdigest()
    assert result.original_sha256 != result.projection_sha256
    assert all(32 <= byte <= 126 for byte in result.wire_safe[2:-6])
    assert result.refs_safe[0]["offset"] == 2
    assert result.refs_safe[0]["length"] == len(encoded)
    assert private not in repr(result)


_SECRET = "private-session/+ 中文?"
_RAW = _SECRET.encode()
_JSON_ESCAPED = json.dumps(_SECRET, ensure_ascii=True)[1:-1].encode()
_COMMON_FORMS = [
    _RAW,
    base64.b64encode(_RAW),
    base64.b64encode(_RAW).rstrip(b"="),
    base64.urlsafe_b64encode(_RAW),
    base64.urlsafe_b64encode(_RAW).rstrip(b"="),
    _RAW.hex().encode(),
    _RAW.hex().upper().encode(),
    quote(_SECRET, safe="").encode(),
    quote_plus(_SECRET, safe="").encode(),
    b"".join(f"%{byte:02X}".encode() for byte in _RAW),
    b"".join(f"%{byte:02x}".encode() for byte in _RAW),
    _JSON_ESCAPED,
    re.sub(rb"\\u[0-9a-f]{4}", lambda match: b"\\u" + match[0][2:].upper(), _JSON_ESCAPED),
    _JSON_ESCAPED.replace(b"/", b"\\/"),
    json.dumps(_JSON_ESCAPED.decode())[1:-1].encode(),
]


@pytest.mark.parametrize("encoded", _COMMON_FORMS)
def test_common_known_encodings_are_projected_without_leaking_or_changing_wire_length(encoded):
    projector = BinaryProtocolProjector()
    wire = b"before\x00" + encoded + b"\x00after"
    result = projector.project(wire, [_SECRET])
    assert result.wire_safe[:7] == b"before\x00"
    assert result.wire_safe[-6:] == b"\x00after"
    assert len(result.wire_safe) == len(wire)
    assert encoded not in result.wire_safe
    assert result.refs_safe
    assert _SECRET not in repr(result.refs_safe)
    assert encoded.decode("utf-8") not in json.dumps(result.refs_safe)
    assert all(set(ref) == {"offset", "length", "opaque_ref"} for ref in result.refs_safe)
    assert result.masked_literals and projector.masked_literals
    assert result.version == "known-private-byte-projection-v1"
    assert all(_SECRET not in value for value in projector.masked_literals)


def test_cross_frame_literal_base64_hex_and_url_decoders_keep_the_same_random_correlation():
    private = "one-private-value/+ 中文"
    raw = private.encode()
    projector = BinaryProtocolProjector()
    direct = projector.project(raw, [private])
    encoded = projector.project(base64.b64encode(raw), [private])
    hexadecimal = projector.project(raw.hex().encode(), [private])
    url = projector.project(quote(private, safe="").encode(), [private])
    assert base64.b64decode(encoded.wire_safe) == direct.wire_safe
    assert bytes.fromhex(hexadecimal.wire_safe.decode()) == direct.wire_safe
    assert unquote_to_bytes(url.wire_safe) == direct.wire_safe
    assert {result.refs_safe[0]["opaque_ref"] for result in (direct, encoded, hexadecimal, url)} == {
        direct.refs_safe[0]["opaque_ref"]}
    assert projector.project(b"second:" + raw, [private]).wire_safe == b"second:" + direct.wire_safe


@pytest.mark.parametrize("values", [["abcdef", "cdefgh"], ["abcdefghi", "cde"], ["aba", "bab"]])
def test_overlapping_and_nested_occurrences_are_covered_in_full(values, monkeypatch):
    # Control the external randomness boundary so every covered byte has a
    # known, independent expected mask rather than relying on random inequality.
    monkeypatch.setattr("secrets.token_bytes", lambda length: b"+" * length)
    original = b"abababa" if values == ["aba", "bab"] else b"abcdefghi"
    projector = BinaryProtocolProjector()
    result = projector.project(b"<" + original + b">", values)
    assert result.wire_safe[:1] == b"<" and result.wire_safe[-1:] == b">"
    assert len(result.wire_safe) == len(original) + 2
    assert all(value.encode() not in result.wire_safe for value in values)
    expected = b"<rrrrrrrri>" if values == ["abcdef", "cdefgh"] else b"<" + b"r"*len(original) + b">"
    assert result.wire_safe == expected
    assert projector.project(b"<" + original + b">", list(reversed(values))).wire_safe == result.wire_safe
    assert result.refs_safe and all(ref["offset"] >= 1 for ref in result.refs_safe)


def test_no_known_private_occurrence_leaves_arbitrary_binary_bytes_exactly_unchanged():
    wire = bytes(range(256)) + b"normal response"
    result = BinaryProtocolProjector().project(wire, ["not-in-this-frame"])
    assert result.wire_safe is wire or result.wire_safe == wire
    assert result.refs_safe == result.masked_literals == ()
    assert result.original_sha256 == result.projection_sha256


def test_empty_binary_and_empty_private_configuration_are_valid_unchanged_projections():
    projector = BinaryProtocolProjector()
    assert projector.project(b"", [""]).wire_safe == b""
    assert projector.project(b"frame", []).wire_safe == b"frame"
    assert projector.masked_literals == ()


def test_new_known_values_do_not_perturb_existing_masks_and_snapshots_are_host_only():
    projector = BinaryProtocolProjector()
    first = projector.project(b"first-private-literal", ["first-private-literal"])
    old_snapshot = projector.masked_literals
    second = projector.project(b"second-private-literal", ["second-private-literal"])
    assert set(old_snapshot) <= set(projector.masked_literals)
    assert projector.project(b"first-private-literal", ["second-private-literal", "first-private-literal"]).wire_safe == first.wire_safe
    assert first.wire_safe != second.wire_safe
    assert "first-private-literal" not in repr(old_snapshot)
    assert "second-private-literal" not in repr(projector.masked_literals)
    assert old_snapshot != projector.masked_literals


def test_masks_are_host_random_not_original_encodings_hashes_or_reusable_between_hosts():
    private = "a-specific-private-value"
    original = private.encode()
    first = BinaryProtocolProjector().project(original, [private])
    second = BinaryProtocolProjector().project(original, [private])
    assert first.wire_safe != second.wire_safe
    assert first.wire_safe not in {original, original.hex().encode(), base64.b64encode(original),
        hashlib.sha256(original).hexdigest().encode()[:len(original)]}
    ref = first.refs_safe[0]["opaque_ref"]
    assert private not in ref and hashlib.sha256(original).hexdigest() not in ref


def test_masked_answer_text_is_refused_but_labelled_metadata_becomes_an_opaque_host_ref():
    projector = BinaryProtocolProjector()
    projection = projector.project(b"sensitive-session-value", ["sensitive-session-value"])
    mask = projection.wire_safe.decode()
    with pytest.raises(ExtractionError, match="sensitive_response"):
        deidentify_decoded({"content": "The answer contains " + mask}, projector.masked_literals)
    material = deidentify_decoded({"session_id": mask, "content": "Unchanged answer."}, projector.masked_literals)
    assert material.value["session_id"].startswith("[host-private:")
    assert material.value["content"] == "Unchanged answer."
    assert mask not in json.dumps(material.value)
    assert "sensitive-session-value" not in repr(material.field_refs)


def test_generated_monty_program_cannot_recover_known_original_via_base64_hex_or_charcodes():
    private = "private-value-for-untrusted-VM"
    wire = b"\x0a" + bytes([len(private)]) + private.encode() + b"\x12\x04tail"
    projector = BinaryProtocolProjector()
    projection = projector.project(wire, [private])
    source = '''
import base64
def adapt(payload):
    wire = base64.b64decode(payload['wire'])
    length = wire[1]
    value = wire[2:2+length]
    return {'literal':value.decode(), 'base64':base64.b64encode(value).decode(),
        'hex':value.hex(), 'charcodes':[byte for byte in value], 'tail':wire[2+length:].hex()}
'''
    result = ProtocolRuntime().execute(source, {"wire": base64.b64encode(projection.wire_safe).decode()})
    assert result["literal"] != private
    assert base64.b64decode(result["base64"]) != private.encode()
    assert bytes.fromhex(result["hex"]) != private.encode()
    assert bytes(result["charcodes"]) != private.encode()
    assert result["tail"] == "12047461696c"
    assert private not in json.dumps(result)
    for field in ("literal", "base64", "hex"):
        with pytest.raises(ExtractionError, match="sensitive_response"):
            deidentify_decoded({"content": result[field]}, projector.masked_literals)


@pytest.mark.parametrize("wire,values", [(bytearray(b"private"), ["private"]),
    (b"wire", "private"), (b"wire", [object()]), (b"wire", ["\ud800"])])
def test_invalid_host_inputs_fail_with_a_closed_reason(wire, values):
    with pytest.raises(ProtocolProjectionError, match="^protocol_projection_input_invalid$"):
        BinaryProtocolProjector().project(wire, values)


@pytest.mark.parametrize("settings,wire,values", [
    ({"max_wire_bytes": 3}, b"long-wire", []),
    ({"max_pattern_bytes": 3}, b"private", ["private"]),
    ({"max_matches": 1}, b"private/private", ["private"]),
])
def test_projection_limits_reject_the_whole_frame_without_returning_a_prefix(settings, wire, values):
    with pytest.raises(ProtocolProjectionError, match="^protocol_projection_limit$"):
        BinaryProtocolProjector(**settings).project(wire, values)


def test_arbitrary_error_code_cannot_echo_a_private_value():
    error = ProtocolProjectionError("private-value-in-error")
    assert error.reason == error.code == str(error) == "protocol_projection_unavailable"


def test_a_new_known_value_colliding_with_an_old_mask_fails_closed_without_changing_correlation():
    private = "original-private-value"
    projector = BinaryProtocolProjector()
    first = projector.project(private.encode(), [private])
    with pytest.raises(ProtocolProjectionError, match="^protocol_projection_unavailable$"):
        projector.project(private.encode(), [private, first.wire_safe.decode()])
    assert projector.project(private.encode(), [private]).wire_safe == first.wire_safe


def test_original_frame_digests_remain_distinct_when_a_known_private_value_changes():
    projector = BinaryProtocolProjector()
    first = projector.project(b"secret-value-one", ["secret-value-one", "secret-value-two"])
    second = projector.project(b"secret-value-two", ["secret-value-one", "secret-value-two"])
    assert first.original_sha256 != second.original_sha256
    assert first.projection_sha256 != second.projection_sha256
    assert first.refs_safe[0]["opaque_ref"] != second.refs_safe[0]["opaque_ref"]


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_json_string_quotes_controls_and_unicode_are_masked_without_breaking_the_envelope(ensure_ascii):
    private = 'credential"\\/\n\t 中文'
    encoded = json.dumps(private, ensure_ascii=ensure_ascii)[1:-1].encode()
    wire = b'{"session_id":"' + encoded + b'","content":"tail stays complete"}'
    projection = BinaryProtocolProjector().project(wire, [private])
    decoded = json.loads(projection.wire_safe)
    assert decoded["content"] == "tail stays complete"
    assert decoded["session_id"] != private
    assert len(projection.wire_safe) == len(wire)


def test_each_projection_exposes_decoded_mask_screening_values_without_needing_the_owner_snapshot():
    private = "original-private-base64-value"
    projection = BinaryProtocolProjector().project(base64.b64encode(private.encode()), [private])
    decoded = base64.b64decode(projection.wire_safe).decode()
    with pytest.raises(ExtractionError, match="sensitive_response"):
        deidentify_decoded({"content": decoded}, projection.masked_literals)
