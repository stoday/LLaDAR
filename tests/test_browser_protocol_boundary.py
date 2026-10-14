"""Real pure-VM boundary: known private wire and state stay host-owned."""
import base64
import hashlib
import json

import pytest

from lladar.browser_target import BrowserTarget
from lladar.answer_extraction import ExtractionError


SECRET = "local-sensitive-session-83dc21"


class WireDriver:
    def __init__(self):
        self._secrets = {SECRET}
        wire = json.dumps({"session_id": SECRET, "turn": "turn-123", "content": "Complete answer"}).encode()
        self.original = {"event_id": "wire-1", "sequence": 1, "kind": "websocket_received",
                         "binary_b64": base64.b64encode(wire).decode(),
                         "payload_sha256": hashlib.sha256(wire).hexdigest()}

    def raw_event(self, identifier):
        assert identifier == "wire-1"
        return dict(self.original)

    def read_event(self, identifier, **_):
        return {key: value for key, value in self.raw_event(identifier).items() if key != "binary_b64"}

    def inspect(self):
        return {"frames": []}


def target(tmp_path):
    return BrowserTarget(page_url="https://example.test", driver=WireDriver(), workspace=tmp_path,
                         output_fn=lambda _: None)


def test_known_private_binary_is_projected_before_real_vm_and_raw_digest_is_host_only(tmp_path):
    owner = target(tmp_path)
    original = dict(owner.driver.original)
    event = owner._protocol_event("wire-1")
    wire = base64.b64decode(event["binary_b64"])
    assert SECRET.encode() not in wire
    assert len(wire) == len(base64.b64decode(original["binary_b64"]))
    assert event["payload_sha256"] == hashlib.sha256(wire).hexdigest()
    assert original["payload_sha256"] not in json.dumps(event)
    assert owner.driver.original == original
    source = '''
import base64
import json
def adapt(payload):
    wire = base64.b64decode(payload["events"][0]["binary_b64"])
    return {"decoded": json.loads(wire), "hex": wire.hex()}
'''
    decoded = owner._runtime().execute(source, {"events": [event]})
    assert SECRET not in json.dumps(decoded)
    assert SECRET.encode().hex() not in decoded["hex"]
    assert owner._protocol_event("wire-1") == event


def test_known_metadata_in_context_is_replaced_before_vm(tmp_path):
    owner = target(tmp_path)
    owner._state = {"session_id": SECRET, "counter": 7}
    owner.candidate = {"source": '''
def adapt(payload):
    return {"context": payload["context"], "seen": payload["context"]["session_id"]}
'''}
    result = owner._execute("describe")
    assert SECRET not in result["seen"]
    assert SECRET not in json.dumps(owner._state)
    assert owner._state["counter"] == 7


def test_new_labelled_private_context_is_learned_and_never_reused_as_raw_vm_input(tmp_path):
    owner = target(tmp_path)
    fresh = "previously-unseen-session-e182"
    owner.candidate = {"source": 'def adapt(payload):\n    return {"context": {"session_id": "' + fresh + '"}}'}
    owner._execute("describe")
    assert fresh in owner.driver._secrets
    assert fresh not in json.dumps(owner._state)
    owner.candidate = {"source": '''
def adapt(payload):
    return {"context": payload["context"], "seen": payload["context"]["session_id"]}
'''}
    assert fresh not in owner._execute("describe")["seen"]


def test_unlabelled_private_baseline_is_refused_before_vm(tmp_path):
    owner = target(tmp_path)
    owner.candidate = {"source": 'def adapt(payload):\n    return {}'}
    with pytest.raises(ExtractionError, match="sensitive_response"):
        owner._execute("describe", baseline={"answer": "prefix " + SECRET})


def test_projection_literal_cannot_be_emitted_as_answer_prose(tmp_path):
    owner = target(tmp_path)
    event = owner._protocol_event("wire-1")
    owner.candidate = {"source": '''
import base64
import json
def adapt(payload):
    envelope = json.loads(base64.b64decode(payload["observation"]["events"][0]["binary_b64"]))
    return {"materials": [{"data": "Answer contains " + envelope["session_id"]}]}
'''}
    with pytest.raises(ExtractionError, match="sensitive_response"):
        owner._execute("observe", observation={"events": [event]})


def test_projection_literal_byte_list_cannot_bypass_material_screen(tmp_path):
    owner = target(tmp_path)
    event = owner._protocol_event("wire-1")
    owner.candidate = {"source": '''
import base64
import json
def adapt(payload):
    envelope = json.loads(base64.b64decode(payload["observation"]["events"][0]["binary_b64"]))
    return {"materials": [{"data": [ord(c) for c in envelope["session_id"]]}]}
'''}
    with pytest.raises(ExtractionError, match="sensitive_response"):
        owner._execute("observe", observation={"events": [event]})


def test_issued_view_remains_pinned_for_validation_after_new_metadata_learning(tmp_path):
    owner = target(tmp_path)
    owner.driver._secrets.clear()
    issued = owner._protocol_event("wire-1")
    owner.driver._secrets.add(SECRET)
    assert owner._protocol_authority("wire-1") == issued
    # The next interpreter invocation gets a new safe view, not the old cache.
    refreshed = owner._protocol_event("wire-1")
    assert refreshed != issued
    assert SECRET.encode() not in base64.b64decode(refreshed["binary_b64"])
    assert owner._protocol_authority("wire-1") == refreshed


