from pathlib import Path

import lladar


PAIR = {
    "complete_question": "完整問題",
    "complete_answer": "完整答案",
    "underspecified_question": "缺失問題",
    "missing_information": "缺失資訊",
    "invalid_assumptions": ["錯誤假設"],
    "acceptable_behaviors": ["ask_clarification"],
}


class CountingProvider:
    def __init__(self):
        self.calls = 0

    def generate_structured(self, prompt, *, model, temperature):
        self.calls += 1
        return PAIR


def test_random_select_limits_generation_to_n_pairs(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\ncc\ndd\nee\n", encoding="utf-8")
    provider = CountingProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2,
        overlap=0,
        random_select=2,
        provider=provider,
    )

    assert len(dataset) == 2
    assert provider.calls == 2
    assert len({item["id"] for item in dataset}) == 2


def test_random_select_larger_than_dataset_keeps_all_pairs(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("aa\nbb\n", encoding="utf-8")
    provider = CountingProvider()

    dataset = lladar.create_test_dataset(
        knowledge=knowledge,
        chunk_size=2,
        overlap=0,
        random_select=999,
        provider=provider,
    )

    assert len(dataset) == provider.calls
    assert len(dataset) > 0


def test_random_select_must_be_positive(tmp_path: Path):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("source", encoding="utf-8")

    try:
        lladar.create_test_dataset(
            knowledge=knowledge,
            random_select=0,
            provider=CountingProvider(),
        )
    except ValueError as error:
        assert "random_select" in str(error)
    else:
        raise AssertionError("random_select=0 should be rejected")
