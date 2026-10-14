"""Independent complete DOM references, qualified by host text rather than models."""
from collections import deque

import pytest

from lladar.answer_extraction import ExtractionOptions
from lladar.browser_reference import DOMReferenceReader, ReferenceUnavailable


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        result = browser.new_page()
        result.set_content('<!doctype html><html><body><button>Send</button>'
            '<article aria-busy="false">Earlier answer</article></body></html>')
        try:
            yield result
        finally:
            browser.close()


def reader_for(answers, *, model_started=None):
    pending, requests = deque(answers), []
    class Provider:
        def __init__(self, **settings):
            self.settings = settings
        def extract(self, request, **settings):
            requests.append(request)
            return {"result": {"status": "final", "text": pending.popleft()}, "usage": None}
    options = ExtractionOptions(model_destination="fixture:model", transfer_approved=True,
        provider_factory=Provider, max_input_tokens=100000, max_output_tokens=10000)
    return DOMReferenceReader(options, model_started=model_started or (lambda: None)), requests


def finish_answer(reader, page, turn_id, text):
    baseline = reader.begin_turn(turn_id, page)
    page.locator('article').evaluate('(node, text) => {node.setAttribute("aria-busy","true");'
        'node.textContent=text;node.setAttribute("aria-busy","false");}', text)
    return reader.finish_turn(baseline, page)


@pytest.mark.browser
def test_three_trusted_full_answers_qualify_an_independent_dom_reference(page):
    answers = ["Answer one.", "Answer two.", "Answer three.", "Formal answer, complete to its last word."]
    debits = []
    reader, requests = reader_for(answers, model_started=lambda: debits.append("model"))
    for index, text in enumerate(answers[:3]):
        reference = finish_answer(reader, page, f"verification-{index}", text)
        assert reader.qualify(reference, text) is (index == 2)
    formal = finish_answer(reader, page, "formal", answers[-1])
    assert reader.reference(formal) == answers[-1]
    assert debits == ["model"] * 4
    assert len(requests) == 4
    assert all('candidate' not in item["body"] and 'materials' not in item["body"] for item in requests)


@pytest.mark.browser
def test_reference_remains_unavailable_before_three_distinct_host_qualifications(page):
    reader, requests = reader_for(["First answer.", "Second answer."])
    first = finish_answer(reader, page, "first", "First answer.")
    assert reader.qualify(first, "First answer.") is False
    with pytest.raises(ReferenceUnavailable, match="^reference_unavailable$"):
        reader.qualify(first, "First answer.")
    second = finish_answer(reader, page, "second", "Second answer.")
    assert reader.qualify(second, "Second answer.") is False
    formal = finish_answer(reader, page, "formal", "Not eligible yet.")
    with pytest.raises(ReferenceUnavailable):
        reader.reference(formal)
    assert len(requests) == 2


@pytest.mark.browser
def test_a_missing_last_paragraph_cannot_pass_the_qualified_whole_subtree(page):
    complete = "First paragraph.\n\nLast paragraph, retained in full."
    reader, requests = reader_for(["One.", "Two.", "Three.", "First paragraph."])
    for index, text in enumerate(["One.", "Two.", "Three."]):
        reference = finish_answer(reader, page, f"qualification-{index}", text)
        reader.qualify(reference, text)
    baseline = reader.begin_turn("formal", page)
    page.locator('article').evaluate('node => {node.setAttribute("aria-busy","true");'
        'node.innerHTML="<p>First paragraph.</p><p>Last paragraph, retained in full.</p>";'
        'node.setAttribute("aria-busy","false");}')
    formal = reader.finish_turn(baseline, page)
    with pytest.raises(ReferenceUnavailable):
        reader.reference(formal)
    assert complete in requests[-1]["body"].replace('\\n', '\n')


