"""Fake MODEL for local integration fixtures, not a production answer parser.

Only the documented synthetic servers in tests use these fixed fields. Passing
these tests establishes host orchestration, not a real model's fidelity.
"""
import json
import io
import sys
from contextlib import contextmanager
from unittest.mock import patch
from lladar.answer_extraction import ExtractionOptions


class FixtureTerminal(io.StringIO):
    """External interactive terminal fixture; never used by production."""

    def isatty(self):
        return True


@contextmanager
def browser_cli_terminal():
    """Model the CLI's external terminal streams without disabling test capture."""
    with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True):
        yield


def fixture_answer(request):
    def field(value):
        if isinstance(value, dict):
            if "final_text" in value:
                return value["final_text"]
            if "answer" in value:
                return value["answer"]
            if "data" in value:
                return field(value["data"])
            if "container" in value:
                return "".join(json.loads(value["container"])["fragments"])
        return None

    body = request["body"]
    media = request["content_type"].split(";")[0]
    if media == "text/plain":
        return body
    if media == "text/event-stream":
        values = [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]
        answers = [field(value) for value in values if field(value) is not None]
        return answers[-1] if answers else None
    if "ndjson" in media:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
        answers = [field(row) for row in rows if row.get("type") == "final" or "container" in row]
        return answers[0] if answers else None
    return field(json.loads(body))


class FixtureProvider:
    def __init__(self, **_):
        self.calls = []

    def extract(self, request, **_):
        self.calls.append(request)
        try:
            answer = fixture_answer(request)
        except (ValueError, TypeError, KeyError):
            answer = None
        return {"result": {"status": "final", "text": answer} if isinstance(answer, str) and answer
                else {"status": "invalid"}, "usage": None}


def fixture_options():
    return ExtractionOptions(provider_factory=FixtureProvider, transfer_approved=True)


def approve(prompt):
    if prompt.startswith("Type MATCH"):
        return "MATCH"
    return "YES"
