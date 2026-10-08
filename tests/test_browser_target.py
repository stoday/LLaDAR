"""Request-template and local profile boundaries shared across extraction modes."""
from pathlib import Path
import json
import re
from urllib.parse import parse_qs, urlsplit
import pytest

from lladar.browser_target import BrowserTarget, CapturedInteraction, RequestTemplate
from lladar.response_capture import ObservedResponse, ResponseObservationSource
from fixture_extraction import fixture_options, FixtureTerminal
from lladar.command_log import command_log

class FixtureBrowserDriver:
    def __init__(self):
        self.source = ResponseObservationSource()
    def capture_calibration(self, marker, prompt):
        prompt(marker)
        return CapturedInteraction("POST", "https://example.test/api/ask", {"content-type":"application/json"},
                                   json.dumps({"question":marker}),
                                   self.source.begin_request(marker).observe("application/json", '{"answer":"Calibration"}'))
    def replay(self, template, question, request_id, *, timeout_seconds=None):
        return self.source.begin_request(request_id).observe("application/json", '{"answer":"Verification"}')
    def close(self):
        pass


@pytest.mark.parametrize("question", [None, '  客服幾點開？\n包含 "週末" 嗎？  '])
def test_custom_calibration_question_is_reused_with_new_verification_response(question):
    captured, replayed = [], []

    class Driver(FixtureBrowserDriver):
        def capture_calibration(self, marker, prompt):
            captured.append(marker)
            return super().capture_calibration(marker, prompt)

        def replay(self, template, text, request_id, **kwargs):
            replayed.append((text, request_id))
            assert json.loads(template.render_body(text))["question"] == text
            return super().replay(template, text, request_id, **kwargs)

    target = BrowserTarget(
        page_url="https://example.test/chat", timeout=5, driver=Driver(), verbose=False,
        calibration_question=question, confirmed=True, input_fn=lambda _prompt: "MATCH",
        output_fn=lambda _message: None, review_stream=FixtureTerminal(),
        extraction_options=fixture_options(),
    )
    try:
        target.prepare([], interactive=True, record_count=1, request_count=1)
        assert len(replayed) == 1 and replayed[0][1] == "browser-verification"
        if question is None:
            assert re.fullmatch(r"LLaDAR calibration [0-9a-f]{24}", captured[0])
            assert re.fullmatch(r"LLaDAR verification [0-9a-f]{24}", replayed[0][0])
        else:
            assert captured == [question] and replayed[0][0] == question
        assert target.answer("Dataset question", "dataset-1") == "Verification"
        assert replayed[1] == ("Dataset question", "dataset-1")
        assert target._extractor.evidence["calls"] == 3
        assert target.evidence["verification"] == "independent_local_reference"
    finally:
        target.close()


@pytest.mark.parametrize("question", ["", " \t\n", 123])
def test_invalid_calibration_question_is_rejected_before_browser_opens(question):
    def forbidden_driver(**kwargs):
        pytest.fail("Invalid question must not open a browser")

    with pytest.raises(ValueError, match="non-empty question"):
        BrowserTarget(page_url="https://example.test/chat", timeout=5,
                      calibration_question=question, driver_factory=forbidden_driver)


@pytest.mark.parametrize("payload,expected", [
    ({"input": {"question": '客服 "週末"\n幾點開？'}}, 1),
    ({"history": ['客服 "週末"\n幾點開？'], "question": '客服 "週末"\n幾點開？'}, 2),
    ({'客服 "週末"\n幾點開？': "value"}, 0),
])
def test_encoded_calibration_question_counts_only_replaceable_json_values(payload, expected):
    from types import SimpleNamespace
    from lladar.playwright_driver import PlaywrightBrowserDriver

    request = SimpleNamespace(url="https://example.test/ask", post_data=json.dumps(payload),
                              headers={"content-type": "application/json"})
    assert PlaywrightBrowserDriver._marker_count(request, '客服 "週末"\n幾點開？') == expected


