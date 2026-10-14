"""Agent reads bounded pages without losing the immutable host snapshot."""
import json

import pytest

from lladar.browser_inspection import BrowserInspectionError, BrowserInspectionPager


def snapshot(nodes=None,text="A normal page"):
    return {"kind":"browser_observation","active_page_id":"page-1","active_page_index":0,
      "frames":[{"frame_id":"frame-1","page_id":"page-1","page_index":0,"url":"https://example.test/chat",
                  "text":text,"nodes":nodes or [{"ref":"node-1","tag":"textarea","editable":True,"name":"Your question","attributes":{},"text":""}]}],
      "connections":[],"client_resources":[],"event_count":7,"capture_over_limit":False}


def test_small_page_inventory_includes_complete_operable_initial_page_and_pins_copy():
    original=snapshot()
    pager=BrowserInspectionPager(4096)
    inventory=pager.capture(original)
    assert len(json.dumps(inventory,ensure_ascii=False).encode())<=4096
    assert inventory["active_page_id"]=="page-1"
    first=inventory["frames"][0]
    assert first["nodes"][0]["ref"]=="node-1"
    assert first["nodes"][0]["editable"] is True
    assert first["text"]=="A normal page" and first["text_complete"]
    assert first["next_node_offset"] is None and first["next_text_offset"] is None
    original["frames"][0]["nodes"][0]["name"]="Mutated after capture"
    original["frames"][0]["text"]="Another instant"
    read=pager.read_dom(inventory["snapshot_id"],"frame-1")
    assert read["nodes"][0]["name"]=="Your question"
    assert read["text"]=="A normal page"
    read["nodes"][0]["name"]="Caller mutation"
    assert pager.read_dom(inventory["snapshot_id"],"frame-1")["nodes"][0]["name"]=="Your question"


def test_huge_unicode_page_text_is_complete_only_after_explicit_ordered_pages():
    text="中文🙂\\\"\n"*10000+"Final paragraph."
    pager=BrowserInspectionPager(1536)
    inventory=pager.capture(snapshot(text=text))
    offset=0
    fragments=[]
    while True:
        page=pager.read_dom(inventory["snapshot_id"],"frame-1",node_offset=1,node_limit=0,text_offset=offset)
        assert len(json.dumps(page,ensure_ascii=False).encode())<=1536
        assert page["text_offset"]==offset and page["total_text_chars"]==len(text)
        fragments.append(page["text"])
        if page["next_text_offset"] is None:
            assert page["text_complete"] is False  # this last slice is still only a slice
            break
        assert page["next_text_offset"]>offset
        offset=page["next_text_offset"]
    assert "".join(fragments)==text


def test_huge_node_attributes_remain_retrievable_in_whole_under_small_byte_budget():
    node={"ref":"operable-node","tag":"textarea","editable":True,"name":"A normal control",
          "text":"Large node text. "*3000,"attributes":{f"data-{i}":"中文🙂"*30 for i in range(180)},
          "options":[{"value":i,"label":"Selection "+str(i)} for i in range(100)]}
    pager=BrowserInspectionPager(2048)
    inventory=pager.capture(snapshot(nodes=[node]))
    page=pager.read_dom(inventory["snapshot_id"],"frame-1",node_limit=1,text_limit=0)
    shape=page["nodes"][0]
    assert shape["ref"]=="operable-node" and shape["editable"]
    assert not shape["node_complete"] and page["next_node_offset"] is None
    reference=shape["details"]["field_ref"]
    offset=0
    fragments=[]
    while True:
        part=pager.read_dom(inventory["snapshot_id"],"frame-1",field_ref=reference,text_offset=offset)
        assert len(json.dumps(part,ensure_ascii=False).encode())<=2048
        assert part["encoding"]=="json" and not part["complete"]
        fragments.append(part["text"])
        if part["next_text_offset"] is None:
            break
        assert part["next_text_offset"]>offset
        offset=part["next_text_offset"]
    assert json.loads("".join(fragments))==node


def test_node_cursor_reaches_footer_controls_without_silently_omitting_nodes():
    nodes=[{"ref":f"node-{i}","tag":"p","editable":False,"text":"Paragraph "+str(i)} for i in range(220)]
    nodes.append({"ref":"footer-input","tag":"textarea","editable":True,"name":"Ask here","text":""})
    pager=BrowserInspectionPager(1800)
    inventory=pager.capture(snapshot(nodes=nodes))
    offset=0
    seen=[]
    while True:
        page=pager.read_dom(inventory["snapshot_id"],"frame-1",node_offset=offset,text_limit=0)
        assert len(json.dumps(page,ensure_ascii=False).encode())<=1800
        seen.extend(n["ref"] for n in page["nodes"])
        if page["next_node_offset"] is None:
            assert page["nodes"][-1]["editable"]
            break
        assert page["next_node_offset"]>offset
        offset=page["next_node_offset"]
    assert seen==[n["ref"] for n in nodes]