@pytest.mark.browser
def test_a_formal_turn_without_a_current_terminal_guard_falls_back_without_calling_a_model(page):
    debits = []
    reader, requests = reader_for(["One.", "Two.", "Three.", "Unconfirmed answer."],
        model_started=lambda: debits.append("model"))
    for index, text in enumerate(["One.", "Two.", "Three."]):
        reader.qualify(finish_answer(reader, page, f"qualification-{index}", text), text)
    baseline = reader.begin_turn("formal", page)
    page.locator('article').evaluate('(node) => {node.textContent="Unconfirmed answer.";}')
    formal = reader.finish_turn(baseline, page)
    with pytest.raises(ReferenceUnavailable):
        reader.reference(formal)
    assert len(debits) == len(requests) == 3


@pytest.mark.browser
def test_model_agreement_cannot_replace_the_trusted_full_text_qualification(page):
    reader, requests = reader_for(["Repeated model invention."] * 3)
    for index, text in enumerate(["Actual first.", "Actual second.", "Actual third."]):
        reference = finish_answer(reader, page, f"verification-{index}", text)
        with pytest.raises(ReferenceUnavailable):
            reader.qualify(reference, text)
    assert reader.qualified is False
    assert reader.qualification_count == 0
    assert len(requests) == 3


@pytest.mark.browser
def test_rendered_dom_cannot_qualify_markdown_that_is_not_present_verbatim(page):
    reader, requests = reader_for(["**Bold answer.**"])
    baseline = reader.begin_turn("verification", page)
    page.locator('article').evaluate('node => {node.setAttribute("aria-busy","true");'
        'node.innerHTML="<strong>Bold answer.</strong>";node.setAttribute("aria-busy","false");}')
    reference = reader.finish_turn(baseline, page)
    with pytest.raises(ReferenceUnavailable):
        reader.qualify(reference, "**Bold answer.**")
    assert requests == []


@pytest.mark.browser
def test_independent_capture_rejects_overflow_and_protected_material_before_a_model_call(page, monkeypatch):
    reader, requests = reader_for([])
    monkeypatch.setenv("LLADAR_DOM_NODE_LIMIT", "1")
    with pytest.raises(ReferenceUnavailable):
        reader.begin_turn("overflow", page)
    monkeypatch.delenv("LLADAR_DOM_NODE_LIMIT")
    reader.protected_values = ("synthetic-private-session",)
    page.locator('article').evaluate('node => node.textContent="synthetic-private-session"')
    with pytest.raises(ReferenceUnavailable):
        reader.begin_turn("protected", page)
    assert requests == []


@pytest.mark.browser
def test_independent_capture_lost_on_navigation_is_unavailable(page):
    reader, requests = reader_for([])
    baseline = reader.begin_turn("verification", page)
    page.goto("data:text/html,<article aria-busy='false'>Different document.</article>")
    with pytest.raises(ReferenceUnavailable):
        reader.finish_turn(baseline, page)
    assert requests == []


@pytest.mark.browser
def test_repeated_busy_transition_without_new_dom_answer_falls_back_without_model_calls(page):
    reader, requests = reader_for(["One.", "Two.", "Three.", "Three."])
    for index, text in enumerate(["One.", "Two.", "Three."]):
        reader.qualify(finish_answer(reader, page, f"verification-{index}", text), text)
    formal = finish_answer(reader, page, "formal", "Three.")
    with pytest.raises(ReferenceUnavailable):
        reader.reference(formal)
    assert len(requests) == 3


@pytest.mark.browser
def test_a_busy_turn_that_started_before_the_reference_boundary_cannot_qualify(page):
    reader, requests = reader_for(["Unrelated earlier turn."])
    page.locator('article').evaluate('node => node.setAttribute("aria-busy","true")')
    baseline = reader.begin_turn("verification", page)
    page.locator('article').evaluate('node => {node.textContent="Unrelated earlier turn.";'
        'node.setAttribute("aria-busy","false");}')
    reference = reader.finish_turn(baseline, page)
    with pytest.raises(ReferenceUnavailable):
        reader.qualify(reference, "Unrelated earlier turn.")
    assert requests == []