@pytest.mark.parametrize("verbose", [True, False])
def test_browser_stages_are_logged_before_blocking_work(tmp_path, verbose):
    from fixture_extraction import FixtureProvider
    from lladar.answer_extraction import ExtractionOptions

    log = tmp_path / "progress.log"
    calls = []

    def expect(message):
        assert (message in log.read_text(encoding="utf-8")) == verbose

    class Driver(FixtureBrowserDriver):
        def capture_calibration(self, marker, prompt):
            expect("Capturing calibration request and website response started")
            return super().capture_calibration(marker, prompt)

        def replay(self, template, question, request_id, **kwargs):
            stage = "verification" if request_id == "browser-verification" else "dataset"
            expect(f"Waiting for {stage} website response started")
            calls.append(request_id)
            return super().replay(template, question, request_id, **kwargs)

    class Provider(FixtureProvider):
        def extract(self, request, **kwargs):
            stage = ("calibration", "verification", "dataset")[len(self.calls)]
            expect(f"Extracting {stage} answer with model started")
            return super().extract(request, **kwargs)

    with command_log(log):
        target = BrowserTarget(
            page_url="https://example.test/chat?token=private-path",
            timeout=5, verbose=verbose, driver=Driver(), confirmed=True,
            input_fn=lambda _prompt: "MATCH", output_fn=lambda _message: None,
            review_stream=FixtureTerminal(),
            extraction_options=ExtractionOptions(provider_factory=Provider, transfer_approved=True),
        )
        try:
            target.prepare([], interactive=True, record_count=1, request_count=1)
            assert target.answer("Question", "dataset-1") == "Verification"
        finally:
            target.close()
    saved = log.read_text(encoding="utf-8")
    assert ("Website response received bytes=" in saved) == verbose
    assert ("Verification passed; ready for dataset requests" in saved) == verbose
    assert calls == ["browser-verification", "dataset-1"]
    assert "private-path" not in saved
    assert '"answer"' not in saved

def test_reusable_site_profile_is_reported_as_new_then_reused(tmp_path: Path):
    runs_root = tmp_path / ".lladar" / "runs"

    def driver_factory(**options):
        profile_dir = Path(options["profile_dir"])
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "fixture-session").write_text("signed-in", encoding="utf-8")
        return FixtureBrowserDriver()

    first = BrowserTarget(
        page_url="https://example.test/chat",
        timeout=5,
        runs_root=runs_root,
        verbose=False,
        driver_factory=driver_factory,
        output_fn=lambda _message: None,
    )
    first.close()
    messages: list[str] = []
    prompts = iter(["", "YES", "MATCH"])
    second = BrowserTarget(
        page_url="https://example.test/chat",
        timeout=5,
        runs_root=runs_root,
        verbose=False,
        driver_factory=driver_factory,
        input_fn=lambda _message: next(prompts),
        output_fn=messages.append,
        extraction_options=fixture_options(),
        review_stream=FixtureTerminal(),
    )

    second.prepare(["Dataset question"], interactive=True, request_count=1)
    second.close()

    assert first.evidence["browser_profile"] == "new"
    assert second.evidence["browser_profile"] == "reused"
    assert any("reused browser profile" in message for message in messages)


def test_fresh_browser_profile_is_temporary_and_removed_on_close(tmp_path: Path):
    created_profiles: list[Path] = []

    def driver_factory(**options):
        profile_dir = Path(options["profile_dir"])
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "fixture-session").write_text("signed-in", encoding="utf-8")
        created_profiles.append(profile_dir)
        return FixtureBrowserDriver()

    target = BrowserTarget(
        page_url="https://example.test/chat",
        timeout=5,
        runs_root=tmp_path / ".lladar" / "runs",
        verbose=False,
        fresh_profile=True,
        driver_factory=driver_factory,
        output_fn=lambda _message: None,
    )

    assert target.evidence["browser_profile"] == "fresh"
    assert created_profiles[0].is_dir()
    target.close()
    assert not created_profiles[0].exists()


def test_json_request_template_preserves_arbitrary_question_text():
    marker = "LLaDAR calibration marker"
    template = RequestTemplate.from_interaction(
        CapturedInteraction(
            method="POST",
            url="https://example.test/ask",
            headers={"content-type": "application/json"},
            request_body=json.dumps({"input": {"text": marker}}),
            response=ObservedResponse("application/json", '{"answer":"ok"}'),
        ),
        marker,
    )

    rendered = json.loads(template.render_body('Line one\nHe said "hello".'))

    assert rendered["input"]["text"] == 'Line one\nHe said "hello".'


def test_structured_json_request_media_type_preserves_arbitrary_question_text():
    marker = "LLaDAR calibration marker"
    template = RequestTemplate.from_interaction(
        CapturedInteraction(
            method="POST",
            url="https://example.test/graphql",
            headers={"content-type": "application/graphql+json"},
            request_body=json.dumps({"variables": {"question": marker}}),
            response=ObservedResponse("application/graphql-response+json", '{"data":{"answer":"ok"}}'),
        ),
        marker,
    )

    rendered = json.loads(template.render_body('Why "this"?'))

    assert rendered["variables"]["question"] == 'Why "this"?'


