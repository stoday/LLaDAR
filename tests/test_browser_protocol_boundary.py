"""Authorized recorded material stays exact; host receipts enforce its identity."""
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



def test_authorized_wire_reaches_real_vm_without_projection(tmp_path):
    owner = target(tmp_path)
    original = dict(owner.driver.original)
    event = owner._protocol_event("wire-1")
    assert event["binary_b64"] == original["binary_b64"]
    assert event["payload_sha256"] == original["payload_sha256"]
    source = "import base64\nimport json\ndef adapt(payload):\n    return json.loads(base64.b64decode(payload['events'][0]['binary_b64']))"
    assert owner._runtime().execute(source, {"events": [event]})["session_id"] == SECRET
    assert owner.driver.original == original


def test_authorized_context_baseline_and_encoded_tool_material_remain_exact(tmp_path):
    owner = target(tmp_path)
    encoded = base64.b64encode(SECRET.encode()).decode()
    owner._state = {"session_id": SECRET, "copy": encoded}
    owner.candidate = {"source": "def adapt(payload):\n    return {'context': payload['context'], 'baseline': payload['baseline']}"}
    baseline = {"answer": "prefix " + SECRET}
    result = owner._execute("describe", baseline=baseline)
    assert result["context"] == owner._state == {"session_id": SECRET, "copy": encoded}
    assert result["baseline"] == baseline
    assert json.loads(owner._tool_result({"text": encoded})) == {"text": encoded}


def test_learning_metadata_does_not_rewrite_issued_protocol_view(tmp_path):
    owner = target(tmp_path)
    owner.driver._secrets.clear()
    issued = owner._protocol_event("wire-1")
    owner.driver._secrets.add(SECRET)
    assert owner._protocol_authority("wire-1") == issued
    assert owner._protocol_event("wire-1") == issued
