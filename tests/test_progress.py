from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

import lladar
from lladar.cli import main
from lladar.validation import QUALITY_CHECKS


def _candidate():
    return {
        "key_information": {"dimension": "limit", "text": "10-user", "value": "10"},
        "original": {"question": "What is the 10-user plan limit?", "answer": "10."},
        "variants": [
            {
                "kind": "information_omission",
                "question": "What is the plan limit?",
                "answer": None,
                "change": {"removed": ["10-user"], "added": []},
            },
            *[
                {
                    "kind": "peer_cue_addition",
                    "question": f"What is the plan limit for my {role}?",
                    "answer": None,
                    "change": {"removed": ["10-user"], "added": [f"my {role}"]},
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


def _judgment():
    return {
        "valid": True,
        "reason": "valid",
        "reason_code": "quality_validation_failed",
        "semantic_key": "plan limit|10|limit",
        "checks": {check: True for check in QUALITY_CHECKS},
    }


class FakeProvider:
    def generate_structured(self, prompt, *, model, temperature):
        if "Validate one generated question group" in prompt:
            return _judgment()
        if "semantic knowledge segmenter" in prompt:
            return {
                "segments": [
                    {"unit_ids": ["u0"], "knowledge_facts": ["A source fact."]}
                ]
            }
        return _candidate()


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_api_shows_timestamped_configuration_progress_and_eta_by_default(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")

    lladar.create_test_dataset(knowledge=knowledge, provider=FakeProvider())

    progress = capsys.readouterr().err
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", progress)
    for expected in (
        "[CONFIG]",
        "effective settings",
        "knowledge",
        "prompt_source        none",
        "policies             general-social-context@1",
        "chunk_size           2000",
        "overlap              0.1",
        "count                0",
        "model                gemini:gemini-3.7-flash",
        "provider             FakeProvider",
        "[SOURCE]",
        "[CHUNK]",
        "[GROUP]",
        "target_ready=all",
        "[GROUP]",
        "elapsed=",
        "ETA=",
        "[DONE]",
    ):
        assert expected in progress


def test_cli_colors_verbose_labels_on_a_tty(tmp_path: Path, monkeypatch):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    stderr = TtyBuffer()
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stderr", stderr)

    assert main(
        ["create", "test-dataset", "--knowledge", str(knowledge), "--output", str(output)],
        provider=FakeProvider(),
    ) == 0
    assert "\x1b[" in stderr.getvalue()
    assert "[CONFIG]" in stderr.getvalue()


def test_cli_can_disable_verbose_progress(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    assert main(
        [
            "create", "test-dataset", "--knowledge", str(knowledge),
            "--output", str(output), "--no-verbose",
        ],
        provider=FakeProvider(),
    ) == 0
    assert capsys.readouterr().err == ""


def test_api_reports_retry_cache_and_write_events(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("Plan A has a limit of 10 users.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    cache_dir = tmp_path / "cache"

    class RecoveringProvider(FakeProvider):
        def __init__(self):
            self.failed = False

        def generate_structured(self, prompt, *, model, temperature):
            if "Generate one source-grounded question group" in prompt and not self.failed:
                self.failed = True
                return {"key_information": {}}
            return super().generate_structured(prompt, model=model, temperature=temperature)

    lladar.create_test_dataset(
        knowledge=knowledge,
        output=output,
        cache=True,
        cache_dir=cache_dir,
        provider=RecoveringProvider(),
    )
    first_progress = capsys.readouterr().err

    class UnavailableProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise AssertionError("group cache should satisfy this run")

    lladar.create_test_dataset(
        knowledge=knowledge,
        cache=True,
        cache_dir=cache_dir,
        provider=UnavailableProvider(),
    )
    second_progress = capsys.readouterr().err

    assert "[RETRY]" in first_progress
    assert "[CACHE]" in first_progress and "miss" in first_progress
    assert "saved" in first_progress
    assert "[WRITE]" in first_progress
    assert "[CACHE]" in second_progress and "hit" in second_progress


def test_api_reports_each_semantic_window(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact. " * 20, encoding="utf-8")

    class SegmentProvider:
        def generate_structured(self, prompt, *, model, temperature):
            assert "semantic knowledge segmenter" in prompt
            return {"segments": []}

    lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size="auto",
        max_output_tokens=20,
        auto_window_ratio=0.5,
        provider=SegmentProvider(),
        strict=True,
    )

    progress = capsys.readouterr().err
    assert "[WINDOW]" in progress
    assert "1/" in progress


def test_operational_provider_errors_do_not_leak_secrets(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact.", encoding="utf-8")

    class SecretErrorProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise lladar.ProviderError("request failed with API key TOP-SECRET-VALUE")

    with pytest.raises(lladar.ProviderError):
        lladar.create_test_dataset(knowledge=knowledge, provider=SecretErrorProvider())
    progress = capsys.readouterr().err
    assert "TOP-SECRET-VALUE" not in progress
    assert "[RETRY]" not in progress


def test_cli_reports_generic_provider_failure_without_secret(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A fact.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    class SecretErrorProvider:
        def generate_structured(self, prompt, *, model, temperature):
            raise lladar.ProviderError("request failed with API key CLI-SECRET-VALUE")

    exit_code = main(
        [
            "create", "test-dataset", "--knowledge", str(knowledge),
            "--chunk-size", "2000", "--output", str(output),
        ],
        provider=SecretErrorProvider(),
    )

    stderr = capsys.readouterr().err
    assert exit_code == 2
    assert "provider generation failed" in stderr
    assert "CLI-SECRET-VALUE" not in stderr