def test_form_request_template_url_encodes_arbitrary_question_text():
    marker = "LLaDAR calibration marker"
    template = RequestTemplate.from_interaction(
        CapturedInteraction(
            method="POST",
            url="https://example.test/ask",
            headers={"content-type": "application/x-www-form-urlencoded"},
            request_body="question=LLaDAR+calibration+marker&mode=chat",
            response=ObservedResponse("text/plain", "ok"),
        ),
        marker,
    )

    rendered = parse_qs(template.render_body("A&B + C"), strict_parsing=True)

    assert rendered == {"question": ["A&B + C"], "mode": ["chat"]}


def test_get_request_template_rewrites_encoded_query_value_only():
    marker = "LLaDAR calibration marker"
    template = RequestTemplate.from_interaction(
        CapturedInteraction(
            method="GET",
            url="https://example.test/ask?question=LLaDAR+calibration+marker&mode=chat",
            headers={},
            request_body="",
            response=ObservedResponse("application/json", '{"answer":"ok"}'),
        ),
        marker,
    )

    rendered = parse_qs(urlsplit(template.render_url("A&B + C")).query, strict_parsing=True)

    assert rendered == {"question": ["A&B + C"], "mode": ["chat"]}
    assert template.render_body("A&B + C") == ""


def test_multipart_calibration_is_blocked_instead_of_replayed_as_raw_text():
    marker = "LLaDAR calibration marker"
    body = (
        "--fixture\r\nContent-Disposition: form-data; name=\"question\"\r\n\r\n"
        f"{marker}\r\n--fixture--\r\n"
    )

    with pytest.raises(ValueError, match="multipart"):
        RequestTemplate.from_interaction(
            CapturedInteraction(
                method="POST",
                url="https://example.test/ask",
                headers={"content-type": "multipart/form-data; boundary=fixture"},
                request_body=body,
                response=ObservedResponse("application/json", '{"answer":"ok"}'),
            ),
            marker,
        )


def test_request_budget_failure_after_success_does_not_claim_another_request():
    from types import SimpleNamespace
    from lladar.answer_extraction import ExtractionError
    from lladar.browser_target import BrowserTarget, browser_failure_diagnostic

    calls = []
    target = BrowserTarget.__new__(BrowserTarget)
    target.evidence = {"status": "verified", "stage": "ready"}
    target._template, target._remaining_requests = object(), 1
    target._extractor = SimpleNamespace(check_budget=lambda: None, remaining_seconds=lambda: 60,
                                        extract=lambda *args, **kwargs: "answer")
    target.driver = SimpleNamespace(replay=lambda *args, **kwargs: calls.append(args))
    assert target.answer("question", "first") == "answer"
    with pytest.raises(ExtractionError) as caught:
        target.answer("question", "second")
    assert caught.value.reason == "extraction_budget_exhausted"
    diagnostic = target.evidence["diagnostic"]
    assert diagnostic["stage"] == "dataset_preflight"
    assert diagnostic["request_attempted"] is False and len(calls) == 1
    with pytest.raises(ExtractionError) as blocked:
        target.answer("question", "third")
    assert browser_failure_diagnostic(blocked.value, target.evidence)["blocked_by"] == "extraction_budget_exhausted"


def test_diagnostics_reject_remote_strings_in_metadata():
    from lladar.browser_target import BrowserRequestFailure, BrowserRequestTimeout, browser_failure_diagnostic

    evidence = {"stage": "fixture-secret-url", "failure_reason": "fixture-secret-token"}
    errors = [BrowserRequestFailure("fixture-secret-reason", http_status="fixture-secret-status",
                                   received_bytes="fixture-secret-bytes", elapsed_seconds="fixture-secret-time"),
              BrowserRequestTimeout(timeout_seconds=float("inf"), http_status=True,
                                    received_bytes=True, elapsed_seconds=float("nan"))]
    for error in errors:
        diagnostic = browser_failure_diagnostic(error, evidence)
        assert "http_status" not in diagnostic and "timeout_seconds" not in diagnostic
        assert "received_bytes" not in diagnostic and "elapsed_seconds" not in diagnostic
        assert "fixture-secret" not in str(diagnostic)
