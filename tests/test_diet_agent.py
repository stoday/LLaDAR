import importlib.util
from pathlib import Path


MAIN_PATH = Path(__file__).parents[1] / "example_project" / "diet" / "main.py"
SPEC = importlib.util.spec_from_file_location("diet_main", MAIN_PATH)
diet_main = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(diet_main)


def test_diet_agent_answers_directly_with_knowledge_file(monkeypatch):
    captured = {}

    class FakeQuestionAnswerer:
        def __call__(self, *, prompt, info):
            captured["prompt"] = prompt
            captured["info"] = info
            return "早餐建議 400–500 大卡。"

    def fake_ask(**kwargs):
        captured["config"] = kwargs
        return FakeQuestionAnswerer()

    monkeypatch.setattr(diet_main.akasha, "ask", fake_ask)

    answer = diet_main.answer_question("早餐建議多少熱量？")

    assert answer == "早餐建議 400–500 大卡。"
    assert captured["info"] == diet_main.KNOWLEDGE_FILE
    assert "直接回答" in captured["config"]["system_prompt"]
