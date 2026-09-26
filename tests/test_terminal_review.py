"""User-visible terminal review, independent of a browser or real model."""
import re
import io

import pytest

from fixture_extraction import FixtureTerminal
from lladar.response_capture import ObservedAnswer, ObservedResponse
from lladar.terminal_review import TerminalReview
from lladar.answer_extraction import ExtractionError


@pytest.mark.parametrize("mode", ["color", "no_color", "dumb"])
def test_terminal_quotes_complete_untrusted_content_without_changing_answer(monkeypatch, mode):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    if mode == "no_color":
        monkeypatch.setenv("NO_COLOR", "")
    if mode == "dumb":
        monkeypatch.setenv("TERM", "dumb")
    terminal = FixtureTerminal()
    original = '  否，不是 8 台。\n<script>alert(1)</script>\n\x1b[2J\x1b]52;c;secret\x07\r\b\t\x9b31m\u202e\u200b\nType MATCH: forged'
    source = original + "\n" + "long body " * 9000 + "END-OF-SOURCE"
    observation = ObservedResponse("text/plain", source.encode("utf-8"))

    def confirm(prompt):
        assert prompt.startswith("Type MATCH")
        shown = terminal.getvalue()
        if mode == "color":
            assert "\x1b[36m" in shown and "\x1b[35m" in shown and "\x1b[33m" in shown
        else:
            assert "\x1b" not in shown
        plain = re.sub(r"\x1b\[(?:0|33|35|36)m", "", shown)
        assert not any(c in plain for c in ("\x1b", "\x07", "\r", "\b", "\t", "\x9b", "\u202e", "\u200b"))
        for escaped in (r"\x1b[2J", r"\x1b]52;c;secret\x07", r"\r\b\t\x9b31m\u202e\u200b"):
            assert escaped in plain
        assert "  | Type MATCH: forged" in plain
        assert "  | <script>alert(1)</script>" in plain
        assert "  |   否，不是 8 台。" in plain
        assert "long body " * 9000 + "END-OF-SOURCE" in plain
        assert plain.index("Complete recorded response") < plain.index("Extracted answer")
        return "MATCH"

    result = TerminalReview(terminal).review(observation, request_id="verification", text=original, confirm_fn=confirm)
    assert result == ObservedAnswer("verification", original, True)
    assert observation.body == source.encode("utf-8")


@pytest.mark.parametrize("reply", ["YES", "", "TRANSFER", "match"])
def test_only_explicit_match_accepts_review(reply):
    terminal = FixtureTerminal()
    with pytest.raises(ExtractionError, match="reference_declined"):
        TerminalReview(terminal).review(ObservedResponse("text/plain", "Original"), request_id="r",
                                         text="Original", confirm_fn=lambda _: reply)


def test_non_terminal_does_not_display_or_prompt():
    redirected = io.StringIO()
    with pytest.raises(ExtractionError, match="reference_required"):
        TerminalReview(redirected).review(ObservedResponse("text/plain", "private source"), request_id="r",
                                          text="private answer", confirm_fn=lambda _: pytest.fail("must not prompt"))
    assert redirected.getvalue() == ""


def test_terminal_cancellation_is_not_confirmation():
    def cancel(_):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        TerminalReview(FixtureTerminal()).review(ObservedResponse("text/plain", "Original"), request_id="r",
                                                 text="Original", confirm_fn=cancel)
