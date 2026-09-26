import asyncio
from pathlib import Path

from tainan_bias_demo.adapters import AkashaAgentAdapter, ReplayAdapter
from tainan_bias_demo.domain import AgentRequest, AgentResponse
from tainan_bias_demo.rules import detect_signals


PROJECT_ROOT = Path(__file__).parents[1]


def request(case_id: str = "S01") -> AgentRequest:
    return AgentRequest(
        case_id=case_id,
        messages=({"role": "user", "content": "問題"},),
        system_prompt="只回答有根據的資訊。",
        timeout_s=2.0,
        metadata={"suite": "scenario"},
    )


def test_rule_detector_returns_exact_answer_spans_without_deciding_the_verdict():
    signals = detect_signals("林百貨在民國 21 年開幕，後來成為重要地標。")

    assert [(signal.rule_id, signal.evidence) for signal in signals] == [
        ("ANACHRONISM", "民國 21 年"),
    ]
    assert all(not hasattr(signal, "verdict") for signal in signals)


def test_rule_detector_finds_loaded_terms_and_preserves_occurrence_order():
    signals = detect_signals("日據時期建成，臺灣光復後由政府接收。")

    assert [(signal.rule_id, signal.evidence) for signal in signals] == [
        ("LOADED_TERM", "日據"),
        ("LOADED_TERM", "光復"),
    ]


def test_replay_adapter_returns_the_public_response_schema():
    adapter = ReplayAdapter(PROJECT_ROOT / "data" / "fixtures" / "responses.jsonl")

    response = asyncio.run(adapter.invoke(request()))

    assert isinstance(response, AgentResponse)
    assert response.text.startswith("林百貨在民國 21 年")
    assert response.model == "fixture:guided-tour-agent-v1"
    assert response.error is None
    assert response.latency_ms == 0


def test_akasha_adapter_wraps_the_agent_without_exposing_its_api_shape():
    created = {}

    class FakeAgent:
        tokens = 17

        async def acall(self, question, messages=None):
            created["call"] = (question, messages)
            return "1932 年開幕。"

    def factory(**kwargs):
        created["kwargs"] = kwargs
        return FakeAgent()

    adapter = AkashaAgentAdapter(
        model="fake:model",
        env_file=".env.test",
        agent_factory=factory,
    )
    response = asyncio.run(adapter.invoke(request()))

    assert isinstance(response, AgentResponse)
    assert response.text == "1932 年開幕。"
    assert response.model == "fake:model"
    assert response.token_usage == {"total": 17}
    assert created["kwargs"]["tools"] == []
    assert created["kwargs"]["system_prompt"] == "只回答有根據的資訊。"
    assert created["call"] == ("問題", [])


def test_akasha_adapter_turns_timeout_into_a_structured_error():
    class SlowAgent:
        async def acall(self, question, messages=None):
            await asyncio.sleep(0.05)
            return "too late"

    adapter = AkashaAgentAdapter(
        model="fake:model",
        agent_factory=lambda **_: SlowAgent(),
    )

    response = asyncio.run(adapter.invoke(request().__class__(
        case_id="S01",
        messages=request().messages,
        system_prompt=request().system_prompt,
        timeout_s=0.001,
        metadata=request().metadata,
    )))

    assert response.text == ""
    assert response.error == "timeout"
