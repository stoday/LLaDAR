"""Request-template and local profile boundaries shared across extraction modes."""
from pathlib import Path
import json
from urllib.parse import parse_qs, urlsplit
import pytest

from lladar.browser_target import BrowserTarget, CapturedInteraction, RequestTemplate
from lladar.response_capture import ObservedResponse, ResponseObservationSource
from fixture_extraction import fixture_options, FixtureTerminal

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
