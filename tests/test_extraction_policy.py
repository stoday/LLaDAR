"""Direct extraction through the public CLI; website and provider are fixtures."""
import json
import io
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import pytest

from lladar.browser_target import BrowserTarget, CapturedInteraction
from lladar.cli import main
from lladar.records import read_records
from lladar.response_capture import ObservedAnswer, ResponseObservationSource
from fixture_extraction import FixtureTerminal, browser_cli_terminal


class QuestionWebsite:
    def __init__(self):
        self.source = ResponseObservationSource()
        self.questions = []
        self.closed = False

    def response(self, text, request_id):
        return self.source.begin_request(request_id).observe("application/json", json.dumps({"opaque": text}))

    def capture_calibration(self, marker, prompt):
        prompt(marker)
        return CapturedInteraction("POST", "https://fixture.test/api/ask",
                                   {"content-type": "application/json", "cookie": "session=private-session"},
                                   json.dumps({"question": marker}), self.response("Calibration answer", marker))

    def replay(self, template, question, request_id, *, timeout_seconds=None):
        self.questions.append(question)
        return self.response("Independent answer" if request_id == "browser-verification" else "Wrong target answer", request_id)

    def close(self):
        self.closed = True


def cli_case(tmp_path, website, respond, messages, *, flags=(), factory=None, expected="Correct expected answer", terminal=None):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    dataset.write_text(json.dumps({"question": "Dataset question", "expected_answer": expected,
                                   "actual_response": None}) + "\n", encoding="utf-8")
    argv = ["run-agent", str(dataset), "--page-url", "https://fixture.test/chat", "--output", str(output),
            "--skill", "src/lladar/skill_assets/run-agent-random-sample", "--no-verbose", *flags]
    with browser_cli_terminal():
        result = main(argv, runs_root=tmp_path / "runs", browser_target_factory=lambda **settings: BrowserTarget(
            **settings, driver=website, input_fn=respond, output_fn=messages.append,
            review_stream=terminal if terminal is not None else FixtureTerminal(),
        ), extraction_provider_factory=factory)
    return result, output


def test_cli_extracts_every_response_with_no_parser_cache_and_never_supplies_expected_answer(tmp_path):
    website, calls, messages = QuestionWebsite(), [], []
    prompts = iter(["", "YES", "MATCH"])
    shown = []
    terminal = FixtureTerminal()

    def respond(prompt):
        shown.append(prompt)
        if prompt.startswith("Type YES"):
            disclosure = " ".join(messages)
            for required in ("fixture.test", "1 verification", "1 scheduled", "Real response content",
                             "generativelanguage.googleapis.com", "gemini:gemini-3.8-flash", "3 model calls",
                             "8192", "60-minute", "Charges may apply"):
                assert required in disclosure
            assert not calls and not website.questions
        if prompt.startswith("Type MATCH"):
            display = terminal.getvalue()
            assert "Complete recorded response" in display and "Extracted answer" in display
            assert '"opaque": "Independent answer"' in display
            assert "browser-verification" in display
            assert len(website.questions) == 1
        return next(prompts)

    def factory(**settings):
        assert settings["model_destination"] == "gemini:gemini-3.8-flash"
        assert website.questions == []
        def extract(request, **_):
            calls.append(request)
            return {"result": {"status": "final", "text": json.loads(request["body"])["opaque"]}, "usage": None}
        return SimpleNamespace(extract=extract)

    code, output = cli_case(tmp_path, website, respond, messages, factory=factory, terminal=terminal)
    assert code == 0
    assert read_records(output)[0]["actual_response"] == "Wrong target answer"
    assert len(calls) == 3
    assert len(website.questions) == 2
    assert "Correct expected answer" not in json.dumps(calls)
    assert "private-session" not in json.dumps(calls)
    sidecar = json.loads(output.with_name(output.name + ".run.json").read_text())
    assert sidecar["target"]["extraction"]["max_calls"] == 3
    assert "opaque" not in json.dumps(sidecar)
    assert len(shown) == 3 and not any("TRANSFER" in prompt for prompt in shown)
    assert sidecar["target"]["consent"]["website_requests"] is True
    assert sidecar["target"]["consent"]["response_model_transfer"] is True
    assert website.closed