def test_host_receipt_refuses_native_event_mutation(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    owner = target(tmp_path)
    owner._protocol_event("wire-1")
    owner.driver.original["sequence"] = 999
    with pytest.raises(BrowserDiscoveryError, match="response_source_unconfirmed"):
        owner._protocol_authority("wire-1")


def test_partial_event_is_never_treated_as_complete_protocol_material(tmp_path, monkeypatch):
    from lladar.browser_target import BrowserDiscoveryError
    owner = target(tmp_path)
    monkeypatch.setattr(owner.driver, 'read_event', lambda *_, **__: {"next_offset": 65536})
    with pytest.raises(BrowserDiscoveryError, match="response_size_limit"):
        owner._protocol_event("wire-1")


def test_aligned_base64_mask_in_host_issued_wire_is_valid_vm_input(tmp_path):
    owner = target(tmp_path)
    wire=b'ABC'+SECRET.encode()+b'XYZ'
    owner.driver.original.update(binary_b64=base64.b64encode(wire).decode(),
                                 payload_sha256=hashlib.sha256(wire).hexdigest())
    event=owner._protocol_event('wire-1')
    owner.candidate={'source': '''
import base64
def adapt(payload):
    return {"wire_length":len(base64.b64decode(payload["observation"]["events"][0]["binary_b64"]))}
'''}
    assert owner._execute('observe',observation={'events':[event]})['wire_length']==len(wire)


def test_actor_context_cannot_claim_trusted_binary_exemption(tmp_path):
    owner=target(tmp_path)
    event=owner._protocol_event('wire-1')
    owner._state={'binary_b64':base64.b64encode(SECRET.encode()).decode()}
    owner.candidate={'source':'def adapt(payload):\n    return {}'}
    with pytest.raises(ExtractionError,match='sensitive_response'):
        owner._execute('observe',observation={'events':[event]})


def test_new_private_metadata_and_reencoded_context_are_screened_together(tmp_path):
    owner=target(tmp_path)
    fresh='new-session-9c348b'
    encoded=base64.b64encode(fresh.encode()).decode()
    owner.candidate={'source':'def adapt(payload):\n    return '+repr({'context':{'session_id':fresh,'copy':encoded}})}
    with pytest.raises(ExtractionError,match='sensitive_response'):
        owner._execute('describe')


def test_encoded_private_text_is_stopped_before_any_interpreter_receives_it(tmp_path):
    owner=target(tmp_path)
    owner.driver.original={'event_id':'wire-1','kind':'http_body','sequence':1,
                           'text':json.dumps({'content':base64.b64encode(SECRET.encode()).decode()})}
    with pytest.raises(ExtractionError,match='sensitive_response'):
        owner._protocol_event('wire-1')


def test_all_actor_tool_outputs_screen_encoded_private_values(tmp_path):
    owner=target(tmp_path)
    with pytest.raises(ExtractionError,match='sensitive_response'):
        owner._tool_result({'frame':{'text':base64.b64encode(SECRET.encode()).decode()}})


def test_inspect_phase_only_exempts_exact_host_issued_projected_binary(tmp_path):
    owner=target(tmp_path)
    wire=b'ABC'+SECRET.encode()+b'XYZ'
    owner.driver.original.update(binary_b64=base64.b64encode(wire).decode(),
                                 payload_sha256=hashlib.sha256(wire).hexdigest())
    event=owner._protocol_event('wire-1')
    payload=owner._protocol_payload({'phase':'inspect','events':[event]})
    assert payload['events'][0]['binary_b64']==event['binary_b64']
    forged=dict(event,sequence=89)
    from lladar.browser_target import BrowserDiscoveryError
    with pytest.raises(BrowserDiscoveryError,match='response_source_unconfirmed'):
        owner._protocol_payload({'phase':'inspect','events':[forged]})


@pytest.mark.parametrize('transport',['base64','byte-list','request-ref','dom-draft'])
def test_observe_actions_cannot_hide_question_submission_without_intent(tmp_path,transport):
    from lladar.browser_target import BrowserDiscoveryError
    owner=target(tmp_path)
    question='Only submit this question once.'
    owner._current_question=question
    if transport=='base64':
        action={'kind':'websocket','connection_id':'peer','binary':True,
                'value':base64.b64encode(b'framing:'+question.encode()).decode()}
    elif transport=='byte-list':
        action={'kind':'http','url':'https://example.test','method':'POST','body':list(question.encode())}
    elif transport=='request-ref':
        owner.driver.original.update(kind='http_request',url='https://example.test',method='POST',body=question)
        action={'kind':'http','request_ref':'wire-1'}
    else:
        owner.driver.inspect=lambda:{'active_page_index':0,'frames':[{'page_index':0,'nodes':[
            {'editable':True,'private':False,'value':question}]}]}
        action={'kind':'press','ref':'current-editor','key':'Enter'}
    owner.candidate={'source':'def adapt(payload):\n    return '+repr({'actions':[action]})}
    with pytest.raises(BrowserDiscoveryError,match='unmarked_submission'):
        owner._execute('observe',turn_id='current')
    assert owner.ledger.data['counts'].get('actions',0)==0
    assert owner.ledger.data['counts'].get('submissions',0)==0


def test_private_value_learned_from_other_input_cannot_leave_stale_wire_exempt(tmp_path):
    from lladar.browser_target import BrowserDiscoveryError
    owner=target(tmp_path)
    owner.driver._secrets.clear()
    event=owner._protocol_event('wire-1')
    owner._state={'session_id':SECRET}
    owner.candidate={'source':'def adapt(payload):\n    return {}'}
    with pytest.raises(BrowserDiscoveryError,match='protected_protocol_material'):
        owner._execute('observe',observation={'events':[event]})
    assert SECRET in owner.driver._secrets
    # A fresh read now has the complete new protected-value set.
    refreshed=owner._protocol_event('wire-1')
    assert owner._execute('observe',observation={'events':[refreshed]})=={}