@pytest.mark.browser
def test_dom_that_gains_a_tail_during_model_reading_is_not_a_stable_full_reference(page):
    reader, _requests = reader_for(["One.", "Two.", "Three."])
    for index, text in enumerate(["One.", "Two.", "Three."]):
        reader.qualify(finish_answer(reader, page, f"verification-{index}", text), text)
    formal = finish_answer(reader, page, "formal", "Initially visible answer.")
    class ChangedProvider:
        def __init__(self, **settings):
            pass
        def extract(self, request, **settings):
            page.locator('article').evaluate('node => node.textContent+=" Newly visible last paragraph."')
            return {"result": {"status": "final", "text": "Initially visible answer."}, "usage": None}
    from dataclasses import replace
    reader.options = replace(reader.options, provider_factory=ChangedProvider)
    with pytest.raises(ReferenceUnavailable):
        reader.reference(formal)


@pytest.mark.browser
def test_abandon_turn_closes_observers_without_qualifying_or_reusing_its_id(page):
    debits = []
    reader, requests = reader_for(["One.", "Two.", "Three.", "Fresh repaired answer."],
        model_started=lambda: debits.append("model"))
    for index, text in enumerate(["One.", "Two.", "Three."]):
        reader.qualify(finish_answer(reader, page, f"qualification-{index}", text), text)
    baseline = reader.begin_turn("unsubmitted", page)
    page.evaluate("() => {globalThis.retainedReferenceStates=Object.keys(globalThis)"
        ".filter(key=>key.startsWith('lladar_reference_')).map(key=>globalThis[key]);}")
    assert reader.abandon_turn(baseline) is None
    assert reader.qualified and reader.qualification_count == 3
    assert len(requests) == len(debits) == 3
    assert page.evaluate("() => Object.keys(globalThis).filter(key=>key.startsWith('lladar_reference_'))") == []
    page.evaluate("async () => {document.querySelector('article').setAttribute('aria-busy','true');"
        "await new Promise(resolve=>setTimeout(resolve,0));}")
    assert page.evaluate("() => retainedReferenceStates.every(state=>state.records.length===0)")
    for operation in (lambda: reader.begin_turn("unsubmitted", page),
                      lambda: reader.finish_turn(baseline, page),
                      lambda: reader.abandon_turn(baseline)):
        with pytest.raises(ReferenceUnavailable, match="^reference_unavailable$"):
            operation()
    page.locator('article').evaluate("node => node.setAttribute('aria-busy','false')")
    repaired = finish_answer(reader, page, "unique-repaired-reference", "Fresh repaired answer.")
    assert reader.reference(repaired) == "Fresh repaired answer."
    assert reader.qualification_count == 3
    assert len(requests) == len(debits) == 4


@pytest.mark.browser
def test_abandon_turn_rejects_foreign_and_copied_baselines_before_closing_any_frame(page):
    from dataclasses import replace
    page.evaluate("() => {const frame=document.createElement('iframe');"
        "frame.srcdoc='<article aria-busy=\"false\">Child frame</article>';document.body.append(frame);}")
    page.locator('iframe').content_frame.locator('article').wait_for()
    reader, requests = reader_for([])
    foreign, foreign_requests = reader_for([])
    original = reader.begin_turn("owned", page)
    other = foreign.begin_turn("foreign", page)
    assert len(page.frames) == 2
    for owner, baseline in ((foreign, original), (reader, replace(original)),
                           (reader, replace(original, _keys=other._keys))):
        with pytest.raises(ReferenceUnavailable, match="^reference_unavailable$"):
            owner.abandon_turn(baseline)
        with pytest.raises(ReferenceUnavailable, match="^reference_unavailable$"):
            owner.finish_turn(baseline, page)
        assert all(frame.evaluate("() => Object.keys(globalThis).filter(key=>"
            "key.startsWith('lladar_reference_')).length") == 2 for frame in page.frames)
    reader.abandon_turn(original)
    assert all(frame.evaluate("() => Object.keys(globalThis).filter(key=>"
        "key.startsWith('lladar_reference_')).length") == 1 for frame in page.frames)
    foreign.abandon_turn(other)
    assert all(frame.evaluate("() => Object.keys(globalThis).filter(key=>"
        "key.startsWith('lladar_reference_')).length") == 0 for frame in page.frames)
    assert requests == foreign_requests == []
    assert reader.qualification_count == foreign.qualification_count == 0
