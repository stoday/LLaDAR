"""Supported benchmark scoring rule contracts shared by import and evaluation."""
from __future__ import annotations

from typing import Any


_EXACT_METHODS = {"exact_option", "exact_option_set", "exact_order", "exact_text"}
_TOKEN_FIELDS = {"positive_tokens", "negative_tokens", "positive_label",
                 "negative_label", "neutral_label"}


def validate_rule_parameters(rule: dict[str, Any]) -> None:
    method = rule.get("method")
    parameters = rule.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("rule parameters must be an object")
    if method in _EXACT_METHODS:
        if parameters:
            raise ValueError(f"{method} does not support scoring parameters")
        return
    if method == "token_balance":
        if set(parameters) != _TOKEN_FIELDS:
            raise ValueError("token_balance requires exactly its two token sets and three labels")
        for key in ("positive_tokens", "negative_tokens"):
            tokens = parameters[key]
            if not isinstance(tokens, list) or not tokens or any(
                    not isinstance(token, str) or not token for token in tokens):
                raise ValueError(f"token_balance requires nonempty {key}")
        for key in ("positive_label", "negative_label", "neutral_label"):
            if not isinstance(parameters[key], str) or not parameters[key]:
                raise ValueError(f"token_balance requires {key}")
        return
    if method == "rubric_judge":
        if set(parameters) != {"rubric"} or not isinstance(parameters["rubric"], str) \
                or not parameters["rubric"].strip():
            raise ValueError("rubric_judge requires exactly one nonempty rubric")
        return
    raise ValueError(f"unimplemented scoring method {method}")
