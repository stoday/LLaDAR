"""Offline budget propagation checks; model responses are scripted, not live."""
import json
from pathlib import Path
import sys

import pytest

from lladar.auto_adapter import AutoAdapter
from lladar.cli import build_parser, main
from lladar.model_profiles import resolve_model_profile
from lladar.runner import run_agent


@pytest.mark.parametrize("model, expected", [
    ("gemini:gemini-3.8-flash", (1_048_576, 65_536)),
    ("gemini:gemini-3-flash-preview", (1_048_576, 65_536)),
    ("gemini:gemini-3.7-flash", (1_048_576, 65_536)),
    ("gemini:gemini-2.5-flash", (1_048_576, 65_536)),
    ("unknown:model", (16_384, 8_192)),
])
def test_adapter_resolves_shared_profile(tmp_path, model, expected):
    project = tmp_path / "project"
    project.mkdir()
    adapter = AutoAdapter(project, python=Path(sys.executable), env_file=None, model=model)
    assert (adapter.model_profile.max_input_tokens, adapter.model_profile.max_output_tokens) == expected
    assert adapter.model_profile == resolve_model_profile(model)


@pytest.mark.parametrize("overrides, expected", [
    ({}, (1_048_576, 65_536)),
    ({"max_input_tokens": 32000}, (32000, 65_536)),
    ({"max_output_tokens": 16000}, (1_048_576, 16000)),
    ({"max_input_tokens": 32000, "max_output_tokens": 16000}, (32000, 16000)),
])
def test_discovery_generation_and_repair_receive_same_budget(tmp_path, monkeypatch, overrides, expected):
    import akasha

    project = tmp_path / "project"
    project.mkdir()
    (project / "entrypoint.py").write_text("def answer(question): return question\n", encoding="utf-8")
    adapter = AutoAdapter(
        project, python=Path(sys.executable), env_file=None,
        model="gemini:gemini-3.8-flash", graphify=False, verbose=False, **overrides,
    )
    calls = []

    def scripted_agent(**options):
        calls.append(options)
        turn = len(calls)

        def respond(prompt):
            if turn == 1:
                result = {"candidates": [{
                    "id": "public", "label": "Public answer", "entrypoint": "entrypoint.py:answer",
                    "public_boundary": True, "rationale": "Public function", "flow": ["answer"],
                    "output": "returned string", "transport": "python", "service": None,
                    "evidence": [{"path": "entrypoint.py", "line": 1, "quote": "def answer(question)"}],
                }], "unresolved": [], "summary": "Public function"}
            elif turn == 2:
                # Exercise the fresh repair-agent path after initial generation fails.
                return iter([{"type": "answer", "data": "not a proposal"}])
            else:
                assert turn == 3, "Unexpected extra model call"
                path = adapter.explorer.write_harness("adapter.py", (
                    "import json, sys, os\n"
                    "sys.path.insert(0, os.getcwd())\n"
                    "from entrypoint import answer\n"
                    "request = json.load(sys.stdin)\n"
                    "print(json.dumps({'request_id': request['request_id'], "
                    "'output': answer(request['message']), 'observation': 'entrypoint.answer'}))\n"
                ))
                result = {"harness": path, "explanation": "Call public function", "blockers": []}
            return iter([{"type": "answer", "data": json.dumps(result)}])

        return respond

    monkeypatch.setattr(akasha, "agents", scripted_agent)
    adapter.prepare(["hello"], interactive=False, interface_selector=lambda plan: "public")

    assert len(calls) == 3
    for options in calls:
        assert options["model"] == "gemini:gemini-3.8-flash"
        assert (options["max_input_tokens"], options["max_output_tokens"]) == expected
        assert options["thinking"] is True and options["stream"] is True
    report = json.loads((adapter.evidence / "run.json").read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert (report["max_input_tokens"], report["max_output_tokens"]) == expected


@pytest.mark.parametrize("name", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("value", [0, -1])
def test_invalid_budget_fails_before_evidence_creation(tmp_path, name, value):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match=name + " must be greater than 0"):
        AutoAdapter(project, python=Path(sys.executable), env_file=None,
                    model="gemini:gemini-3-flash-preview", **{name: value})
    assert not (tmp_path / "adapter").exists()
    with pytest.raises(ValueError, match=name + " must be greater than 0"):
        run_agent(tmp_path / "absent.jsonl", tmp_path / "answers.jsonl", project=project, **{name: value})


@pytest.mark.parametrize("target", [{"answer": lambda question: question}, {"page_url": "https://example.test"}])
@pytest.mark.parametrize("name", ["max_input_tokens", "max_output_tokens"])
def test_adapter_budget_is_rejected_for_other_targets(tmp_path, target, name):
    with pytest.raises(ValueError, match="apply only to a project target"):
        run_agent(tmp_path / "absent.jsonl", tmp_path / "answers.jsonl", **target, **{name: 16000})


@pytest.mark.parametrize("flags, expected", [
    ([], (1_048_576, 65_536)),
    (["--max-input-tokens", "32000", "--max-output-tokens", "16000"], (32000, 16000)),
])
def test_cli_through_runner_passes_budget_to_adapter(tmp_path, isolated_target_python, monkeypatch, flags, expected):
    project = tmp_path / "project"
    project.mkdir()
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"question": "hello", "expected_answer": "hello", "actual_response": None}) + "\n")
    captured = []

    def capture_prepare(self, probes, **options):
        captured.append((self.model, self.model_profile.max_input_tokens, self.model_profile.max_output_tokens))
        raise RuntimeError("Stop before live discovery")

    monkeypatch.setattr(AutoAdapter, "prepare", capture_prepare)
    result = main([
        "run-agent", str(dataset), "--project", str(project),
        "--target-python", str(isolated_target_python), "--output", str(tmp_path / "answers.jsonl"),
        "--no-verbose", *flags,
    ], runs_root=tmp_path / "runs")
    assert result == 2
    assert captured == [("gemini:gemini-3.8-flash", *expected)]


def test_cli_budget_defaults_defer_to_profile():
    args = build_parser().parse_args(["run-agent", "dataset.jsonl"])
    assert args.max_input_tokens is None
    assert args.max_output_tokens is None
