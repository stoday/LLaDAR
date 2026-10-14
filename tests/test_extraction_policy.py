"""Public runner extraction contracts, without legacy browser capture fixtures."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lladar.answer_extraction import ResponseExtractor
from lladar.cli import main
from lladar.records import read_records
from lladar.response_capture import ResponseObservationSource
from lladar.runner import run_agent
from fixture_extraction import browser_cli_terminal


@pytest.mark.parametrize("flag", ["--parser-policy", "--allow-parser-execution", "--allow-parser-model-transfer",
                                 "--parser-cache", "--no-parser-cache", "--clear-parser-cache",
                                 "--calibration-question"])
def test_removed_flags_are_rejected_before_reading_files_or_initializing_browser(flag):
    with pytest.raises(SystemExit) as caught:
        main(["run-agent", "does-not-exist.jsonl", "--page-url", "https://fixture.test", flag])
    assert caught.value.code == 2


@pytest.mark.parametrize("policy", ["builtin-only", "model-assisted"])
def test_removed_policy_values_are_not_compatibility_aliases(policy):
    with pytest.raises(SystemExit) as caught:
        main(["run-agent", "does-not-exist.jsonl", "--page-url", "https://fixture.test", "--parser-policy", policy])
    assert caught.value.code == 2


@pytest.mark.parametrize("model", ["gemini:gemini-3.8-flash", "openai:fixture", "anthropic:fixture", "ollama:fixture"])
@pytest.mark.parametrize("output_limit", [32768, 65536])
def test_cli_routes_extraction_through_selected_model_and_keeps_expected_answer_private(tmp_path, model, output_limit):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    dataset.write_text(json.dumps({"question": "Formal dataset question", "expected_answer": "Private scoring oracle",
                                   "actual_response": None}) + "\n", encoding="utf-8")
    captures, settings = [], []

    def provider_factory(**configuration):
        settings.append(configuration)
        def extract(request, **_):
            captures.append(request)
            return {"result": {"status": "final", "text": request["body"]}, "usage": None}
        return SimpleNamespace(extract=extract)

    class Target:
        def __init__(self, **configuration):
            self.options = configuration["extraction_options"]
            assert configuration["probe_question"] == "What assistance does the service provide?"
            self.source = ResponseObservationSource()
            self.evidence = {"mode": "browser", "status": "verified"}
        def prepare(self, probes, *, interactive, record_count, request_count):
            assert probes == [], "Exploration must not receive future dataset questions"
            assert request_count == 3 and record_count == 1
            from dataclasses import replace
            # The external target stand-in grants the new single guided consent.
            self.options = replace(self.options, transfer_approved=True)
            self.extractor = ResponseExtractor(self.options, max_calls=request_count)
        def answer(self, question, request_id):
            observation = self.source.begin_request(request_id).observe("text/plain", "Original website answer")
            result = self.extractor.extract(observation, request_id=request_id)
            self.evidence["extraction"] = self.extractor.evidence
            return result
        def close(self):
            pass

    with browser_cli_terminal():
        assert main(["run-agent", str(dataset), "--page-url", "https://fixture.test/chat", "--output", str(output),
                     "--demo-question", "What assistance does the service provide?", "--model", model,
                     "--max-input-tokens", "32768", "--max-output-tokens", str(output_limit),
                     "--no-verbose"],
                    browser_target_factory=Target, extraction_provider_factory=provider_factory) == 0
    assert read_records(output)[0]["actual_response"] == "Original website answer"
    assert len(captures) == 3 and len(settings) == 1
    assert settings[0]["model_destination"] == model
    assert settings[0]["max_output_tokens"] == output_limit
    assert "Private scoring oracle" not in json.dumps(captures)
    assert "Formal dataset question" not in json.dumps(captures)
    run = json.loads(Path(str(output) + ".run.json").read_text(encoding="utf-8"))
    assert run["target"]["extraction"]["calls"] == 3
    assert run["target"]["extraction"]["input_byte_limit"] == 32768 * 4


def test_keyboard_cancellation_saves_completed_answers_and_stops_dispatch(tmp_path):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    dataset.write_text("".join(json.dumps({"question": str(number), "expected_answer": "unused", "actual_response": None})
                               + "\n" for number in range(3)), encoding="utf-8")
    attempts, closed = [], []
    class Target:
        evidence = {"mode": "browser", "status": "verified"}
        def __init__(self, **_):
            pass
        def prepare(self, probes, **_):
            assert probes == []
        def answer(self, question, request_id):
            attempts.append(request_id)
            if len(attempts) == 2:
                raise KeyboardInterrupt()
            return "Original completed answer"
        def close(self):
            closed.append(True)
    with pytest.raises(KeyboardInterrupt):
        run_agent(dataset, output, page_url="https://fixture.test", browser_target_factory=Target,
                  interactive=False, verbose=False)
    assert output.exists()
    assert [row["actual_response"] for row in read_records(output)] == ["Original completed answer"]
    assert len(attempts) == 2 and closed == [True]
    assert json.loads(Path(str(output) + ".run.json").read_text(encoding="utf-8"))["status"] == "cancelled"
