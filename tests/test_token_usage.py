"""Usage accounting at real LangChain/Akasha and dataset publication boundaries."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from lladar.token_usage import TokenCallback, TokenUsage, model_usage, token_usage_stage
from test_skill_agent import ToolScriptModel


def response(incoming=10, outgoing=4, **details):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="private answer", usage_metadata={"input_tokens": incoming,
        "output_tokens": outgoing, "total_tokens": incoming + outgoing, **details}))]])


def test_callback_missing_failed_fallback_and_duplicate_events(capsys):
    tracker = TokenUsage(verbose=True)
    callback = TokenCallback(tracker, "generation")
    run = uuid4()
    callback.on_chat_model_start({}, [], run_id=run)
    callback.on_llm_end(response(input_token_details={"cache_read": 3},
                                 output_token_details={"reasoning": 2}), run_id=run)
    callback.on_llm_end(response(), run_id=run)  # Duplicate callback, not another call.
    run = uuid4()
    callback.on_llm_start({}, [], run_id=run)
    callback.on_llm_end(LLMResult(generations=[], llm_output={"token_usage": {
        "prompt_tokens": 5, "completion_tokens": 2}}), run_id=run)
    run = uuid4()
    callback.on_llm_start({}, [], run_id=run)
    callback.on_llm_end(LLMResult(generations=[]), run_id=run)
    run = uuid4()
    callback.on_llm_start({}, [], run_id=run)
    callback.on_llm_error(RuntimeError("private credential"), run_id=run)
    callback.close()
    usage = tracker.snapshot()
    assert (usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]) == (15, 6, 21)
    assert (usage["cached_input_tokens"], usage["reasoning_output_tokens"]) == (3, 2)
    assert (usage["reported_calls"], usage["unreported_calls"], usage["failed_calls"]) == (2, 2, 1)
    assert usage["status"] == "partial"
    text = capsys.readouterr().err
    assert "[TOKENS]" in text and "input=15 output=6 total=21" in text
    assert "private" not in text + json.dumps(usage)


def test_wrapper_usage_is_not_double_counted_and_interruption_is_unknown():
    tracker = TokenUsage()
    callback = TokenCallback(tracker, "inventory")
    wrapper, inner, interrupted = uuid4(), uuid4(), uuid4()
    callback.on_chat_model_start({"id": ["akasha", "OllamaJSONModel"]}, [], run_id=wrapper)
    callback.on_chat_model_start({}, [], run_id=inner)
    callback.on_llm_end(response(), run_id=inner)
    callback.on_llm_end(response(), run_id=wrapper)
    callback.on_chat_model_start({}, [], run_id=interrupted)
    callback.close()
    assert tracker.snapshot()["total_tokens"] == 14
    assert tracker.snapshot()["unreported_calls"] == 1


def test_native_akasha_counts_each_tool_round_and_repeated_invocation(tmp_path, capsys):
    from lladar.skill_agent import AkashaSkillAgent
    from lladar.token_usage import usage_snapshot

    skill = tmp_path / "usage-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: usage-example\ndescription: Example.\n---\nLoad this skill.")
    def reply(content="Done", calls=None):
        return AIMessage(content=content, tool_calls=calls or [], usage_metadata={
            "input_tokens": 10, "output_tokens": 4, "total_tokens": 14})
    model = ToolScriptModel(replies=[
        reply("", [{"name": "load_skill", "args": {"reference": skill.name}, "id": "load"}]),
        reply(), reply(),
    ])
    @token_usage_stage
    def execute(**kwargs):
        agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model,
                                 env_file="", max_input_tokens=16000, max_output_tokens=2000, verbose=False)
        agent({"stage": "inventory"})
        assert usage_snapshot()["total_tokens"] == 28
        # Includes another invocation even if the skill remains loaded.
        agent({"stage": "inventory_audit"})
        return usage_snapshot()
    usage = execute(verbose=False)
    assert usage["reported_calls"] == 3
    assert usage["stages"]["inventory"]["total_tokens"] == 28
    assert usage["stages"]["inventory_audit"]["total_tokens"] == 14
    assert usage["input_tokens"] == 30 and usage["output_tokens"] == 12
    assert "[TOKENS]" not in capsys.readouterr().err
    assert usage_snapshot() is None
    with model_usage("generation"):
        model.replies.append(reply())
        model.invoke("Outside the dataset scope")
    assert usage["total_tokens"] == 42


@pytest.mark.parametrize("verbose", [False, True])
def test_dataset_cli_sidecar_report_and_completed_resume(tmp_path, capsys, monkeypatch, verbose):
    from lladar.cli import main
    from lladar.knowledge_coverage import render_coverage_report
    from test_knowledge_coverage import CoverageAgent

    class MeteredAgent(CoverageAgent):
        def __call__(self, request):
            model = ToolScriptModel(replies=[AIMessage(content="Done", usage_metadata={
                "input_tokens": 10, "output_tokens": 4, "total_tokens": 14})])
            with model_usage(request["stage"]):
                model.invoke("private request")
            return super().__call__(request)

    monkeypatch.chdir(tmp_path)
    source, output = tmp_path / "knowledge.md", tmp_path / "qa.jsonl"
    source.write_text("Service 1 retains data for 1 days.")
    args = ["create", "test-dataset", "--knowledge", str(source), "--output", str(output),
            "--lang", "en", "--verbose" if verbose else "--no-verbose"]
    assert main(args, skill_agent_factory=MeteredAgent) == 0
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    usage = sidecar["token_usage"]
    assert usage["status"] == "reported"
    assert set(usage["stages"]) == {"inventory", "inventory_reconciliation", "generation", "inventory_audit", "coverage_review"}
    assert sidecar["inventory"]["reconciliation"]["audit"]["reused"] == "initial"
    assert usage["reported_calls"] == 5
    assert usage["total_tokens"] == 70
    report = Path(str(output) + ".operation.md").read_text(encoding="utf-8")
    assert "`total_tokens`: 70" in report and "input=50, output=20, total=70" in report
    assert "`total_tokens`: 70" in render_coverage_report(sidecar)
    assert ("[TOKENS]" in capsys.readouterr().err) is verbose
    checkpoint = json.loads(Path(str(output) + ".work/session.json").read_text(encoding="utf-8"))
    assert checkpoint["data"]["state"]["token_usage"]["total_tokens"] == 70
    assert main([*args, "--resume", str(output) + ".work"], skill_agent_factory=MeteredAgent) == 0
    resumed_report = Path(str(output) + ".operation-2.md").read_text(encoding="utf-8")
    assert "`total_tokens`: 70" in resumed_report
    assert "input=0, output=0, total=0" in resumed_report


def test_failed_attempt_usage_survives_resume_without_recounting(tmp_path):
    from lladar.operation_report import OperationReport, operation_scope
    from lladar.token_usage import usage_snapshot
    from lladar.work_session import stage_session

    @stage_session("generation")
    @token_usage_stage
    def work(*, output, resume=None, force=False, verbose=False, skill=None):
        with model_usage("generation"):
            ToolScriptModel(replies=[AIMessage(content="Done", usage_metadata={
                "input_tokens": 10, "output_tokens": 4, "total_tokens": 14})]).invoke("private")
        if resume is None:
            raise RuntimeError("simulate interruption after a paid response")
        Path(output).write_text("done")
        return usage_snapshot()

    output = tmp_path / "output"
    report = OperationReport()
    with operation_scope(report):
        with pytest.raises(RuntimeError):
            work(output=output)
    assert report.token_usage["total_tokens"] == 14
    with operation_scope(report):
        usage = work(output=output, resume=str(output) + ".work")
    assert usage["total_tokens"] == 28
    assert report.token_usage["invocation"]["total_tokens"] == 14


def test_missing_and_legacy_usage_are_never_claimed_as_complete():
    assert TokenUsage().snapshot()["status"] == "unavailable"
    tracker = TokenUsage(historical_unknown=True)
    callback = TokenCallback(tracker, "generation")
    run = uuid4()
    callback.on_llm_start({}, [], run_id=run)
    callback.on_llm_end(response(), run_id=run)
    assert tracker.snapshot()["status"] == "partial"
    assert tracker.snapshot()["historical_usage_unknown"] is True
    tracker = TokenUsage()
    callback = TokenCallback(tracker, "generation")
    run = uuid4()
    callback.on_llm_start({}, [], run_id=run)
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="Done"))]]), run_id=run)
    assert tracker.snapshot()["total_tokens"] == 0
    assert tracker.snapshot()["unreported_calls"] == 1
    assert tracker.snapshot()["status"] == "partial"