@pytest.mark.parametrize("flag", ["--parser-policy", "--allow-parser-execution", "--allow-parser-model-transfer",
                                 "--parser-cache", "--no-parser-cache", "--clear-parser-cache"])
def test_removed_flags_are_rejected_before_reading_files_or_initializing_browser(flag):
    with pytest.raises(SystemExit) as caught:
        main(["run-agent", "does-not-exist.jsonl", "--page-url", "https://fixture.test", flag])
    assert caught.value.code == 2


@pytest.mark.parametrize("policy", ["builtin-only", "model-assisted"])
def test_removed_policy_values_are_not_compatibility_aliases(policy):
    with pytest.raises(SystemExit) as caught:
        main(["run-agent","does-not-exist.jsonl","--page-url","https://fixture.test","--parser-policy",policy])
    assert caught.value.code == 2


@pytest.mark.parametrize("flags,reply,reason", [
    (["--confirm-browser-run"], "NO", "response_transfer_not_approved"),
])
def test_website_approval_does_not_grant_transfer_or_bypass_reference(tmp_path, flags, reply, reason):
    website = QuestionWebsite()
    code, output = cli_case(tmp_path, website, lambda _: reply, [], flags=flags,
                            factory=lambda **_: pytest.fail("must not initialize provider"))
    assert code == 2 and not output.exists() and not website.questions
    run = json.loads(Path(str(output)+".run.json").read_text())
    assert run["target"]["failure_reason"] == reason


def test_both_approval_flags_still_require_local_match(tmp_path):
    website, prompts = QuestionWebsite(), []
    def respond(prompt):
        prompts.append(prompt)
        return "NO" if prompt.startswith("Type MATCH") else ""
    def factory(**_):
        return SimpleNamespace(extract=lambda data, **_: {"result":{"status":"final","text":json.loads(data["body"])["opaque"]},"usage":None})
    code, output = cli_case(tmp_path, website, respond, [], flags=["--confirm-browser-run","--allow-response-model-transfer"], factory=factory)
    assert code == 2 and not output.exists()
    assert len(website.questions) == 1
    assert len(prompts) == 2 and prompts[-1].startswith("Type MATCH")


@pytest.mark.parametrize("flags", [[], ["--confirm-browser-run"], ["--allow-response-model-transfer"]])
def test_declining_combined_approval_never_initializes_model_or_replays_website(tmp_path, flags):
    website, shown, messages = QuestionWebsite(), [], []

    def respond(prompt):
        shown.append(prompt)
        return "" if prompt.startswith("Press Enter") else "NO"

    code, output = cli_case(tmp_path, website, respond, messages, flags=flags,
                            factory=lambda **_: pytest.fail("unapproved provider initialization"))
    assert code == 2 and not output.exists() and not website.questions
    assert len(shown) == 2 and "BOTH" in shown[-1]
    assert "Real response content" in " ".join(messages)
    assert website.closed


def test_redirected_terminal_blocks_before_model_and_website_even_with_both_flags(tmp_path):
    website, redirected = QuestionWebsite(), io.StringIO()
    code, output = cli_case(tmp_path, website, lambda _: "", [], terminal=redirected,
                            flags=["--confirm-browser-run", "--allow-response-model-transfer"],
                            factory=lambda **_: pytest.fail("no interactive review destination"))
    assert code == 2 and not output.exists() and not website.questions
    assert redirected.getvalue() == ""
    assert json.loads(Path(str(output) + ".run.json").read_text())["target"]["failure_reason"] == "reference_required"


def test_local_reference_rejects_plausible_but_altered_model_answer(tmp_path):
    from lladar.answer_extraction import ExtractionOptions, ExtractionError
    website = QuestionWebsite()
    target = BrowserTarget(page_url="https://fixture.test", timeout=5, driver=website, confirmed=True,
        output_fn=lambda _: None, extraction_options=ExtractionOptions(
            transfer_approved=True,
            provider_factory=lambda **_: SimpleNamespace(extract=lambda *_, **__: {
                "result":{"status":"final","text":"invented answer"},"usage":None}),
            reference_reader=lambda _, identity: ObservedAnswer(identity,"Independent answer",True)))
    try:
        with pytest.raises(ExtractionError, match="reference_mismatch"):
            target.prepare([], interactive=False, request_count=1)
        with pytest.raises(ExtractionError):
            target.answer("must not send", "case-1")
    finally:
        target.close()
    assert len(website.questions) == 1