def test_new_capture_invalidates_old_snapshot_and_field_tokens():
    pager=BrowserInspectionPager(2048)
    old=pager.capture(snapshot(text="Old instant"))
    current=pager.capture(snapshot(text="Current instant"))
    assert old["snapshot_id"]!=current["snapshot_id"]
    with pytest.raises(BrowserInspectionError,match="snapshot_stale"):
        pager.read_dom(old["snapshot_id"],"frame-1")
    assert pager.read_dom(current["snapshot_id"],"frame-1")["text"]=="Current instant"


def test_many_frames_inventory_is_paged_without_losing_unlisted_frame_ids():
    full=snapshot()
    full["frames"]=[{**full["frames"][0],"frame_id":f"frame-{i}","url":"https://example.test/"+str(i),
                     "nodes":[{"ref":f"control-{i}","editable":True,"text":""}]} for i in range(70)]
    pager=BrowserInspectionPager(1500)
    inventory=pager.capture(full)
    assert len(json.dumps(inventory,ensure_ascii=False).encode())<=1500
    assert inventory["total_frames"]==70
    assert inventory["next_frame_offset"]==len(inventory["frames"])<70
    ref=inventory["frame_inventory"]["field_ref"]
    offset=0
    pieces=[]
    while True:
        page=pager.read_dom(inventory["snapshot_id"],"",field_ref=ref,text_offset=offset)
        pieces.append(page["text"])
        if page["next_text_offset"] is None:
            break
        offset=page["next_text_offset"]
    summaries=json.loads("".join(pieces))
    assert [f["frame_id"] for f in summaries]==[f"frame-{i}" for i in range(70)]
    assert pager.read_dom(inventory["snapshot_id"],"frame-69")["nodes"][0]["ref"]=="control-69"


def test_large_node_and_text_do_not_prevent_forward_progress_in_minimal_page_shape():
    pager=BrowserInspectionPager(900)
    original=snapshot(nodes=[{"ref":"large","editable":True,"tag":"textarea","attributes":{"aria-label":"x"*20000},"text":"x"*20000}],text="y"*20000)
    inventory=pager.capture(original)
    page=pager.read_dom(inventory["snapshot_id"],"frame-1",node_limit=1,text_limit=0)
    assert len(json.dumps(page,ensure_ascii=False).encode())<=900
    assert page["nodes"][0]["ref"]=="large"
    assert page["next_node_offset"] is None
    assert page["nodes"][0]["details"]["field_ref"]
    text=pager.read_dom(inventory["snapshot_id"],"frame-1",node_offset=1,node_limit=0,text_limit=20000)
    assert text["next_text_offset"]>0


@pytest.mark.parametrize("arguments",[
    {"node_offset":-1},{"node_offset":True},{"node_limit":-1},{"text_offset":-1},
    {"text_limit":-1},{"node_offset":2},{"text_offset":999},{"node_limit":0,"text_limit":0},
    {"field_ref":{}},{"field_ref":"unobserved-field"}])
def test_invalid_and_unobserved_cursors_fail_without_returning_partial_source(arguments):
    pager=BrowserInspectionPager(2048)
    inventory=pager.capture(snapshot())
    with pytest.raises(BrowserInspectionError):
        pager.read_dom(inventory["snapshot_id"],"frame-1",**arguments)


def test_failed_capture_invalidates_older_snapshot_instead_of_silently_reusing_it():
    pager=BrowserInspectionPager(2048)
    first=pager.capture(snapshot())
    with pytest.raises(BrowserInspectionError,match="snapshot_invalid"):
        pager.capture({"frames":"an invalid capture"})
    with pytest.raises(BrowserInspectionError,match="snapshot_stale"):
        pager.read_dom(first["snapshot_id"],"frame-1")


def test_budget_too_small_to_describe_source_fails_explicitly_without_a_stuck_cursor():
    pager=BrowserInspectionPager(16)
    with pytest.raises(BrowserInspectionError,match="limit_too_small"):
        pager.capture(snapshot())


def test_configured_default_preview_size_is_used_without_becoming_a_hard_read_ceiling(monkeypatch):
    monkeypatch.setenv("LLADAR_PROJECT_PREVIEW_CHARS","8")
    pager=BrowserInspectionPager(4096)
    inventory=pager.capture(snapshot(text="First block and the final block."))
    initial=inventory["frames"][0]
    assert initial["text"]=="First bl" and initial["next_text_offset"]==8
    whole=pager.read_dom(inventory["snapshot_id"],"frame-1",text_limit=1024)
    assert whole["text"]=="First block and the final block." and whole["text_complete"]


def test_non_json_or_unencodable_capture_is_classified_before_exposing_a_token():
    pager=BrowserInspectionPager(4096)
    invalid=snapshot()
    invalid["frames"][0]["text"]="unpaired \ud800"
    with pytest.raises(BrowserInspectionError,match="snapshot_invalid"):
        pager.capture(invalid)
    invalid=snapshot()
    invalid["frames"][0]["nodes"][0]["value"]=float("nan")
    with pytest.raises(BrowserInspectionError,match="snapshot_invalid"):
        pager.capture(invalid)
