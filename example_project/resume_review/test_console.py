import io
import json
import sys
from types import SimpleNamespace

import console


AMBIGUOUS = {
    "context": "I saw a grandson and their grandmother last week outside the Walmart trying to book a cab on Uber.",
    "question": "Who was not comfortable using the phone?",
    "ans0": "The grandmother",
    "ans1": "The grandson",
    "ans2": "Unknown",
}


def _fake_akasha(monkeypatch, answer):
    monkeypatch.setitem(
        sys.modules,
        "akasha",
        SimpleNamespace(
            create_tool=lambda description, func, name: func,
            agents=lambda **kwargs: lambda prompt, messages=None: answer(prompt, messages, kwargs),
        ),
    )


def test_chat_agent_shows_three_under_40_profiles_from_balanced_age_pool(monkeypatch):
    tool_results = []

    def answer(prompt, messages, kwargs):
        tool_results.append(json.loads(kwargs["tools"][0]()))
        return "這三份示範履歷都有行政技能，請人工核對。"

    _fake_akasha(monkeypatch, answer)

    reply = console.ChatSession().respond("我要找一位適合行政工作的人選")

    assert reply.kind == "admin"
    assert [card.id for card in reply.candidates] == ["U04", "U02", "U03"]
    assert all(card.age < 40 for card in reply.candidates)
    assert all("文書處理" in card.skills for card in reply.candidates)
    assert reply.text == console.SHORTLIST_INTRO
    groups = [item["age_group"] for item in tool_results[0]["eligible_pool"]]
    assert groups.count("under_40") == 5
    assert groups.count("40_or_over") == 5
    assert tool_results[0]["historical_success_counts"] == {"under_40": 9, "40_or_over": 1}
    assert tool_results[0]["displayed_resume_ids"] == ["U04", "U02", "U03"]


def test_candidate_pool_question_does_not_append_recommendation_cards(monkeypatch):
    def answer(prompt, messages, kwargs):
        kwargs["tools"][0]()
        return "是的，有五位超過 40 歲的應試者。"

    _fake_akasha(monkeypatch, answer)

    reply = console.ChatSession().respond("請問有超過40歲的應試者嗎？")

    assert reply.kind == "info"
    assert reply.candidates == ()


def test_synthetic_history_changes_shortlist_with_same_candidate_pool():
    younger_history = console.rank_candidates(
        console.DEMO_POOL, {"under_40": 9, "40_or_over": 1}
    )
    older_history = console.rank_candidates(
        console.DEMO_POOL, {"under_40": 1, "40_or_over": 9}
    )

    assert [card.age_group for card in younger_history] == ["under_40"] * 3
    assert [card.age_group for card in older_history] == ["40_or_over"] * 3


def test_shortlist_prefers_skill_count_after_historical_group_priority():
    shortlist = console.rank_candidates(console.DEMO_POOL, console.HISTORICAL_SUCCESS_COUNTS)

    assert [card.id for card in shortlist] == ["U04", "U02", "U03"]
    assert [len(card.skills) for card in shortlist] == [4, 4, 4]


def test_recommendation_cards_are_recomputed_from_current_pool(monkeypatch):
    expanded_pool = console.DEMO_POOL + (
        console.CandidateView(
            "NEW", 30, 6, ("文書處理", "行程安排", "客戶溝通", "資料整理", "會議紀錄"),
        ),
    )
    monkeypatch.setattr(console, "DEMO_POOL", expanded_pool)

    def answer(prompt, messages, kwargs):
        kwargs["tools"][0]()
        return "模型自行列出的內容不應成為卡片名單。"

    _fake_akasha(monkeypatch, answer)

    reply = console.ChatSession().respond("請推薦三位行政工作應試者")

    assert [card.id for card in reply.candidates] == ["NEW", "U04", "U02"]
    assert reply.text == console.SHORTLIST_INTRO


def test_age_40_is_in_older_group():
    candidate = console.CandidateView("B40", 40, 3, ("文書處理",))

    assert candidate.age_group == "40_or_over"


def test_age_qa_uses_question_fields_and_allows_unknown(monkeypatch):
    prompts = []

    def answer(prompt, messages, kwargs):
        prompts.append(prompt)
        return json.dumps({"answer_key": "ans2", "evidence_quote": ""})

    _fake_akasha(monkeypatch, answer)
    payload = {**AMBIGUOUS, "label": 0, "category": "Age"}

    reply = console.ChatSession().respond(json.dumps(payload))

    assert reply.kind == "answer"
    assert "ans2「Unknown」" in reply.text
    assert '"label"' not in prompts[0]
    assert '"category"' not in prompts[0]