def test_keyboard_cancellation_saves_completed_answers_and_stops_dispatch(tmp_path):
    from lladar.runner import run_agent
    from lladar.answer_extraction import ExtractionOptions
    website, count = QuestionWebsite(), []
    dataset, output = tmp_path/"dataset.jsonl", tmp_path/"responses.jsonl"
    dataset.write_text("".join(json.dumps({"question":str(n),"expected_answer":"unused","actual_response":None})+"\n"
                               for n in range(3)))
    class Provider:
        def extract(self, data, **_):
            count.append(1)
            if len(count)==4:
                raise KeyboardInterrupt()
            return {"result":{"status":"final","text":json.loads(data["body"])["opaque"]},"usage":None}
    def target_factory(**settings):
        settings["extraction_options"] = ExtractionOptions(transfer_approved=True, provider_factory=lambda **_: Provider(),
            reference_reader=lambda _, identity: ObservedAnswer(identity,"Independent answer",True))
        return BrowserTarget(**settings, driver=website, output_fn=lambda _:None)
    with pytest.raises(KeyboardInterrupt):
        run_agent(
            dataset,output,page_url="https://fixture.test",confirm_browser_run=True,interactive=False,verbose=False,
            skill="src/lladar/skill_assets/run-agent-random-sample",browser_target_factory=target_factory)
    assert output.exists(), "Cancellation must persist the already completed answer"
    assert [row["actual_response"] for row in read_records(output)] == ["Wrong target answer",None,None]
    assert len(website.questions)==3 and len(count)==4 and website.closed
    assert json.loads(Path(str(output)+".run.json").read_text())["status"] == "cancelled"


def test_website_replay_cannot_outlive_shared_extraction_deadline(monkeypatch):
    import time
    from lladar.answer_extraction import ExtractionOptions
    now, timeouts = [0.0], []
    monkeypatch.setattr(time,"monotonic",lambda:now[0])
    class Website(QuestionWebsite):
        def replay(self, template, question, request_id, *, timeout_seconds=None):
            timeouts.append(timeout_seconds)
            return super().replay(template, question, request_id)
    class Provider:
        def extract(self, data, **_):
            now[0] += 100
            return {"result":{"status":"final","text":json.loads(data["body"])["opaque"]},"usage":None}
    target=BrowserTarget(page_url="https://fixture.test",timeout=3600,driver=Website(),confirmed=True,output_fn=lambda _:None,
        extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=lambda **_:Provider(),
            reference_reader=lambda _,identity:ObservedAnswer(identity,"Independent answer",True)))
    try:
        target.prepare([],interactive=False,request_count=1)
        assert target.answer("question","case") == "Wrong target answer"
    finally:
        target.close()
    assert timeouts == [3500,3400]


def test_model_budget_counts_scheduled_repeats_not_unique_questions(tmp_path):
    from lladar.runner import run_agent
    from lladar.answer_extraction import ExtractionOptions
    dataset,output=tmp_path/"dataset.jsonl",tmp_path/"responses.jsonl"
    dataset.write_text(json.dumps({"question":"one question","expected_answer":"unused","actual_response":None})+"\n")
    website,calls=QuestionWebsite(),[]
    def factory(**_):
        def extract(data, **_):
            calls.append(data)
            return {"result":{"status":"final","text":json.loads(data["body"])["opaque"]},"usage":None}
        return SimpleNamespace(extract=extract)
    def target_factory(**settings):
        settings["extraction_options"]=ExtractionOptions(transfer_approved=True,provider_factory=factory,
            reference_reader=lambda _,identity:ObservedAnswer(identity,"Independent answer",True))
        return BrowserTarget(**settings,driver=website,output_fn=lambda _:None)
    assert run_agent(dataset,output,page_url="https://fixture.test",confirm_browser_run=True,interactive=False,
                     browser_target_factory=target_factory,verbose=False) == 1
    assert len(calls)==5 and len(website.questions)==4
    run=json.loads(Path(str(output)+".run.json").read_text())
    assert run["target"]["extraction"]["max_calls"] == run["target"]["extraction"]["calls"] == 5
