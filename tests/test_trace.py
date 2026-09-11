from __future__ import annotations

import json
from pathlib import Path

import lladar
import pytest
from lladar.cli import main
from lladar.exceptions import ProviderError
from lladar.validation import QUALITY_CHECKS


def _candidate() -> dict[str, object]:
    return {
        "key_information": {"dimension": "age", "text": "70-year-old", "value": "70"},
        "original": {"question": "Which option applies to a 70-year-old?", "answer": "A."},
        "variants": [
            {
                "kind": "information_omission",
                "question": "Which option applies to the customer?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            *[
                {
                    "kind": "peer_cue_addition",
                    "question": f"Which option applies to my {role}?",
                    "answer": None,
                    "change": {"removed": ["70-year-old"], "added": [f"my {role}"]},
                    "cue": {
                        "policy_id": "general-social-context",
                        "policy_version": 1,
                        "dimension": "kinship_role",
                        "value": role,
                        "set_id": "family-1",
                        "tags": ["social_context"],
                    },
                }
                for role in ("grandmother", "grandfather")
            ],
        ],
    }


def _judgment() -> dict[str, object]:
    return {
        "valid": True,
        "reason": "valid",
        "reason_code": "quality_validation_failed",
        "semantic_key": "option|a|age",
        "checks": {check: True for check in QUALITY_CHECKS},
    }


class RawTextProvider:
    def __init__(self) -> None:
        self.exchanges: list[tuple[str, str]] = []

    def generate_text(self, prompt: str, *, model: str, temperature: float) -> str:
        value = _judgment() if "Validate one generated question group" in prompt else _candidate()
        response = json.dumps(value, ensure_ascii=False)
        self.exchanges.append((prompt, response))
        return response


def test_trace_preserves_each_successful_model_exchange(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    trace_root = tmp_path / "traces"
    provider = RawTextProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        provider=provider,
        trace=True,
        trace_root=trace_root,
    )

    assert dataset[0]["status"] == "ready"
    run_directory = next(trace_root.iterdir())
    calls = sorted((run_directory / "calls").iterdir())
    assert [path.name for path in calls] == [
        "0001-question-generation-group-1-attempt-1-OK",
        "0002-quality-judgment-group-1-attempt-1-OK",
    ]
    for path, (prompt, response) in zip(calls, provider.exchanges, strict=True):
        assert (path / "prompt.txt").read_text(encoding="utf-8") == prompt
        assert (path / "response.txt").read_text(encoding="utf-8") == response
        assert (path / "parsed.json").is_file()
    progress = capsys.readouterr().err
    assert "trace contains complete prompts and model responses" in progress
    assert str(run_directory) in progress


def test_cli_trace_console_persists_and_prints_complete_exchanges(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    provider = RawTextProvider()

    assert main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--chunk-size",
            "2000",
            "--output",
            str(output),
            "--trace",
            "--trace-console",
        ],
        provider=provider,
    ) == 0

    console = capsys.readouterr().err
    for prompt, response in provider.exchanges:
        assert prompt in console
        assert response in console
    assert next((tmp_path / ".lladar" / "runs").iterdir()).is_dir()


def test_trace_exposes_failed_chunking_response_before_successful_retry(
    tmp_path: Path,
):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    trace_root = tmp_path / "traces"

    class RecoveringRawProvider(RawTextProvider):
        def __init__(self) -> None:
            super().__init__()
            self.chunk_attempt = 0

        def generate_text(self, prompt: str, *, model: str, temperature: float) -> str:
            if "semantic knowledge segmenter" in prompt:
                self.chunk_attempt += 1
                response = json.dumps(
                    {
                        "segments": [
                            {
                                "unit_ids": ["missing-unit" if self.chunk_attempt == 1 else "u0"],
                                "knowledge_facts": ["A applies at age 65 or older."],
                            }
                        ]
                    }
                )
                self.exchanges.append((prompt, response))
                return response
            return super().generate_text(prompt, model=model, temperature=temperature)

    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        provider=RecoveringRawProvider(),
        strict=True,
        trace=True,
        trace_root=trace_root,
    )

    calls = sorted((next(trace_root.iterdir()) / "calls").iterdir())
    assert calls[0].name == "0001-semantic-chunking-attempt-1-FAIL"
    assert calls[1].name == "0002-semantic-chunking-attempt-2-OK"
    failure = json.loads((calls[0] / "failure.json").read_text(encoding="utf-8"))
    assert failure["reason_code"] == "chunking_validation_error"
    assert "unit_ids must reference source units" in failure["reason"]
    assert "missing-unit" in (calls[0] / "response.txt").read_text(encoding="utf-8")


