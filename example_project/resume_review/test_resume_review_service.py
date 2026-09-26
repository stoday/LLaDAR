from fastapi.testclient import TestClient

import resume_review_service as service


PAYLOAD = {
    "job": {"title": "Python 工程師", "required_skills": ["Python", "SQL"], "minimum_years": 2},
    "candidates": [
        {"id": "A", "skills": ["Python", "SQL"], "years_experience": 3, "age": 32},
        {"id": "B", "skills": ["Python", "SQL"], "years_experience": 3, "age": 48},
    ],
}


def test_equal_qualifications_get_equal_evidence_without_age_in_model_input(monkeypatch):
    import akasha

    prompts = []

    def fake_agent(**kwargs):
        assert "年齡" in kwargs["system_prompt"]

        def answer(prompt):
            prompts.append(prompt)
            return "符合所列技能與年資，交由人工審閱。"

        return answer

    monkeypatch.setattr(akasha, "agents", fake_agent)
    response = TestClient(service.app).post("/review", json=PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["ranked"] is False
    first, second = body["reviews"]
    assert first["matched_skills"] == second["matched_skills"] == ["Python", "SQL"]
    assert first["decision"] == second["decision"] == "human_review_required"
    assert prompts[0] == prompts[1]
    assert '"age"' not in prompts[0]


def test_audit_reports_age_risk_without_claiming_discrimination():
    response = TestClient(service.app).post("/bias-audit", json=PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["historical_high_performers"] == {"under_40": 9, "40_or_over": 1}
    assert body["scenario_applicants"] == {"under_40": 5, "40_or_over": 5}
    assert body["submitted_applicants"] == {"under_40": 1, "40_or_over": 1, "age_unknown": 0}
    assert "不能證明" in body["assessment"]
