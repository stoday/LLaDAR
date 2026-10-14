"""Host rejects invented answers, stale sources and unrelated completion."""
import base64
import hashlib
import json

import pytest

from lladar.browser_provenance import validate_turn_materials
from lladar.browser_target import BrowserDiscoveryError


def event(number,kind,**data):
    return {"event_id":str(number),"sequence":number,"kind":kind,"turn_id":"current","page_id":"page", "observation_source":"browser_network",**data}


def check(events,result,baseline=None,page=None,receipt_method="http"):
    if receipt_method and not any(e["kind"]=="submission_attempted" for e in events):
        events=[event(0,"submission_attempted",method=receipt_method,observation_source="browser_host",
                      connection_id="same" if receipt_method=="websocket" else None)]+events
    by_id={e["event_id"]:e for e in events}
    return validate_turn_materials(result,{"turn_id":"current","events":events,"page":page or {"active_page_id":"page"}},
        baseline or {"start_sequence":0,"page":{"active_page_id":"page"}},"Current question",lambda ref:by_id[ref])


def http_case():
    events=[event(1,"http_request",url="https://example.test/ask",method="POST",body=json.dumps({"question":"Current question"})),
            event(2,"http_body",request_event_id="1",text='{"content":"Complete actual answer"}'),
            event(3,"http_finished",request_event_id="1")]
    result={"materials":[{"event_ids":["2"],"data":events[1]["text"]}],
            "evidence":{"submission_events":["1"],"completion_events":["3"]}}
    return events,result


def test_full_actual_http_source_passes_with_independent_reference_still_required():
    events,result=http_case()
    assert check(events,result)=={"send_method":"network","receive_method":"network","requires_independent_reference":True}


def test_dom_submit_with_http_receive_keeps_actual_host_send_method():
    events,result=http_case()
    events.insert(0,event(0,"submission_attempted",method="click",observation_source="browser_host",
                          input_values=["Current question"]))
    # The candidate chooses only the native HTTP request as its send lineage.
    assert result["evidence"]["submission_events"]==["1"]
    assert check(events,result)=={"send_method":"dom","receive_method":"network","requires_independent_reference":True}


def test_dom_submit_with_websocket_receive_keeps_actual_host_send_method():
    request=event(1,"websocket_sent",connection_id="same",text='{"question":"Current question","turn":"current-77"}')
    response=event(2,"websocket_received",connection_id="same",text='{"turn":"current-77","content":"Complete actual answer"}')
    events=[event(0,"submission_attempted",method="press",observation_source="browser_host",input_values=["Current question"]),request,response]
    result={"materials":[{"event_ids":["2"],"data":response["text"]}],
            "evidence":{"submission_events":["1"],"completion_events":["2"]}}
    assert check(events,result)["send_method"]=="dom"


def test_native_submission_without_host_receipt_or_with_ambiguous_receipts_is_rejected():
    events,result=http_case()
    with pytest.raises(BrowserDiscoveryError,match="submission_unconfirmed"):
        check(events,result,receipt_method=None)
    events=[event(0,"submission_attempted",method="http",observation_source="browser_host"),
            event(-1,"submission_attempted",method="click",observation_source="browser_host")]+events
    # Both are in the turn's authorized event range, so ambiguity is explicit.
    events[1]["sequence"]=0
    with pytest.raises(BrowserDiscoveryError,match="submission_unconfirmed"):
        check(events,result)


@pytest.mark.parametrize("alter",["receipt_page","native_page","native_before_receipt","receipt_method"])
def test_submission_receipt_cannot_be_rebound_to_another_page_method_or_wire(alter):
    events,result=http_case()
    receipt=event(0,"submission_attempted",method="http",observation_source="browser_host")
    events.insert(0,receipt)
    if alter=="receipt_page":
        receipt["page_id"]="other-page"
    elif alter=="native_page":
        events[1]["page_id"]="other-page"
    elif alter=="native_before_receipt":
        receipt["sequence"]=events[1]["sequence"]
    else:
        receipt["method"]="websocket"
        receipt["connection_id"]="same"
    with pytest.raises(BrowserDiscoveryError,match="submission_unconfirmed"):
        check(events,result)


def test_fabricated_formal_answer_is_rejected_even_with_real_source_ids():
    events,result=http_case()
    result["materials"][0]["data"]='{"content":"A fabricated answer"}'
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)


def test_whole_json_decoded_material_preserves_all_fields_types_and_array_order():
    events,result=http_case()
    events[1]["text"]='{"content":"Complete actual answer","metadata":{"count":1},"blocks":["First","Tail"]}'
    result["materials"][0]["data"]=json.loads(events[1]["text"])
    assert check(events,result)["receive_method"]=="network"
    result["materials"][0]["data"]["blocks"].reverse()
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)
    result["materials"][0]["data"]=json.loads(events[1]["text"])
    result["materials"][0]["data"]["metadata"]["count"]=True
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)
    result["materials"][0]["data"]={"content":"Complete actual answer"}
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)
    events[1]["text"]='[{"content":"First"},{"content":"Tail"}]'
    result["materials"][0]["data"]=json.loads(events[1]["text"])
    assert check(events,result)["receive_method"]=="network"
    result["materials"][0]["data"].reverse()
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)


