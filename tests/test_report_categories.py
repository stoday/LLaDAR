"""Reports retain categorical measurements and expose saved answer differences."""
import json

from lladar.reporting import create_report
from test_simple_pipeline import ReportSkillAgent


def evaluation_fixture(categories, dimension="answer_quality"):
    distribution = {category: categories.count(category) for category in categories}
    return {
        "source": "responses.jsonl", "trials_source": None, "skill": {},
        "evaluator_model": "fixture", "plan": {"dimensions": [
            {"name": dimension, "kind": "categorical", "description": "Saved quality"}]},
        "summary": {"evaluated": len(categories)},
        "aggregates": {dimension: {"kind": "categorical", "description": "Saved quality",
                                    "count": len(categories), "missing": 0, "distribution": distribution}},
        "stability": {"records": []},
        "items": [{"record_index": index, "trial": 1, "status": "evaluated",
                   "question": "Question", "expected_answer": "Expected | fact",
                   "actual_response": "Actual\nanswer", "reason": "Saved reason",
                   "values": {dimension: category}}
                  for index, category in enumerate(categories, 1)],
    }


def test_report_shows_zero_categories_and_saved_difference_evidence(tmp_path):
    source = tmp_path / "evaluation.json"
    source.write_text(json.dumps(evaluation_fixture(["完全符合", "符合且有補充", "部分符合"])), encoding="utf-8")
    original = source.read_bytes()
    output = create_report(source, tmp_path / "report.md", lang="zh-TW",
                           skill_agent_factory=ReportSkillAgent, verbose=False)
    report = output.read_text(encoding="utf-8")
    assert "| 完全符合 | 1 | 與預期一致 |" in report
    assert "| 符合且有補充 | 1 | 有補充：多出內容，需核對 |" in report
    assert "| 部分符合 | 1 | 部分符合：有必要內容缺漏，需核對 |" in report
    assert "| 不符合 | 0 |" in report
    assert "| 無法判定 | 0 |" in report
    details = report.split("## 與預期答案的差異明細")[1].split("## 限制")[0]
    assert "| 1 | 1 |" not in details
    assert "| 2 | 1 | 符合且有補充 |" in details
    assert "| 3 | 1 | 部分符合 |" in details
    assert "Expected \\| fact" in details and "Saved reason" in details
    assert source.read_bytes() == original


def test_report_keeps_generic_categories_without_answer_quality_policy(tmp_path):
    source = tmp_path / "evaluation.json"
    source.write_text(json.dumps(evaluation_fixture(["a|b", "other"], "custom")), encoding="utf-8")
    output = create_report(source, tmp_path / "report.md", skill_agent_factory=ReportSkillAgent, verbose=False)
    report = output.read_text(encoding="utf-8")
    assert "| a\\|b | 1 |" in report
    assert "Differences from expected answers" not in report


def test_report_includes_nonmatching_and_indeterminate_trials(tmp_path):
    source = tmp_path / "evaluation.json"
    source.write_text(json.dumps(evaluation_fixture(["不符合", "無法判定"])), encoding="utf-8")
    output = create_report(source, tmp_path / "report.md", lang="zh-TW",
                           skill_agent_factory=ReportSkillAgent, verbose=False)
    details = output.read_text(encoding="utf-8").split("## 與預期答案的差異明細")[1]
    assert "| 1 | 1 | 不符合 |" in details
    assert "| 2 | 1 | 無法判定 |" in details
