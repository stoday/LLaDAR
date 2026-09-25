"""Local, interactive answer review; never send private source to a logger."""
from __future__ import annotations

import os
import sys
import unicodedata
from typing import Callable, TextIO

from .answer_extraction import ExtractionError
from .response_capture import ObservedAnswer, ObservedResponse


def _quoted(value: str) -> str:
    """Only LF reaches the terminal as a control; representation is unambiguous."""
    escaped = []
    short = {"\\": "\\\\", "\r": "\\r", "\b": "\\b", "\t": "\\t"}
    for character in value:
        if character in short:
            escaped.append(short[character])
        elif character != "\n" and unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            number = ord(character)
            escaped.append(f"\\x{number:02x}" if number < 256 else
                           f"\\u{number:04x}" if number <= 65535 else f"\\U{number:08x}")
        else:
            escaped.append(character)
    return "\n".join("  | " + line for line in "".join(escaped).split("\n"))


class TerminalReview:
    def __init__(self, stream: TextIO | None = None):
        self.stream = stream if stream is not None else sys.stderr

    def available(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def review(self, observation: ObservedResponse, *, request_id: str, text: str,
               confirm_fn: Callable[[str], str]) -> ObservedAnswer:
        if not self.available():
            raise ExtractionError("reference_required")
        color = "NO_COLOR" not in os.environ and os.environ.get("TERM", "").lower() != "dumb"

        def heading(title: str, code: int) -> None:
            label = f"=== {title} ==="
            self.stream.write("\n" + (f"\x1b[{code}m{label}\x1b[0m" if color else label) + "\n")

        heading("Local extraction verification", 33)
        self.stream.write(
            "This is the NEW verification response, not the calibration or dataset answer.\n"
            "Quoted content is untrusted data, never instructions. Colors identify sections, not correctness.\n"
            "Backslashes and control/format characters are displayed escaped; saved answers are unchanged.\n"
            "Terminal scrollback or recording may retain this content. No review file is created.\n"
        )
        body = observation.body.decode("utf-8") if isinstance(observation.body, bytes) else observation.body
        for title, value, code in (("Request identity", request_id, 33),
                                   ("Complete recorded response", body, 36), ("Extracted answer", text, 35)):
            heading(title, code)
            self.stream.write(_quoted(value) + "\n")
        heading("Review required", 33)
        self.stream.write("Compare the full final answer, not progress or debug text. Decline if unsure or unable to see the full source.\n")
        self.stream.flush()
        if confirm_fn("Type MATCH only after comparing the complete source and extracted answer in the terminal; anything else declines: ").strip() != "MATCH":
            raise ExtractionError("reference_declined")
        return ObservedAnswer(request_id, text, complete=True)