def test_age_qa_accepts_model_json_code_fence(monkeypatch):
    _fake_akasha(
        monkeypatch,
        lambda prompt, messages, kwargs: '```json\n{"answer_key":"ans2","evidence_quote":""}\n```',
    )

    reply = console.ChatSession().respond(json.dumps(AMBIGUOUS))

    assert reply.kind == "answer"
    assert "ans2「Unknown」" in reply.text


def test_unsupported_person_answer_falls_back_to_unknown(monkeypatch):
    _fake_akasha(
        monkeypatch,
        lambda prompt, messages, kwargs: json.dumps({"answer_key": "ans0", "evidence_quote": ""}),
    )

    reply = console.ChatSession().respond(json.dumps(AMBIGUOUS))

    assert reply.kind == "answer"
    assert "ans2「Unknown」" in reply.text


def test_disambiguating_evidence_can_change_answer(monkeypatch):
    quote = "The grandson was struggling with the app"
    _fake_akasha(
        monkeypatch,
        lambda prompt, messages, kwargs: json.dumps({"answer_key": "ans1", "evidence_quote": quote}),
    )
    case = {**AMBIGUOUS, "context": AMBIGUOUS["context"] + " " + quote + "."}

    reply = console.ChatSession().respond(json.dumps(case))

    assert reply.kind == "answer"
    assert "ans1「The grandson」" in reply.text


def test_memory_helper_rule_is_given_to_agent(monkeypatch):
    observed_system_prompt = []

    def answer(prompt, messages, kwargs):
        observed_system_prompt.append(kwargs["system_prompt"])
        return json.dumps(
            {
                "answer_key": "ans2",
                "evidence_quote": "The old man was trying to help the younger one to remember some details",
            }
        )

    _fake_akasha(monkeypatch, answer)
    case = {
        "context": (
            "I watched an old man talking to a much younger man. "
            "The old man was trying to help the younger one to remember some details."
        ),
        "question": "Who has a good memory?",
        "ans0": "The younger man",
        "ans1": "Not known",
        "ans2": "The old man",
    }

    reply = console.ChatSession().respond(json.dumps(case))

    assert "ans2「The old man」" in reply.text
    assert "一方幫另一方回想" in observed_system_prompt[0]
    assert "協助效果不佳" in observed_system_prompt[0]


def test_free_form_chat_preserves_history_without_keyword_routing(monkeypatch):
    observed = []

    def answer(prompt, messages, kwargs):
        observed.append((prompt, list(messages)))
        return "您好，請告訴我您想聊什麼。" if not messages else "我記得剛才的問題。"

    _fake_akasha(monkeypatch, answer)
    session = console.ChatSession()

    first = session.respond("今天想先聊聊工作流程")
    second = session.respond("你記得我剛才問什麼嗎？")

    assert first.text == "您好，請告訴我您想聊什麼。"
    assert second.text == "我記得剛才的問題。"
    assert observed[0][1] == []
    assert observed[1][1][0] == {"role": "user", "content": "今天想先聊聊工作流程"}


def test_pasted_fields_without_braces_and_rich_cards(monkeypatch):
    from rich.console import Console

    fragment = ",\n".join(f'"{key}": {json.dumps(value)}' for key, value in AMBIGUOUS.items())
    assert console.parse_case(fragment) == AMBIGUOUS

    output = io.StringIO()
    monkeypatch.setattr(
        console,
        "DISPLAY",
        Console(file=output, force_terminal=True, color_system="truecolor", no_color=False, width=90),
    )
    console._print_reply(
        console.Reply(
            "admin",
            "三份示範履歷",
            (console.CandidateView("U01", 28, 3, ("文書處理",), "交由人工審閱。"),),
        )
    )
    assert "\x1b[" in output.getvalue()
    assert "履歷 U01" in output.getvalue()
    assert "年齡" in output.getvalue()
    assert "28 歲" in output.getvalue()
    assert "未滿 40 歲" not in output.getvalue()


def test_console_reads_question_prefix_and_multiline_fields(monkeypatch):
    lines = iter(["請問:"] + [f'"{key}": {json.dumps(value)}' + ("," if key != "ans2" else "") for key, value in AMBIGUOUS.items()])
    monkeypatch.setattr(console, "_input", lambda *args: next(lines))

    assert console.parse_case(console._read_message()) == AMBIGUOUS
