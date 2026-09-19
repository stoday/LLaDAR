"""Stream protocol checks; no provider calls or simulated LLM answers."""
import json

import pytest

from lladar.auto_adapter import _stream_answer


def test_only_answer_chunks_enter_proposal_json():
    events = iter([
        {"type": "thinking", "data": '{"harness":"wrong"}'},
        {"type": "answer", "data": '{"harness":'},
        {"type": "tool", "data": {"content": "tool output"}},
        {"type": "answer", "data": '"adapter.py", "blockers":[]}'},
    ])
    assert json.loads(_stream_answer(events)) == {
        "harness": "adapter.py", "blockers": []}


def test_plain_text_chunks_are_preserved():
    assert _stream_answer(iter(['{"candidates":', '[]}'])) == '{"candidates":[]}'


def test_lazy_stream_is_fully_consumed_under_stdout_redirect(capsys):
    from contextlib import redirect_stdout
    import sys

    def events():
        print("thinking summary")
        yield {"type": "thinking", "data": "summary"}
        yield {"type": "answer", "data": "{}"}
        print("tool result")

    with redirect_stdout(sys.stderr):
        assert _stream_answer(events()) == "{}"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "thinking summary\ntool result\n"


def test_stream_exception_does_not_return_partial_answer():
    def events():
        yield {"type": "answer", "data": '{"harness":'}
        raise ConnectionError("stream interrupted")

    with pytest.raises(ConnectionError, match="interrupted"):
        _stream_answer(events())


@pytest.mark.parametrize("events, error, message", [
    ([], ValueError, "no answer"),
    ([{"type": "thinking", "data": "summary"}], ValueError, "no answer"),
    ([{"type": "answer", "data": {}}], ValueError, "strings"),
    ([None], ValueError, "Unsupported"),
    ([{"type": "answer", "data": "{}"},
      {"type": "error", "data": "stream interrupted"}], RuntimeError, "interrupted"),
])
def test_invalid_stream_fails_explicitly(events, error, message):
    with pytest.raises(error, match=message):
        _stream_answer(iter(events))