from __future__ import annotations

import pytest

from lladar.validation_retry import run_validated


def test_retry_prompt_contains_complete_failure_history() -> None:
    prompts: list[str] = []
    values = ["first", "second", "valid"]

    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return values.pop(0)

    def validate(value: str) -> str:
        if value != "valid":
            raise ValueError(f"rejected {value}")
        return value

    assert run_validated("ORIGINAL FACTS", generate, validate) == "valid"
    assert len(prompts) == 3
    assert "rejected first" in prompts[1]
    assert "rejected first" in prompts[2]
    assert "rejected second" in prompts[2]
    assert all("ORIGINAL FACTS" in prompt for prompt in prompts)


def test_retry_uses_caller_validator_and_stops_at_limit() -> None:
    failures = []

    def generate(prompt: str) -> object:
        return {"wrong": True}

    def validate(value: object) -> object:
        raise TypeError("caller contract failed")

    with pytest.raises(TypeError, match="caller contract failed"):
        run_validated(
            "task",
            generate,
            validate,
            attempts=2,
            retry_on=(TypeError,),
            on_failure=failures.append,
        )

    assert [failure["attempt"] for failure in failures] == [1, 2]
    assert all(failure["error_type"] == "TypeError" for failure in failures)