"""Host-enforced discovery accounting through BrowserTarget's public seam."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lladar.answer_extraction import ExtractionOptions
from lladar.browser_target import BrowserDiscoveryError, BrowserTarget


SOURCE_ANSWER = "The independently observed complete source answer."
PROGRAM = '''
def adapt(payload):
    if payload["phase"] == "send":
        return {"actions": [{"kind": "http", "url": "https://example.test/ask", "method": "POST", "body": payload["question"], "submit": SUBMIT_FLAG}], "context": {}}
    if payload["phase"] == "observe":
        events = payload["observation"]["events"]
        requests = [e for e in events if e["kind"] == "http_request"]
        responses = [e for e in events if e["kind"] == "fetch_chunk"]
        ends = [e for e in events if e["kind"] == "fetch_end"]
        if requests and responses and ends:
            return {"complete": True, "context": {}, "evidence": {"submission_events": [requests[0]["event_id"]], "completion_events": [ends[0]["event_id"]]}, "materials": [{"event_ids": [responses[0]["event_id"]], "data": responses[0]["text"]}]}
    return {"actions": [], "context": {}}
'''


class Website:
    def __init__(self, workspace):
        self.workspace = workspace
        self.active = None
        self.turns = {}
        self.events_seen = []
        self.submission_intents = []

    def open(self):
        pass

    def inspect(self):
        return {"frames": [], "client_resources": [], "active_page_id":"fake-page"}

    def begin_turn(self, turn_id):
        self.active = turn_id
        self.turns[turn_id] = []
        return {"turn_id": turn_id, "sequence": len(self.events_seen), "page": self.inspect()}

    def http_request(self, spec, *, submit=False):
        ledger = json.loads((self.workspace / "ledger.json").read_text(encoding="utf-8"))
        self.submission_intents.append(ledger["turns"].get(self.active, {}).get("status"))
        receipt=[("submission_attempted", {"method":"http","observation_source":"browser_host"})] if submit else []
        for kind, extra in receipt+[
            ("http_request", {"body": spec["body"],"url":spec['url'],'method':'POST', "observation_source": "browser_network"}),
            ("fetch_start", {"url":spec['url'],'method':'POST'}),
            ("fetch_chunk", {"text": SOURCE_ANSWER}),
            ("fetch_end", {}),
        ]:
            event = {"event_id": f"event-{len(self.events_seen) + 1}",
                     "sequence": len(self.events_seen) + 1, "kind": kind,
                     "turn_id": self.active, "request_id": self.active, "page_id":"fake-page", **extra}
            self.events_seen.append(event)
            self.turns[self.active].append(event)
        return {"started": True, "submitted": submit}

    def observe_turn(self, turn_id):
        return {"turn_id": turn_id, "submitted": True, "events": self.turns[turn_id], "page": self.inspect()}

    def raw_event(self, event_id):
        return next(event for event in self.events_seen if event["event_id"] == event_id)

    def events(self, **kwargs):
        return self.events_seen

    def close(self):
        pass


def target_for(workspace, source, provider_calls, website_factory=Website):
    website = website_factory(workspace)
    def factory(**settings):
        def agent(_prompt):
            settings["tools"]["submit_adapter"](source, "Observed website protocol")
            settings["tools"]["test_adapter"]()
        return agent
    def provider_factory(**settings):
        def extract(request, **kwargs):
            provider_calls.append(request)
            material = json.loads(request["body"])
            return {"result": {"status": "final", "text": material[0]["data"]}, "usage": None}
        return SimpleNamespace(extract=extract)
    target = BrowserTarget(page_url="https://example.test/chat", driver=website,
        workspace=workspace, agent_factory=factory, confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True, provider_factory=provider_factory),
        reference_reader=lambda turn: SOURCE_ANSWER, output_fn=lambda text: None)
    return target, website


def test_question_send_cannot_omit_durable_submission_intent_by_candidate_flag(tmp_path):
    target, website = target_for(tmp_path, PROGRAM.replace("SUBMIT_FLAG", "False"), [])
    try:
        target.prepare([], interactive=False, request_count=1)
    except BrowserDiscoveryError:
        # Rejecting an unmarked submission before dispatch is also valid.
        assert website.submission_intents == []
    else:
        assert website.submission_intents
        assert all(status == "attempted_unknown" for status in website.submission_intents)
    finally:
        target.close()


def test_extraction_and_exploration_share_the_disclosed_model_call_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("LLADAR_BROWSER_DISCOVERY_MODEL_CALLS", "1")
    provider_calls = []
    target, _website = target_for(tmp_path, PROGRAM.replace("SUBMIT_FLAG", "True"), provider_calls)
    try:
        with pytest.raises(BrowserDiscoveryError):
            target.prepare([], interactive=False, request_count=1)
        # The discovery Agent consumed the only approved model call. No actual
        # extraction request may follow, irrespective of separate counters.
        assert provider_calls == []
    finally:
        target.close()


def test_provider_initialization_and_agent_output_cannot_leak_private_material(tmp_path, capsys):
    secret = "synthetic-provider-private-material"
    def factory(**settings):
        print(secret)
        def agent(_prompt):
            print(secret)
            raise RuntimeError(secret)
        return agent
    target = BrowserTarget(page_url="https://example.test/chat", driver=Website(tmp_path),
        workspace=tmp_path, agent_factory=factory, confirmed=True,
        extraction_options=ExtractionOptions(transfer_approved=True,
                                             provider_factory=lambda **kwargs: None),
        reference_reader=lambda turn: SOURCE_ANSWER, output_fn=lambda text: None)
    try:
        with pytest.raises(BrowserDiscoveryError) as caught:
            target.prepare([], interactive=False, request_count=1)
        assert secret not in str(caught.value)
        captured = capsys.readouterr()
        assert secret not in captured.out + captured.err
    finally:
        target.close()


def test_new_background_response_cannot_be_claimed_as_the_formal_question_answer(tmp_path):
    class BackgroundWebsite(Website):
        def http_request(self, spec, *, submit=False):
            result = super().http_request(spec, submit=submit)
            for kind, extra in [
                ("fetch_chunk", {"text": "Unrelated background response."}),
                ("fetch_end", {}),
            ]:
                event = {"event_id": f"event-{len(self.events_seen) + 1}",
                         "sequence": len(self.events_seen) + 1, "kind": kind,
                         "turn_id": self.active, "request_id": "unrelated-background", **extra}
                self.events_seen.append(event)
                self.turns[self.active].append(event)
            return result
    source = PROGRAM.replace("SUBMIT_FLAG", "True").replace(
        '        if requests and responses and ends:',
        '        if payload["role"] == "formal":\n'
        '            responses = [e for e in responses if e["request_id"] == "unrelated-background"]\n'
        '            ends = [e for e in ends if e["request_id"] == "unrelated-background"]\n'
        '        if requests and responses and ends:',
    )
    target, _website = target_for(tmp_path, source, [], BackgroundWebsite)
    try:
        target.prepare([], interactive=False, request_count=1)
        try:
            answer = target.answer("A new formal question", "formal-1")
        except BrowserDiscoveryError:
            pass
        else:
            assert answer == SOURCE_ANSWER
    finally:
        target.close()