def test_background_completed_request_cannot_complete_current_question():
    events,result=http_case()
    events.extend([event(4,"http_request",url="https://example.test/background",method="GET",body=""),
                   event(5,"http_finished",request_event_id="4")])
    result["evidence"]["completion_events"]=["5"]
    with pytest.raises(BrowserDiscoveryError,match="completion_unconfirmed"):
        check(events,result)


def test_previous_turn_source_is_not_relabelled_as_current():
    events,result=http_case()
    events[1]["turn_id"]="previous"
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)


def test_poll_source_requires_observed_ack_identifier_flow():
    events=[event(1,"http_request",url="https://example.test/start",method="POST",body='{"question":"Current question"}'),
            event(2,"http_body",request_event_id="1",text='{"job_id":"dynamic-request-77","state":"accepted"}'),
            event(3,"http_request",url="https://example.test/jobs/wrong-request",method="GET",body=""),
            event(4,"http_body",request_event_id="3",text='{"content":"Complete actual answer"}'),
            event(5,"http_finished",request_event_id="3")]
    result={"materials":[{"event_ids":["2"],"data":events[1]["text"]},{"event_ids":["4"],"data":events[3]["text"]}],
            "evidence":{"submission_events":["1"],"completion_events":["5"]}}
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)
    events[2]["url"]="https://example.test/jobs/dynamic-request-77"
    assert check(events,result)["send_method"]=="network"


def test_all_visible_stream_chunks_are_required_in_original_order():
    events,_=http_case()
    events=events[:1]+[event(2,"fetch_start",request_id="f",url="https://example.test/ask",method="POST"),
                      event(3,"fetch_chunk",request_id="f",text="First block"),
                      event(4,"fetch_chunk",request_id="f",text="Tail block"),event(5,"fetch_end",request_id="f")]
    result={"materials":[{"event_ids":["3"],"data":"First block"}],
            "evidence":{"submission_events":["1"],"completion_events":["5"]}}
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result)
    result["materials"]=[{"event_ids":["3","4"],"data":"First blockTail block"}]
    assert check(events,result)["requires_independent_reference"]


def binary_event(number,kind,envelope):
    wire=b"XX\x00\x01"+json.dumps(envelope).encode()
    return event(number,kind,connection_id="same",binary_b64=base64.b64encode(wire).decode(),
                 payload_sha256=hashlib.sha256(wire).hexdigest())


def test_background_binary_on_same_socket_cannot_supply_answer():
    sent_data={"question":"Current question","client_turn":"current"}
    reply_data={"client_turn":"other-turn","content":"Background text","stage":"finished"}
    sent=binary_event(1,"websocket_sent",sent_data)
    reply=binary_event(2,"websocket_received",reply_data)
    result={"materials":[{"event_ids":["2"],"source_hashes":{"2":reply["payload_sha256"]},"data":json.dumps(reply_data)}],
      "evidence":{"submission_events":["1"],"completion_events":["2"],
        "decoded_submission":{"event_id":"1","source_sha256":sent["payload_sha256"],"data":sent_data}}}
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check([sent,reply],result,receipt_method="websocket")
    reply_data["client_turn"]="current"
    reply=binary_event(2,"websocket_received",reply_data)
    result["materials"][0]["source_hashes"]={"2":reply["payload_sha256"]}
    result["materials"][0]["data"]=json.dumps(reply_data)
    assert check([sent,reply],result,receipt_method="websocket")["requires_independent_reference"]
    result["materials"][0]["data"]=json.dumps({**reply_data,"content":"Invented text"})
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check([sent,reply],result,receipt_method="websocket")


def test_dom_omitted_block_and_wrong_transition_are_rejected():
    before={"frame_id":"frame","page_id":"page","nodes":[{"ref":"status","attributes":{"data-stage":"idle"}}]}
    after={"frame_id":"frame","page_id":"page","nodes":[{"ref":"status","attributes":{"data-stage":"finished"}},
                                         {"ref":"a","text":"First"},{"ref":"b","text":"Tail"}]}
    events=[event(1,"submission_attempted",method="click",observation_source="browser_host",input_values=["Current question"]),
            event(2,"dom_changed",observation_source="browser_dom",changes=[])]
    result={"materials":[{"frame_id":"frame","event_ids":["2"],"data":json.dumps(after)}],
      "evidence":{"submission_events":["1"],"completion_events":["2"],
        "dom_completion":{"frame_id":"frame","ref":"status","attribute":"data-stage","before":"idle","after":"finished"}}}
    baseline={"start_sequence":1,"baseline":{"active_page_id":"page","frames":[before]}}
    page={"active_page_id":"page","frames":[after]}
    assert check(events,result,baseline,page)["receive_method"]=="dom"
    result["materials"][0]["data"]=json.dumps({**after,"nodes":after["nodes"][:-1]})
    with pytest.raises(BrowserDiscoveryError,match="response_source_unconfirmed"):
        check(events,result,baseline,page)
    result["materials"][0]["data"]=json.dumps(after)
    result["evidence"]["dom_completion"]["before"]="working"
    with pytest.raises(BrowserDiscoveryError,match="completion_unconfirmed"):
        check(events,result,baseline,page)

