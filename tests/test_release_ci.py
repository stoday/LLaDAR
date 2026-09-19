"""Release gating must reject skipped live tests and stale main results."""

import pytest

from scripts.verify_release_ci import require_successful_jobs, require_successful_run


SHA = "a" * 40


def run(number, *, sha=SHA, conclusion="success", status="completed", branch="main", event="push"):
    return {"id": number, "run_number": number, "head_sha": sha,
            "head_branch": branch, "event": event, "status": status,
            "conclusion": conclusion}


def test_latest_main_run_for_exact_tagged_commit_must_pass():
    assert require_successful_run([run(1), run(2)], SHA)["id"] == 2
    with pytest.raises(RuntimeError, match="has not passed"):
        require_successful_run([run(1), run(2, conclusion="failure")], SHA)
    with pytest.raises(RuntimeError, match="has not passed"):
        require_successful_run([run(1), run(2, status="in_progress", conclusion=None)], SHA)
    with pytest.raises(RuntimeError, match="No main push CI run"):
        require_successful_run([run(1, sha="b" * 40), run(2, branch="feature")], SHA)


def test_release_requires_two_python_versions_and_actual_live_success():
    jobs = [{"name": "Test (Python 3.11)", "conclusion": "success"},
            {"name": "Test (Python 3.12)", "conclusion": "success"},
            {"name": "Live vibe-testing (Gemini)", "conclusion": "success"}]
    require_successful_jobs(jobs)
    jobs[-1]["conclusion"] = "skipped"
    with pytest.raises(RuntimeError, match="Live vibe-testing"):
        require_successful_jobs(jobs)