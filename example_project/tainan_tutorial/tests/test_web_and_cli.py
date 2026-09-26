import json
from pathlib import Path

from fastapi.testclient import TestClient

from tainan_bias_demo.cli import run_cli
from tainan_bias_demo.web import create_app


PROJECT_ROOT = Path(__file__).parents[1]


def test_home_has_only_the_two_required_primary_actions_and_accessible_status(tmp_path: Path):
    client = TestClient(create_app(PROJECT_ROOT, runs_dir=tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert response.text.count('data-primary-action="true"') == 2
    assert "開始完整展示" in response.text
    assert "載入既有結果" in response.text
    assert 'aria-live="polite"' in response.text
    assert "待專家簽核" not in response.text


def test_health_endpoint_identifies_the_local_service(tmp_path: Path):
    client = TestClient(create_app(PROJECT_ROOT, runs_dir=tmp_path))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"service": "tainan-bias-demo", "status": "ok"}


def test_replay_api_runs_loads_reviews_and_exports_without_a_provider_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    client = TestClient(create_app(PROJECT_ROOT, runs_dir=tmp_path))

    created = client.post("/api/runs", json={"mode": "replay"})

    assert created.status_code == 201
    payload = created.json()
    run_id = payload["run_id"]
    assert payload["summary"]["fixed_principle_pass_rate"] == 1.0
    assert len(payload["results"]) == 10
    assert client.get(f"/api/runs/{run_id}").json()["replay_fingerprint"]
    assert client.get("/api/runs").json()[0]["run_id"] == run_id

    reviewed = client.post(
        f"/api/runs/{run_id}/reviews",
        json={
            "case_id": "S01",
            "status": "pass",
            "reviewer": "demo-host",
            "reason": "已審核服務紀錄確認。",
        },
    )
    assert reviewed.status_code == 200
    s01 = next(item for item in reviewed.json()["results"] if item["case"]["id"] == "S01")
    assert s01["verdict"]["framing_compliance"] == "fail"
    assert s01["final_review"]["status"] == "pass"

    json_export = client.get(f"/api/runs/{run_id}/export/json")
    html_export = client.get(f"/api/runs/{run_id}/export/html")
    assert json_export.status_code == 200
    assert json.loads(json_export.text)["run_id"] == run_id
    assert html_export.status_code == 200
    assert "固定題原則通過率" in html_export.text


def test_cli_replay_writes_artifacts_and_prints_both_metrics(tmp_path: Path, capsys):
    exit_code = run_cli(
        [
            "run",
            "--project-root",
            str(PROJECT_ROOT),
            "--output",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "固定題原則通過率" in captured.out
    assert "情境題框架偏差率" in captured.out
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(list(tmp_path.glob("*.html"))) == 1
