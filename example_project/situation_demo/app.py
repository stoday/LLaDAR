"""Tiny stateful local application used by the situation example."""
from __future__ import annotations

import re


class OrderAssistant:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.calibration_token: str | None = None

    def chat(self, message: str) -> str:
        self.messages.append(message)
        if "Calibration: remember this token for this session:" in message:
            self.calibration_token = message.split("session:", 1)[1].split(".", 1)[0].strip()
            return "READY"
        if "What exact calibration token" in message:
            return self.calibration_token or "I do not have a token in this session."
        if any(term in message.lower() for term in ("order", "\u8a02\u55ae", "\u5305\u88f9")):
            if re.search(r"(?:#|order\s*(?:id|no\.?))\s*[:#]?\s*[A-Za-z0-9-]{4,}", message, re.I):
                return "I cannot check live order status. Please use the official order page."
            return "Please provide an order number. I cannot confirm status without looking it up."
        return "Please describe your order question and provide an order number for a status lookup."
