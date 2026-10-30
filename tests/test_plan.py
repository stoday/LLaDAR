"""Terminal and provider contracts retained across the planner v2 rewrite."""
from threading import Event
from types import SimpleNamespace
import pytest
from lladar.plan import PlanAgent, _message_text, _ask_interruptibly
from lladar.plan_ui import _is_free_input_option, _render_question
from lladar.language import Language, language_scope, current_language


def test_question_renderer_marks_selected_option():
    rendered = _render_question({
        "context": "我們正在選擇要觀察的行為。",
        "message": "你想先檢查哪一項？",
        "options": ["回答內容", "追問行為"],
    }, selected=1)
    assert "Context so far" in rendered
    assert "│" not in rendered
    assert rendered.splitlines()[1].startswith("──")
    assert rendered.splitlines()[-1].startswith("─")
    assert "\x1b[7m  2. 追問行為\x1b[0m" in rendered
    assert "Enter a number" in rendered

    interactive = _render_question({"message": "選一個", "options": ["A", "B"]},
                                   selected=0, interactive=True)
    assert "Use the up/down keys" in interactive



def test_message_text_accepts_langchain_content_blocks():
    assert _message_text([
        {"type": "text", "text": "{"},
        {"type": "text", "text": '"kind":"question"}'},
    ]) == '{"kind":"question"}'



def test_other_option_switches_to_free_text():
    question = {"options": [{"label": "其他", "description": "自己描述"}]}
    assert _is_free_input_option("1", question)
    assert _is_free_input_option("1", {"options": [{"label": "直接在對話中輸入"}]})
    assert _is_free_input_option("1", {"options": [{"label": "自訂問題清單"}]})
    assert _is_free_input_option("1", {"options": [{"label": "自己輸入回答"}]})
    assert _is_free_input_option("1", {"options": [{"label": "任意文字", "type": "free_input"}]})



@pytest.mark.parametrize("lang,target", [("zh-TW", "Traditional Chinese"), ("en", "English")])
def test_model_thread_receives_selected_language(monkeypatch, lang, target):
    observed = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def generate_message(self, prompt, *, system_prompt, **kwargs):
            observed.append((current_language().lang, system_prompt))
            return '{"kind":"question","message":"fixture","options":[]}'

    monkeypatch.setattr("lladar.plan.AkashaProvider", Provider)
    previous_language = current_language().lang
    with language_scope(Language(lang)):
        agent = PlanAgent(model="fixture:model", env_file=".env")
        _ask_interruptibly(agent, [])
    assert observed[0][0] == lang
    assert f"content in {target}." in observed[0][1]
    assert current_language().lang == previous_language



def test_debug_reports_waiting_while_model_is_running():
    release = Event()
    elapsed = []

    class SlowAgent:
        def ask(self, transcript):
            assert release.wait(2)
            return {"kind": "question"}

    def progress(seconds):
        elapsed.append(seconds)
        release.set()

    assert _ask_interruptibly(SlowAgent(), [], progress=progress, progress_interval=0.01) == {
        "kind": "question"
    }
    assert elapsed and elapsed[0] >= 0.01



def test_provider_stream_observer_receives_thinking_without_changing_answer(monkeypatch):
    from lladar.providers.akasha import AkashaProvider

    monkeypatch.setattr("lladar.providers.akasha.resolve_model_profile", lambda *args, **kwargs:
                        SimpleNamespace(max_input_tokens=1000, max_output_tokens=1000))
    events = []

    def factory(**kwargs):
        def agent(prompt):
            yield {"type": "thinking", "data": "thinking progress"}
            yield {"type": "answer", "data": '{"kind":'}
            yield {"type": "answer", "data": '"question"}'}
        return agent

    provider = AkashaProvider(agent_factory=factory, stream=True,
                              stream_observer=lambda kind, data: events.append((kind, data)))
    assert provider.generate_text("hello", model="fixture:model", temperature=0) == '{"kind":"question"}'
    assert events == [("thinking", "thinking progress"), ("answer", '{"kind":'),
                      ("answer", '"question"}')]



def test_keyboard_hint_allows_starting_a_free_form_answer():
    rendered = _render_question({"message": "說明情況", "options": ["選項"]}, interactive=True)
    assert "up/down keys" in rendered
