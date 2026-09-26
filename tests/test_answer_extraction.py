import json
from dataclasses import replace
import pytest

from lladar.answer_extraction import ExtractionError, ExtractionOptions, ResponseExtractor
from lladar.response_capture import ObservedResponse, ResponseObservationSource


def test_one_response_one_model_call_preserves_literal_answer_and_private_receipt():
    calls = []

    class Provider:
        def extract(self, request, *, timeout_seconds):
            calls.append(request)
            return {"result": {"status": "final", "text": "  不知道。\n**不是 3 台**  "}, "usage": None}

    extractor = ResponseExtractor(
        ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=3,
    )
    observation = ResponseObservationSource().begin_request("private-request-id").observe(
        "application/json", '{"unfamiliar":{"reply":"  不知道。\\n**不是 3 台**  "}}',
    )
    assert extractor.extract(observation, request_id="private-request-id") == "  不知道。\n**不是 3 台**  "
    assert len(calls) == 1
    assert calls[0] == {"content_type": "application/json", "body": observation.body}
    assert "private-request-id" not in json.dumps(calls)
    assert extractor.evidence["calls"] == 1
    assert "不知道" not in json.dumps(extractor.evidence, ensure_ascii=False)


@pytest.mark.parametrize("case,reason", [
    ("consent", "response_transfer_not_approved"),
    ("receipt", "request_identity_mismatch"),
    ("tampered", "request_identity_mismatch"),
    ("incomplete", "completion_unconfirmed"),
    ("binary", "unsupported_response_protocol"),
    ("oversize", "provider_input_limit"),
    ("secret", "sensitive_response"),
])
def test_invalid_or_unapproved_evidence_never_initializes_provider(case, reason):
    def factory(**_):
        pytest.fail("provider must not initialize")

    response = ResponseObservationSource().begin_request("r").observe("application/json", '{"answer":"hello"}')
    if case == "receipt":
        response = ObservedResponse("text/plain", "hello")
    if case == "tampered":
        response = replace(response, body="tampered")
    if case in {"incomplete", "binary", "oversize", "secret"}:
        response = ResponseObservationSource().begin_request("r").observe(
            "application/octet-stream" if case == "binary" else "text/plain",
            '{"access_token":"private-token"}' if case == "secret" else "x" * (130000 if case == "oversize" else 1),
            stream_closed=case != "incomplete",
        )
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=factory, transfer_approved=case != "consent"), max_calls=3)
    with pytest.raises(ExtractionError) as caught:
        extractor.extract(response, request_id="r")
    assert caught.value.reason == reason
    assert extractor.evidence["calls"] == 0


@pytest.mark.parametrize("result,reason", [
    ({"status": "incomplete"}, "model_incomplete"),
    ({"status": "ambiguous"}, "model_ambiguous"),
    ({"status": "unsupported"}, "model_unsupported"),
    ({"status": "invalid"}, "model_invalid"),
    ({"status": "final", "text": ""}, "provider_response_invalid"),
    ({"source": "import os; os.system('unsafe')"}, "provider_response_invalid"),
    ({"status": "final", "text": "OK", "tools": []}, "provider_response_invalid"),
])
def test_model_failure_is_safe_consumes_one_call_and_blocks_later_dispatch(result, reason):
    class Provider:
        def extract(self, request, *, timeout_seconds):
            return {"result": result, "usage": None}

    source = ResponseObservationSource()
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=3)
    with pytest.raises(ExtractionError) as caught:
        extractor.extract(source.begin_request("first").observe("text/plain", "secret answer"), request_id="first")
    assert caught.value.reason == reason
    with pytest.raises(ExtractionError):
        extractor.extract(source.begin_request("second").observe("text/plain", "other"), request_id="second")
    assert extractor.evidence["calls"] == 1
    assert "secret answer" not in json.dumps(extractor.evidence)


@pytest.mark.parametrize("kind", ["old_receipt", "prestarted", "foreign_source", "duplicate_id"])
def test_verification_requires_a_new_request_after_calibration_extraction(kind):
    class Provider:
        def extract(self, request, **_):
            return {"result":{"status":"final","text":"Same answer"},"usage":None}
    source = ResponseObservationSource()
    first = source.begin_request("first").observe("text/plain", "Same answer")
    pending = source.begin_request("second")
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=3)
    assert extractor.extract(first, request_id="first") == "Same answer"
    request_id = "second"
    if kind == "old_receipt":
        observation = first
    elif kind == "prestarted":
        observation = pending.observe("text/plain", "Same answer")
    elif kind == "foreign_source":
        observation = ResponseObservationSource().begin_request("second").observe("text/plain", "Same answer")
    else:
        request_id = "first"
        observation = source.begin_request(request_id).observe("text/plain", "Same answer")
    with pytest.raises(ExtractionError):
        extractor.extract(observation, request_id=request_id)
    assert extractor.evidence["calls"] == 1


def test_distinct_requests_can_return_identical_answers_but_never_exceed_approved_calls():
    class Provider:
        def extract(self, request, **_):
            return {"result":{"status":"final","text":"Same answer"},"usage":None}
    source = ResponseObservationSource()
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=2)
    for name in ("calibration", "verification"):
        assert extractor.extract(source.begin_request(name).observe("text/plain","Same answer"), request_id=name) == "Same answer"
    with pytest.raises(ExtractionError, match="extraction_budget_exhausted"):
        extractor.check_budget()
    assert extractor.evidence["calls"] == 2


@pytest.mark.parametrize("body", [r'{"\u0061ccess_token":"hidden"}', r'{"box":"{\"access_token\":\"hidden\"}"}'])
def test_encoded_credential_keys_do_not_bypass_transfer_preflight(body):
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **_: pytest.fail("must not initialize"),
                                                    transfer_approved=True), max_calls=3)
    with pytest.raises(ExtractionError, match="sensitive_response"):
        extractor.extract(ResponseObservationSource().begin_request("r").observe("application/json", body), request_id="r")


def test_shared_deadline_is_checked_before_and_after_model_call(monkeypatch):
    import time
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    class Provider:
        def extract(self, request, *, timeout_seconds):
            assert timeout_seconds == 3600
            now[0] = 3601
            return {"result":{"status":"final","text":"Late answer"},"usage":None}
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=3)
    with pytest.raises(ExtractionError, match="extraction_time_exhausted"):
        extractor.extract(ResponseObservationSource().begin_request("r").observe("text/plain","Late answer"), request_id="r")
    assert extractor.evidence["calls"] == 1
