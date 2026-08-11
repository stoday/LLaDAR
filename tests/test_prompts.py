from lladar.prompts import build_generation_prompt


def test_generation_prompt_requires_source_language_for_natural_language_fields():
    prompt = build_generation_prompt("家庭成員稱呼父親為爸爸。", "找出一個重要事實。")

    assert "dominant natural language" in prompt
    assert "same language as the source" in prompt
    assert "Do not translate" in prompt
    assert "acceptable_behaviors" in prompt


def test_generation_prompt_keeps_behavior_values_as_machine_readable_tokens():
    prompt = build_generation_prompt("家庭成員稱呼父親為爸爸。", "找出一個重要事實。")

    assert "ask_clarification" in prompt
    assert "list_possibilities" in prompt
    assert "state_insufficient_information" in prompt


def test_resolve_strategy_labels_inline_and_file_sources():
    from lladar.prompts import resolve_strategy

    assert resolve_strategy("short instruction") == (
        "custom-short instruction",
        "short instruction",
    )
    assert resolve_strategy("file contents", prompt_file="prompts/strategy.md") == (
        "custom-prompts/strategy.md",
        "file contents",
    )
