import json
import pytest

from lladar.providers import AkashaProvider


VALID_PAIR = {
    "complete_question": "完整問題",
    "complete_answer": "完整答案",
    "underspecified_question": "缺失問題",
    "missing_information": "缺失資訊",
    "invalid_assumptions": ["錯誤假設"],
    "acceptable_behaviors": ["ask_clarification"],
}


def test_akasha_provider_parses_a_fenced_json_response():
    class FakeAgent:
        def __call__(self, prompt):
            return "```json\n" + json.dumps(VALID_PAIR, ensure_ascii=False) + "\n```"

    provider = AkashaProvider(agent_factory=lambda **kwargs: FakeAgent())

    result = provider.generate_structured(
        "generate a pair",
        model="gemini:gemini-2.5-flash",
        temperature=0.0,
    )

    assert result == VALID_PAIR

def test_akasha_provider_reserves_output_budget_for_complete_json():
    class FakeAgent:
        def __init__(self, response):
            self.response = response

        def __call__(self, prompt):
            return self.response

    def factory(**kwargs):
        if (
            kwargs.get("thinking")
            or kwargs.get("max_input_tokens") != 1_048_576
            or kwargs.get("max_output_tokens") != 65_536
        ):
            return FakeAgent('{"complete_question": "truncated')
        return FakeAgent(json.dumps(VALID_PAIR, ensure_ascii=False))

    provider = AkashaProvider(agent_factory=factory)

    result = provider.generate_structured(
        "generate a pair",
        model="gemini:gemini-2.5-flash",
        temperature=0.0,
    )

    assert result == VALID_PAIR

def test_user_can_override_akasha_token_budget():
    captured = {}

    class FakeAgent:
        def __call__(self, prompt):
            return json.dumps(VALID_PAIR, ensure_ascii=False)

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    provider = AkashaProvider(
        agent_factory=factory,
        max_input_tokens=12_345,
        max_output_tokens=6_789,
    )
    provider.generate_structured(
        "generate a pair",
        model="gemini:gemini-2.5-flash",
        temperature=0.0,
    )

    assert captured["max_input_tokens"] == 12_345
    assert captured["max_output_tokens"] == 6_789

def test_unknown_model_uses_conservative_token_budget():
    captured = {}

    class FakeAgent:
        def __call__(self, prompt):
            return json.dumps(VALID_PAIR, ensure_ascii=False)

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    provider = AkashaProvider(agent_factory=factory)
    provider.generate_structured(
        "generate a pair",
        model="custom:model",
        temperature=0.0,
    )

    assert captured["max_input_tokens"] == 16_384
    assert captured["max_output_tokens"] == 8_192


def test_akasha_provider_forwards_verbose_to_the_agent():
    captured = {}

    class FakeAgent:
        def __call__(self, prompt):
            return json.dumps(VALID_PAIR, ensure_ascii=False)

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    provider = AkashaProvider(agent_factory=factory, verbose=True)
    provider.generate_structured(
        "generate a pair",
        model="gemini:gemini-2.5-flash",
        temperature=0.0,
    )

    assert captured["verbose"] is True


def test_streamed_provider_collects_only_answer_and_routes_trace_to_stderr(capsys):
    captured = {}

    class Agent:
        def __call__(self, prompt):
            def events():
                print("[agent.trace] start", flush=True)
                yield {"type": "progress", "data": "not part of the answer"}
                yield {"type": "thinking", "data": "not part of the answer"}
                yield {"type": "tool", "data": {"output": "not part of the answer"}}
                yield {"type": "answer", "data": '{"ok":'}
                print("[agent.trace] finish", flush=True)
                yield {"type": "answer", "data": 'true}'}
            return events()

    def factory(**kwargs):
        captured.update(kwargs)
        return Agent()

    provider = AkashaProvider(agent_factory=factory, verbose=True, stream=True)
    assert provider.generate_structured("request", model="custom:model", temperature=0) == {"ok": True}
    logs = capsys.readouterr()
    assert logs.out == ""
    assert "[agent.trace] start" in logs.err
    assert "[agent.trace] finish" in logs.err
    assert "not part of the answer" not in logs.err
    assert captured["thinking"] is False
    assert captured["stream"] is True
    assert captured["verbose"] is True


@pytest.mark.parametrize("event,message", [
    ({"type": "error", "data": "private-provider-details"}, "generation stream failed"),
    ({"type": "answer", "data": None}, "answer chunks must be text"),
    ({"type": "progress", "data": "private-provider-details"}, "response was empty"),
])
def test_streamed_provider_rejects_failed_or_empty_answers(event, message):
    from lladar.exceptions import ProviderError

    class Agent:
        def __call__(self, prompt):
            return iter([event])

    provider = AkashaProvider(agent_factory=lambda **_: Agent(), stream=True)
    with pytest.raises(ProviderError, match=message) as failure:
        provider.generate_text("request", model="custom:model", temperature=0)
    assert "private-provider-details" not in str(failure.value)
