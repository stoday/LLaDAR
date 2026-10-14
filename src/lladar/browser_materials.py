"""Bounded metadata deidentification for locally decoded protocol material.

This is a conservative screen, not proof that arbitrary unknown binary protocols
contain no private information. It does not recognize or choose answer fields.
Unlabelled prose containing known private values is refused, never rewritten.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re

from .answer_extraction import ExtractionError, contains_sensitive_text
from .resource_limits import resource_limit


_LABEL=re.compile(r"password|passwd|authorization|cookie|credential|client.?secret|api.?key|access.?token|refresh.?token|(?:^|[_-])token(?:$|[_-])|session(?:.?id|.?key|.?token)?$|csrf|nonce",re.I)
_PRIVATE_TEXT=re.compile(r"(?:bearer\s+[a-z0-9._~-]+)|(?:eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)|(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",re.I)
_HOST_REF=re.compile(r"\[host-private(?::private-metadata-[0-9]+)?\]\Z")


@dataclass(frozen=True)
class DeidentifiedMaterial:
    value: object
    learned_sensitive_values: tuple[str,...]
    field_refs: list[dict]


def private_screen_values(values):
    """Host-only known literals and bounded common encoded representations."""
    from .browser_protocol_projection import private_literal_forms
    patterns=set()
    pattern_bytes=0
    for secret in values:
        if not secret or len(str(secret))<2:
            continue
        try:
            forms=private_literal_forms(str(secret).encode('utf-8'))
        except UnicodeError:
            raise ExtractionError('sensitive_response') from None
        for form in forms:
            text=form.decode('utf-8')
            if text not in patterns:
                pattern_bytes+=len(form)
                if pattern_bytes>resource_limit('PROTOCOL_INPUT_BYTES'):
                    raise ExtractionError('response_size_limit')
                patterns.add(text)
    return tuple(sorted(patterns))


def deidentify_decoded(value, protected_values, source_events=()):
    """Keep JSON structure, replace labelled metadata with safe local refs.

    Ref values are host opaque. ``field_refs`` contains paths and source IDs,
    never original private values. ``value_ref`` is included only for observed
    native JSON metadata that the host request-binding tool can resolve directly.
    The separate learned tuple is host-only and must not be sent to a model.
    """
    source_events=tuple(source_events)
    try:
        encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (TypeError,ValueError,RecursionError):
        raise ExtractionError("sensitive_response") from None
    if len(encoded.encode("utf-8"))>resource_limit("PROTOCOL_OUTPUT_BYTES"):
        raise ExtractionError("response_size_limit")
    binaries=[]
    source_json=[]
    for event in source_events:
        if event.get("binary_b64"):
            try:
                binaries.append((event["event_id"],base64.b64decode(event["binary_b64"],validate=True)))
            except (KeyError,ValueError,TypeError):
                raise ExtractionError("sensitive_response") from None
        for field in ("text","body"):
            if isinstance(event.get(field),str):
                try:
                    source_json.append((event.get("event_id"),json.loads(event[field])))
                except (ValueError,RecursionError):
                    pass
    known={str(item) for item in protected_values if item and len(str(item))>1}
    learned=set()
    refs=[]
    source_index={}
    def index_source(identifier,node,path):
        if isinstance(node,dict):
            for key,item in node.items():
                validate_key(key)
                if item is None or type(item) in {str,int,float,bool}:
                    stamp=(key,json.dumps(item,ensure_ascii=False,allow_nan=False))
                    source_index.setdefault(stamp,[]).append({"event_id":identifier,"path":path+[key]})
                else:
                    index_source(identifier,item,path+[key])
        elif isinstance(node,list):
            for number,item in enumerate(node):
                index_source(identifier,item,path+[number])
    def read_json(text):
        try:
            decoded=json.loads(text)
            return decoded if isinstance(decoded,(dict,list)) else None
        except (ValueError,TypeError,RecursionError):
            return None
    def labelled(key,inherited):
        return bool(_LABEL.search(str(key))) or (inherited and str(key).lower() in {"id","key","token","value"})
    def scan(node,depth=0,inherited=False):
        if depth>resource_limit("PROTOCOL_RECURSION_DEPTH"):
            raise ExtractionError("sensitive_response")
        if isinstance(node,dict):
            for key,item in node.items():
                private=labelled(key,inherited)
                if isinstance(item,(dict,list)):
                    scan(item,depth+1,private)
                elif private:
                    if isinstance(item,(str,int,float)) and not isinstance(item,bool) and str(item):
                        if not isinstance(item,str) or not _HOST_REF.fullmatch(item):
                            learned.add(str(item))
                else:
                    scan(item,depth+1,False)
        elif isinstance(node,list):
            for item in node:
                scan(item,depth+1,inherited)
        elif isinstance(node,str):
            decoded=read_json(node)
            if decoded is not None:
                scan(decoded,depth+1)
    scan(value)
    identifiable=known|{item for item in learned if len(item)>1}
    # Learn first, then screen the complete output. A private labelled field and
    # a re-encoded copy in the same result/context must not escape separately.
    identifiable=set(private_screen_values(identifiable))
    def validate_key(key):
        # Keys enter both model material and host-binding paths. Refuse private
        # names instead of renaming keys and changing the protocol/answer shape.
        if type(key) is not str or contains_sensitive_text(key, tuple(identifiable)):
            raise ExtractionError("sensitive_response")
    try:
        # Include newly learned metadata values before indexing native paths.
        # Otherwise an output with safe key names can leak through value_ref.
        for identifier,data in source_json:
            if identifier:
                index_source(identifier,data,[])
    except (ValueError,RecursionError):
        raise ExtractionError("sensitive_response") from None
    def opaque(node):
        if isinstance(node,list) and node and all(type(v) is int and 0<=v<=255 for v in node):
            wire=bytes(node)
            if any(wire==raw for _,raw in binaries):
                raise ExtractionError("sensitive_response")
            if any(secret.encode('utf-8') in wire for secret in identifiable):
                raise ExtractionError("sensitive_response")
        if isinstance(node,str):
            for _,raw in binaries:
                if not raw:
                    continue
                # JSON wire already decoded into fields is examined recursively;
                # an encoded byte copy is not an acceptable privacy projection.
                encoded={base64.b64encode(raw).decode('ascii'),base64.urlsafe_b64encode(raw).decode('ascii')}
                forms=encoded|{text.rstrip('=') for text in encoded}|{raw.hex(),raw.hex().upper(),raw.decode('latin-1')}
                if node in forms and read_json(node) is None:
                    raise ExtractionError("sensitive_response")
    def sources_for(secret,label):
        return source_index.get((label,json.dumps(secret,ensure_ascii=False,allow_nan=False)),[])
    def visit(node,path,depth=0,inherited=False):
        if depth>resource_limit("PROTOCOL_RECURSION_DEPTH"):
            raise ExtractionError("sensitive_response")
        opaque(node)
        if isinstance(node,dict):
            result={}
            for key,item in node.items():
                validate_key(key)
                private=labelled(key,inherited)
                if isinstance(item,(dict,list)):
                    result[key]=visit(item,path+[key],depth+1,private)
                elif private:
                    if isinstance(item,str) and _HOST_REF.fullmatch(item):
                        result[key]=item
                        continue
                    private_ref="private-metadata-"+str(len(refs)+1)
                    entry={"private_ref":private_ref,"path":path+[key],"source_event_ids":[e.get("event_id") for e in source_events if e.get("event_id")]}
                    candidates=sources_for(item,key)
                    if len(candidates)==1:
                        entry["value_ref"]=candidates[0]
                    refs.append(entry)
                    result[key]="[host-private:"+private_ref+"]"
                else:
                    result[key]=visit(item,path+[key],depth+1,False)
            return result
        if isinstance(node,list):
            return [visit(item,path+[index],depth+1,inherited) for index,item in enumerate(node)]
        if isinstance(node,str):
            decoded=read_json(node)
            if decoded is not None:
                clean=visit(decoded,path+["$json"],depth+1)
                if clean==decoded:
                    return node  # preserve unchanged source/answer formatting
                return json.dumps(clean,ensure_ascii=False,allow_nan=False)
            if _PRIVATE_TEXT.search(node) or any(secret in node for secret in identifiable):
                raise ExtractionError("sensitive_response")
        return node
    try:
        clean=visit(value,[])
    except RecursionError:
        raise ExtractionError("sensitive_response") from None
    return DeidentifiedMaterial(clean,tuple(sorted(learned)),refs)

