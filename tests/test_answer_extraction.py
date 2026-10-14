import json
from dataclasses import replace
import pytest

from lladar.answer_extraction import ExtractionError, ExtractionOptions, ResponseExtractor, MAX_INPUT_BYTES
from lladar.response_capture import ObservedResponse, ResponseObservationSource


@pytest.mark.parametrize("model,override,expected", [
    ("gemini:gemini-3.8-flash", None, 65536),
    ("google:gemini-3.8-flash", None, 65536),
    ("gemini:fixture-model", None, 65536),
    ("gemini:gemini-3.8-flash", 32768, 32768),
    ("gemini:fixture-model", 131072, 131072),
])
def test_extraction_model_profile_or_override_reaches_provider_and_evidence(model, override, expected):
    settings = []

    class Provider:
        def extract(self, request, *, timeout_seconds):
            return {"result": {"status": "final", "text": "complete fixture answer"}, "usage": None}

    def factory(**kwargs):
        settings.append(kwargs)
        return Provider()

    extractor = ResponseExtractor(ExtractionOptions(model_destination=model, max_output_tokens=override,
                                  provider_factory=factory, transfer_approved=True), max_calls=1)
    observation = ResponseObservationSource().begin_request("budget-test").observe("text/plain", "fixture answer")
    extractor.extract(observation, request_id="budget-test")
    assert settings[0]["max_output_tokens"] == expected
    assert extractor.evidence["max_output_tokens"] == expected


def test_extraction_stage_is_selected_by_host_role_rather_than_call_index():
    class Provider:
        def extract(self, request, *, timeout_seconds):
            return {"result": {"status": "final", "text": "complete source answer"}, "usage": None}
    extractor = ResponseExtractor(ExtractionOptions(provider_factory=lambda **kwargs: Provider(),
        transfer_approved=True), max_calls=1)
    observed = ResponseObservationSource().begin_request("formal-1").observe("text/plain", "complete source answer")
    extractor.extract(observed, request_id="formal-1", stage="formal")
    assert extractor.evidence["events"][0]["stage"] == "formal"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_extraction_output_budget_stops_before_provider_initialization(limit):
    with pytest.raises(ExtractionError, match="invalid_provider_budget"):
        ResponseExtractor(ExtractionOptions(max_output_tokens=limit,
                          provider_factory=lambda **_: pytest.fail("provider must not initialize")), max_calls=1)


def test_single_character_cookie_flag_does_not_block_numbered_answer():
    from lladar.answer_extraction import response_input
    observation = ResponseObservationSource().begin_request("r").observe(
        "text/plain", "1. Open settings.\n2. Save your changes.",
    )
    assert response_input(observation, ("1",))["body"] == observation.body


@pytest.mark.parametrize("body,protected", [
    ("1", ("1",)),
    ("token: 1", ("1",)),
    (r'{"box":"{\"password\":\"1\"}"}', ("1",)),
    ("A reply containing private-session-123", ("private-session-123",)),
    ("Bearer private-value", ("1",)),
])
def test_cookie_flag_fix_preserves_credential_rejection(body, protected):
    from lladar.answer_extraction import response_input
    observation = ResponseObservationSource().begin_request("r").observe("text/plain", body)
    with pytest.raises(ExtractionError, match="sensitive_response"):
        response_input(observation, protected)


def test_one_response_one_model_call_preserves_literal_answer_and_private_receipt():
    calls = []
    progress = []

    class Provider:
        def extract(self, request, *, timeout_seconds):
            calls.append(request)
            return {"result": {"status": "final", "text": "  不知道。\n**不是 3 台**  "}, "usage": None}

    extractor = ResponseExtractor(
        ExtractionOptions(provider_factory=lambda **_: Provider(), transfer_approved=True), max_calls=3,
        progress_callback=progress.append,
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
    assert progress == ['checking_input', 'model_running', 'validating_result', 'final']


def test_newly_discovered_private_values_are_added_without_resetting_extractor_budget():
    from lladar.browser_materials import private_screen_values
    secret='fresh-protocol-session-832a'
    encoded=__import__('base64').b64encode(secret.encode()).decode()
    class Provider:
        def extract(self, request, *, timeout_seconds):
            return {'result':{'status':'final','text':encoded},'usage':None}
    extractor=ResponseExtractor(ExtractionOptions(provider_factory=lambda **_:Provider(),
        transfer_approved=True),max_calls=2)
    before_deadline=extractor._deadline
    extractor.update_protected_values(private_screen_values((secret,)))
    observation=ResponseObservationSource().begin_request('fresh-private').observe(
        'application/json','{"content":"Actual complete answer"}')
    with pytest.raises(ExtractionError,match='sensitive_response'):
        extractor.extract(observation,request_id='fresh-private')
    assert extractor.evidence['calls']==1 and extractor._deadline==before_deadline


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
            '{"access_token":"private-token"}' if case == "secret" else "x" * (MAX_INPUT_BYTES if case == "oversize" else 1),
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


def test_recovery_extractor_cannot_extend_shared_deadline(monkeypatch):
    import time
    now=[0.0]
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    first=ResponseExtractor(ExtractionOptions(),max_calls=2)
    now[0]=3601
    recovery=ResponseExtractor(ExtractionOptions(),max_calls=1,deadline=first._deadline)
    with pytest.raises(ExtractionError,match='extraction_time_exhausted'):
        recovery.check_budget()
