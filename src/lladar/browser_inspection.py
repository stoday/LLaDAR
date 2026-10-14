"""Bounded Agent views of one immutable, host-owned browser snapshot.

This module changes presentation only. Paginated views and JSON field fragments
are never complete frames for provenance, extraction, or completion evidence.
The caller must keep using its original full observation for those operations.
"""
from __future__ import annotations

import copy
import json
import uuid

from .resource_limits import resource_limit


class BrowserInspectionError(ValueError):
    """Safe presentation failure; contains no source content."""

    def __init__(self,reason):
        self.reason=reason
        super().__init__(reason)


def _fail(reason):
    raise BrowserInspectionError(reason)


def _json(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False)


def _size(value):
    return len(_json(value).encode("utf-8"))


class BrowserInspectionPager:
    """Expose operable DOM references while bounding every returned JSON value.

    capture invalidates all previous tokens. read_dom always reads the pinned
    copy and does not contact the browser. Large node fields have field_ref
    descriptors: read_dom(..., field_ref=ref) returns explicit character pages
    of the complete JSON serialization. Concatenate those pages before parsing
    JSON; a single fragment is not an original field value or complete frame.
    """

    def __init__(self,input_byte_limit):
        if type(input_byte_limit) is not int or input_byte_limit<=0:
            _fail("browser_inspection_limit_invalid")
        self.input_byte_limit=input_byte_limit
        self._snapshot_id=None
        self._snapshot={}
        self._frames={}
        self._fields={}
        self._field_paths={}

    def _field(self,value,path):
        if path not in self._field_paths:
            ref=f"field-{len(self._fields)+1}"
            self._field_paths[path]=ref
            self._fields[ref]=_json(value)
        ref=self._field_paths[path]
        return {"field_ref":ref,"encoding":"json","total_chars":len(self._fields[ref]),
                "value_type": "string" if isinstance(value,str) else "object" if isinstance(value,dict)
                  else "array" if isinstance(value,list) else "scalar","complete":False}

    def _compact(self,value,path,budget):
        return copy.deepcopy(value) if _size(value)<=budget else self._field(value,path)

    def capture(self,snapshot):
        # A failed fresh capture must not leave a previous instant available
        # under a token the caller might accidentally reuse as the new view.
        self._snapshot_id=None
        self._snapshot={}
        self._frames={}
        self._fields={}
        self._field_paths={}
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get("frames"),list):
            _fail("browser_inspection_snapshot_invalid")
        try:
            pinned=copy.deepcopy(snapshot)
            _size(pinned)
        except (TypeError,ValueError,UnicodeError,RecursionError):
            _fail("browser_inspection_snapshot_invalid")
        frames={}
        for frame in pinned["frames"]:
            if (not isinstance(frame,dict) or not isinstance(frame.get("frame_id"),str) or not frame["frame_id"]
                    or frame["frame_id"] in frames or not isinstance(frame.get("nodes"),list)
                    or not isinstance(frame.get("text",""),str)):
                _fail("browser_inspection_snapshot_invalid")
            frames[frame["frame_id"]]=frame
        self._snapshot_id="dom-"+uuid.uuid4().hex
        self._snapshot=pinned
        self._frames=frames
        self._fields={}
        self._field_paths={}
        inventory={k:self._compact(v,(k,),self.input_byte_limit//4) for k,v in pinned.items() if k!="frames"}
        inventory.update(snapshot_id=self._snapshot_id,kind="browser_inspection",frames=[],total_frames=len(frames),next_frame_offset=None)
        summaries=[{k:v for k,v in f.items() if k not in {"nodes","text"}} | {
                   "total_nodes":len(f["nodes"]),"total_text_chars":len(f.get("text",""))} for f in frames.values()]
        inventory["frame_inventory"]=self._field(summaries,("frame_inventory",))
        for frame in frames.values():
            remaining=self.input_byte_limit-_size(inventory)-4
            try:
                first=self._page(frame,0,min(128,resource_limit("DOM_NODE_LIMIT")),0,resource_limit("PROJECT_PREVIEW_CHARS"),remaining)
            except Exception as error:
                if getattr(error,"reason",str(error))!="browser_inspection_limit_too_small":
                    raise
                break
            candidate={**inventory,"frames":[*inventory["frames"],first]}
            if _size(candidate)>self.input_byte_limit:
                break
            inventory=candidate
        if len(inventory["frames"])<len(frames):
            inventory["next_frame_offset"]=len(inventory["frames"])
        self._bounded(inventory)
        return inventory

    def _bounded(self,value):
        if _size(value)>self.input_byte_limit:
            _fail("browser_inspection_limit_too_small")
        return value

    def _node(self,node,path,budget):
        if not isinstance(node,dict):
            _fail("browser_inspection_snapshot_invalid")
        if _size(node)<=budget:
            return {**copy.deepcopy(node),"node_complete":True}
        # A shape always points to the whole original node, including fields
        # omitted from its operational preview. No attribute silently vanishes.
        result={k:self._compact(v,path+(k,),budget//4) for k,v in node.items()
                if k in {"ref","tag","role","editable","private","name","text","value","attributes"}}
        result.update(node_complete=False,details=self._field(node,path))
        if _size(result)>budget:
            result={k:copy.deepcopy(node[k]) for k in ("ref","tag","editable","private") if k in node}
            result.update(node_complete=False,details=self._field(node,path))
        return result

    def _page(self,frame,node_offset,node_limit,text_offset,text_limit,budget):
        text=frame.get("text","")
        nodes=frame["nodes"]
        result={k:self._compact(v,(frame["frame_id"],k),max(1,budget//4)) for k,v in frame.items() if k not in {"nodes","text"}}
        result.update(kind="dom_page",snapshot_id=self._snapshot_id,node_offset=node_offset,total_nodes=len(nodes),nodes=[],
                      next_node_offset=None,text_offset=text_offset,total_text_chars=len(text),text="",text_complete=False,next_text_offset=None)
        def offsets():
            consumed=node_offset+len(result["nodes"])
            result["next_node_offset"]=consumed if consumed<len(nodes) else None
            end=text_offset+len(result["text"])
            result["text_complete"]=end==len(text) and text_offset==0
            result["next_text_offset"]=end if end<len(text) else None
        offsets()
        remaining=budget-_size(result)
        # Account for each JSON element once rather than materializing the
        # entire requested range and repeatedly removing overflowing nodes.
        for i in range(node_offset,min(len(nodes),node_offset+node_limit)):
            node=self._node(nodes[i],(frame["frame_id"],"nodes",i),max(1,min(budget//2,remaining)))
            cost=_size(node)+(2 if result["nodes"] else 0)
            if cost>remaining:
                break
            result["nodes"].append(node)
            remaining-=cost
        offsets()
        while _size(result)>budget and result["nodes"]:
            result["nodes"].pop()
            offsets()
        if _size(result)>budget or (node_limit and node_offset<len(nodes) and not result["nodes"]):
            _fail("browser_inspection_limit_too_small")
        requested=text[text_offset:text_offset+text_limit]
        low,high=0,len(requested)
        while low<high:
            middle=(low+high+1)//2
            result["text"]=requested[:middle]
            offsets()
            if _size(result)<=budget:
                low=middle
            else:
                high=middle-1
        result["text"]=requested[:low]
        offsets()
        if text_limit and text_offset<len(text) and not result["text"] and not result["nodes"]:
            _fail("browser_inspection_limit_too_small")
        return result

    def read_dom(self,snapshot_id,frame_id,node_offset=0,node_limit=128,text_offset=0,text_limit=65536,*,field_ref=""):
        if snapshot_id!=self._snapshot_id or not self._snapshot_id:
            _fail("browser_inspection_snapshot_stale")
        if (not isinstance(frame_id,str) or not isinstance(field_ref,str)
                or any(type(v) is not int or v<0 for v in (node_offset,node_limit,text_offset,text_limit))
                or not node_limit and not text_limit):
            _fail("browser_inspection_cursor_invalid")
        if field_ref:
            if field_ref not in self._fields:
                _fail("browser_inspection_field_unknown")
            source=self._fields[field_ref]
            if text_offset>len(source) or not text_limit:
                _fail("browser_inspection_cursor_invalid")
            result={"kind":"dom_field_page","snapshot_id":self._snapshot_id,"field_ref":field_ref,"encoding":"json",
                    "text_offset":text_offset,"total_chars":len(source),"text":"","next_text_offset":None,"complete":False}
            def offsets():
                end=text_offset+len(result["text"])
                result["next_text_offset"]=end if end<len(source) else None
                result["complete"]=text_offset==0 and end==len(source)
            requested=source[text_offset:text_offset+text_limit]
            low,high=0,len(requested)
            while low<high:
                middle=(low+high+1)//2
                result["text"]=requested[:middle]
                offsets()
                if _size(result)<=self.input_byte_limit:
                    low=middle
                else:
                    high=middle-1
            result["text"]=requested[:low]
            offsets()
            if not low and text_offset<len(source):
                _fail("browser_inspection_limit_too_small")
            return self._bounded(result)
        if frame_id not in self._frames:
            _fail("browser_inspection_frame_unknown")
        frame=self._frames[frame_id]
        if node_offset>len(frame["nodes"]) or text_offset>len(frame.get("text","")):
            _fail("browser_inspection_cursor_invalid")
        return self._bounded(self._page(frame,node_offset,node_limit,text_offset,text_limit,self.input_byte_limit))
