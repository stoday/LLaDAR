"""Host checks for fresh website sources, transport linkage and whole material.

These checks establish provenance, not unknown protocol semantics. Generated
decoders remain untrusted; a successful return always requires an independent
same-turn full-answer reference before publishing a result.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, unquote, urlsplit


def _fail(reason):
    from .browser_target import BrowserDiscoveryError
    raise BrowserDiscoveryError(reason, request_attempted=True)


def _json(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _same_json(left,right):
    if type(left) is not type(right):
        return False
    if isinstance(left,dict):
        return left.keys()==right.keys() and all(_same_json(left[key],right[key]) for key in left)
    if isinstance(left,list):
        return len(left)==len(right) and all(_same_json(a,b) for a,b in zip(left,right))
    return left==right


def _scalars(value):
    if isinstance(value, dict):
        return {item for child in value.values() for item in _scalars(child)}
    if isinstance(value, list):
        return {item for child in value for item in _scalars(child)}
    if isinstance(value, str) and value:
        return {value}
    return set()


def _request_values(event):
    values = _scalars(_json(event.get("body", "")))
    values.update(v for _, v in parse_qsl(event.get("body", ""), keep_blank_values=True))
    parts = urlsplit(event.get("url", ""))
    values.update(v for _, v in parse_qsl(parts.query, keep_blank_values=True))
    values.update(unquote(piece) for piece in parts.path.split("/") if piece)
    return values


def _wire_question(event, question):
    from .browser_target import _wire_contains_question
    return _wire_contains_question(event.get("body", event.get("text", "")), question) or _wire_contains_question(event.get("url", ""), question)


def _bytes(event):
    try:
        return base64.b64decode(event["binary_b64"],validate=True)
    except (KeyError, ValueError, TypeError):
        _fail("response_source_unconfirmed")


def _whole_json_envelope(wire):
    """Find a whole JSON document carried after opaque transport framing.

    This is a generic lineage check, not a website decoder. Non-JSON wire
    formats are not inferred here and must use independent reference checks.
    A nested field or a selected answer string cannot pass this whole-document
    comparison because its closing bytes do not consume the original document.
    """
    from .resource_limits import resource_limit
    text=wire.decode("utf-8",errors="surrogateescape")
    decoder=json.JSONDecoder()
    end_only=re.compile(r"\s*\Z")
    deadline=time.monotonic()+resource_limit("PROTOCOL_TIMEOUT_SECONDS")
    for position, character in enumerate(text):
        if time.monotonic()>deadline:
            _fail("response_source_unconfirmed")
        if character not in ("{","["):
            continue
        try:
            value,end=decoder.raw_decode(text,position)
            if end_only.match(text,end):
                return value
        except (ValueError,UnicodeError,RecursionError):
            continue
    return None


def _frame_nodes(page):
    return {f["frame_id"]:{n["ref"]:n for n in f.get("nodes",[])} for f in page.get("frames",[])}


def validate_turn_materials(result, observation, baseline, question, raw_event):
    """Validate observed provenance; require an independent fulltext reference.

    Binary material includes source_hashes={event_id:payload_sha256}. Binary
    submission evidence includes decoded_submission={event_id,data,source_sha256}
    retaining the complete decoded envelope. Hashes prove which wire was read;
    they do not attest generated decoder semantics. The caller must independently
    verify the full website answer on every probe, verification and formal turn.
    """
    turn_id = observation.get("turn_id")
    entries = observation.get("events",[])
    if not isinstance(turn_id,str) or not turn_id or not isinstance(entries,list):
        _fail("response_source_unconfirmed")
    events={}
    start=baseline.get("start_sequence",baseline.get("sequence",0))
    for entry in entries:
        identifier=entry.get("event_id")
        if not isinstance(identifier,str):
            _fail("response_source_unconfirmed")
        original=raw_event(identifier)
        if original != entry or original.get("turn_id") != turn_id or original.get("sequence",0)<start:
            _fail("response_source_unconfirmed")
        events[identifier]=original
    evidence=result.get("evidence",{})
    sent_refs=evidence.get("submission_events",[])
    done_refs=evidence.get("completion_events",[])
    if not isinstance(sent_refs,list) or not sent_refs or any(r not in events for r in sent_refs):
        _fail("submission_unconfirmed")
    if not isinstance(done_refs,list) or not done_refs or any(r not in events for r in done_refs):
        _fail("completion_unconfirmed")
    # The candidate selects lineage references, never the action classification.
    # A host receipt is emitted only after the caller's write-ahead intent and
    # remains authoritative even when the candidate references only wire data.
    receipts=[e for e in events.values() if e["kind"]=="submission_attempted" and e.get("observation_source")=="browser_host"]
    if len(receipts)!=1:
        _fail("submission_unconfirmed")
    receipt=receipts[0]
    methods={"click":"dom","press":"dom","check":"dom","select":"dom","navigate":"dom",
             "http":"network","websocket":"websocket"}
    method=methods.get(receipt.get("method"))
    receipt_page=receipt.get("page_id")
    page=observation.get("page",{})
    prior_page=baseline.get("baseline",baseline.get("page",{}))
    if (method is None or not isinstance(receipt_page,str) or not receipt_page
            or receipt_page!=prior_page.get("active_page_id") or receipt_page!=page.get("active_page_id")):
        _fail("submission_unconfirmed")
    sent=[events[r] for r in sent_refs]
    native=[e for e in sent if e.get("observation_source")=="browser_network" and e["kind"] in {"http_request","websocket_sent"}]
    dom=[receipt] if method=="dom" else []
    if not native and not dom:
        _fail("submission_unconfirmed")
    if any(e not in native and e!=receipt for e in sent):
        _fail("submission_unconfirmed")
    if any(e.get("page_id")!=receipt_page or e["sequence"]<=receipt["sequence"] for e in native):
        _fail("submission_unconfirmed")
    if method=="network" and (not native or any(e["kind"]!="http_request" for e in native)):
        _fail("submission_unconfirmed")
    if method=="websocket" and (not native or any(e["kind"]!="websocket_sent" or e.get("connection_id")!=receipt.get("connection_id") for e in native)):
        _fail("submission_unconfirmed")
    first_send=receipt["sequence"]
    if any(events[r]["sequence"]<=first_send or events[r]["kind"] in {"http_request","websocket_sent","submission_attempted"} for r in done_refs):
        _fail("completion_unconfirmed")
    if dom and question not in receipt.get("input_values",[]) and not (receipt.get("method")=="navigate" and _wire_question(receipt,question)):
        _fail("request_identity_mismatch")
    decoded_sent={}
    for event in native:
        if event.get("binary_b64"):
            claim=evidence.get("decoded_submission",{})
            if isinstance(claim,list):
                claim=next((c for c in claim if c.get("event_id")==event["event_id"]),{})
            wire=_bytes(event)
            if claim.get("event_id")!=event["event_id"] or claim.get("source_sha256")!=hashlib.sha256(wire).hexdigest():
                _fail("request_identity_mismatch")
            decoded=_json(claim.get("data"))
            envelope=_whole_json_envelope(wire)
            if not isinstance(decoded,(dict,list)) or (envelope is not None and decoded!=envelope) or question not in _scalars(decoded):
                _fail("request_identity_mismatch")
            decoded_sent[event["event_id"]]=decoded
        elif not _wire_question(event,question) and not dom:
            _fail("request_identity_mismatch")

    http_chain={e["event_id"] for e in native if e["kind"]=="http_request"}
    # Discover ack -> dynamic-ID -> polling linkage from actual inbound data,
    # without assuming endpoint names, JSON fields or a framework schema.
    for event in sorted(events.values(),key=lambda e:e["sequence"]):
        if event["kind"]!="http_request" or event["event_id"] in http_chain or event.get("observation_source")!="browser_network" or event.get("page_id")!=receipt_page:
            continue
        values=_request_values(event)-{question}
        for reply in events.values():
            if reply["kind"]=="http_body" and reply.get("request_event_id") in http_chain and reply["sequence"]<event["sequence"]:
                if values & (_scalars(_json(reply.get("text","")))-{question}):
                    http_chain.add(event["event_id"])
                    break
    fetch_chain={}
    for event in events.values():
        if event["kind"]=="fetch_start":
            requests=[e for e in events.values() if e["kind"]=="http_request" and e.get("url")==event.get("url")
                      and e.get("method","").upper()==event.get("method","").upper() and e["event_id"] in http_chain]
            if len(requests)==1:
                fetch_chain[event.get("request_id")]=requests[0]["event_id"]
    socket_sends={e.get("connection_id"):e for e in native if e["kind"]=="websocket_sent"}
    material_refs=set()
    used_frames=set()
    used_fetch=set()
    decoded_replies={}
    materials=result.get("materials")
    if not isinstance(materials,list) or not materials:
        _fail("response_material_missing")
    for material in materials:
        refs=material.get("event_ids",[])
        if not isinstance(refs,list) or not refs or any(r not in events for r in refs):
            _fail("response_source_unconfirmed")
        sources=[events[r] for r in refs]
        if any(e["sequence"]<=first_send or e.get("page_id")!=receipt_page for e in sources):
            _fail("response_source_unconfirmed")
        if [e["sequence"] for e in sources]!=sorted(e["sequence"] for e in sources):
            _fail("response_source_unconfirmed")
        data=material.get("data")
        if material.get("frame_id"):
            frame=next((f for f in page.get("frames",[]) if f["frame_id"]==material["frame_id"]),None)
            if frame is None or frame.get("page_id")!=receipt_page or _json(data)!=frame or not all(
                    e["kind"]=="dom_changed" and e.get("observation_source")=="browser_dom" and e.get("page_id")==receipt_page for e in sources):
                _fail("response_source_unconfirmed")
            used_frames.add(frame["frame_id"])
        elif any(e.get("binary_b64") for e in sources):
            claims=material.get("source_hashes",{})
            envelopes=[]
            for event in sources:
                if event["kind"]!="websocket_received" or event.get("observation_source")!="browser_network" or event.get("connection_id") not in socket_sends:
                    _fail("response_source_unconfirmed")
                wire=_bytes(event)
                if claims.get(event["event_id"])!=hashlib.sha256(wire).hexdigest():
                    _fail("response_source_unconfirmed")
                envelopes.append(_whole_json_envelope(wire))
            decoded=_json(data)
            if not isinstance(decoded,(dict,list)):
                _fail("response_source_unconfirmed")
            known=[e for e in envelopes if e is not None]
            if known and decoded!=(known[0] if len(known)==1 else known):
                _fail("response_source_unconfirmed")
            for event in sources:
                sent_event=socket_sends[event["connection_id"]]
                outbound=decoded_sent.get(sent_event["event_id"],_json(sent_event.get("text")))
                shared=(_scalars(outbound)-{question}) & (_scalars(decoded)-{question})
                if not shared:
                    _fail("response_source_unconfirmed")
                decoded_replies[event["event_id"]]=decoded
        else:
            for event in sources:
                kind=event["kind"]
                if kind=="http_body" and event.get("request_event_id") in http_chain:
                    continue
                if kind in {"fetch_chunk","fetch_end"} and event.get("request_id") in fetch_chain:
                    used_fetch.add(event["request_id"])
                    continue
                if kind=="eventsource_message" and event.get("request_event_id") in http_chain:
                    continue
                if kind=="websocket_received" and event.get("observation_source")=="browser_network" and event.get("connection_id") in socket_sends:
                    outbound=_json(socket_sends[event["connection_id"]].get("text"))
                    inbound=_json(event.get("text"))
                    if not ((_scalars(outbound)-{question}) & (_scalars(inbound)-{question})):
                        _fail("response_source_unconfirmed")
                    continue
                _fail("response_source_unconfirmed")
            original="".join(e.get("text","") for e in sources)
            if data!=original and not (isinstance(data,(dict,list)) and _same_json(_json(original),data)):
                _fail("response_source_unconfirmed")
        material_refs.update(refs)

    for ref in done_refs:
        event=events[ref]
        if event["kind"] in {"http_finished","http_body"} and event.get("request_event_id") in http_chain:
            continue
        if event["kind"] in {"fetch_end","fetch_chunk"} and event.get("request_id") in fetch_chain and (event["kind"]=="fetch_end" or ref in material_refs):
            continue
        if event["kind"] in {"websocket_received","eventsource_message"} and ref in material_refs:
            continue
        if event["kind"]=="dom_changed" and used_frames and ref in material_refs:
            continue
        _fail("completion_unconfirmed")
    for event in events.values():
        if event["kind"]=="fetch_chunk" and event.get("request_id") in used_fetch and event["event_id"] not in material_refs:
            _fail("response_source_unconfirmed")
    if used_frames:
        claim=evidence.get("dom_completion",{})
        before_nodes=_frame_nodes(prior_page)
        after_nodes=_frame_nodes(page)
        fid,ref,attribute=claim.get("frame_id"),claim.get("ref"),claim.get("attribute")
        before=before_nodes.get(fid,{}).get(ref,{})
        after=after_nodes.get(fid,{}).get(ref,{})
        if not dom or fid not in used_frames or not attribute or before.get("attributes",{}).get(attribute)!=claim.get("before") or after.get("attributes",{}).get(attribute)!=claim.get("after") or claim.get("before")==claim.get("after"):
            _fail("completion_unconfirmed")
    return {"send_method":method,
            "receive_method":"dom" if used_frames else "network",
            "requires_independent_reference":True}