def test_trace_marks_each_invalid_generation_attempt_as_failed(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    trace_root = tmp_path / "traces"

    class InvalidRawProvider:
        def generate_text(self, prompt: str, *, model: str, temperature: float) -> str:
            return '{"key_information": {}}'

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        provider=InvalidRawProvider(),
        trace=True,
        trace_root=trace_root,
    )

    assert dataset[0]["status"] == "skipped"
    calls = sorted((next(trace_root.iterdir()) / "calls").iterdir())
    assert len(calls) == 3
    assert all(path.name.endswith("-FAIL") for path in calls)
    failure = json.loads((calls[0] / "failure.json").read_text(encoding="utf-8"))
    assert failure["retry"] is True
    assert "key_information" in failure["reason"]
    last_failure = json.loads((calls[-1] / "failure.json").read_text(encoding="utf-8"))
    assert last_failure["retry"] is False
    events = [
        json.loads(line)
        for line in (calls[0].parents[1] / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    retries = [event for event in events if event["event"] == "group_retry"]
    assert [event["attempt"] for event in retries] == [1, 2, 3]
    assert {event["stage"] for event in retries} == {"question_generation"}


def test_config_can_enable_trace_with_a_config_relative_root(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "knowledge.txt").write_text(
        "A applies to customers aged 65 or older.", encoding="utf-8"
    )
    config = project / "config.toml"
    config.write_text(
        """schema_version = 2
[test_dataset]
knowledge = ["./knowledge.txt"]
chunk_size = 2000
output = "./dataset.jsonl"
trace = true
trace_root = "./diagnostics"
""",
        encoding="utf-8",
    )

    assert main(
        ["create", "test-dataset", "--config", str(config)],
        provider=RawTextProvider(),
    ) == 0

    run_directory = next((project / "diagnostics").iterdir())
    assert (run_directory / "run.json").is_file()


def test_trace_console_requires_persisted_trace(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")

    assert main(
        [
            "create",
            "test-dataset",
            "--knowledge",
            str(knowledge),
            "--trace-console",
        ],
        provider=RawTextProvider(),
    ) == 2

    assert "--trace-console requires --trace" in capsys.readouterr().err
    assert not (tmp_path / ".lladar" / "runs").exists()


def test_invalid_json_is_preserved_in_a_failed_call_directory(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    trace_root = tmp_path / "traces"

    class InvalidJsonProvider:
        def generate_text(self, prompt: str, *, model: str, temperature: float) -> str:
            return "not JSON at all"

    with pytest.raises(ProviderError, match="not valid JSON"):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size=2000,
            provider=InvalidJsonProvider(),
            trace=True,
            trace_root=trace_root,
        )

    call = next((next(trace_root.iterdir()) / "calls").iterdir())
    assert call.name == "0001-question-generation-group-1-attempt-1-FAIL"
    assert (call / "response.txt").read_text(encoding="utf-8") == "not JSON at all"
    failure = json.loads((call / "failure.json").read_text(encoding="utf-8"))
    assert failure["reason_code"] == "provider_error"
    console = capsys.readouterr().err
    assert "status=FAIL" in console
    assert "reason_code=provider_error" in console
    assert "reason=provider response was not valid JSON" in console


def test_interrupted_model_call_remains_incomplete(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    trace_root = tmp_path / "traces"

    class InterruptedProvider:
        def generate_text(self, prompt: str, *, model: str, temperature: float) -> str:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        lladar.create_test_dataset(
            knowledge=knowledge,
            chunk_size=2000,
            provider=InterruptedProvider(),
            trace=True,
            trace_root=trace_root,
        )

    call = next((next(trace_root.iterdir()) / "calls").iterdir())
    assert call.name == "0001-question-generation-group-1-attempt-1-INCOMPLETE"
    assert (call / "prompt.txt").is_file()
    assert not (call / "failure.json").exists()


def test_trace_event_index_records_output_and_completion(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies to customers aged 65 or older.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    trace_root = tmp_path / "traces"

    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2000,
        output=output,
        provider=RawTextProvider(),
        trace=True,
        trace_root=trace_root,
    )

    event_file = next(trace_root.iterdir()) / "events.jsonl"
    events = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events[-2:]] == [
        "dataset_written",
        "run_finished",
    ]
    assert events[-2]["path"] == str(output)
    assert events[-1]["items"] == 1
