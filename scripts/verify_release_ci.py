"""Require a successful main push CI run before publishing a version tag."""
from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUIRED_JOBS = {
    "Test (Python 3.11)",
    "Test (Python 3.12)",
    "Live vibe-testing (Gemini)",
}


def require_successful_run(runs: list[dict], commit: str) -> dict:
    matching = [run for run in runs if run.get("head_sha") == commit
                and run.get("head_branch") == "main" and run.get("event") == "push"]
    if not matching:
        raise RuntimeError("No main push CI run exists for the tagged commit")
    latest = max(matching, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise RuntimeError("The latest main push CI run for the tagged commit has not passed")
    return latest


def require_successful_jobs(jobs: list[dict]) -> None:
    passed = {job.get("name") for job in jobs if job.get("conclusion") == "success"}
    missing = REQUIRED_JOBS - passed
    if missing:
        raise RuntimeError("Required main CI jobs did not pass: " + ", ".join(sorted(missing)))


def github_json(path: str, params: dict[str, str]) -> dict:
    query = urlencode(params)
    url = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}?{query}"
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "User-Agent": "lladar-release-gate",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> None:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    main_head = subprocess.check_output(
        ["git", "rev-parse", "refs/remotes/origin/main"], text=True).strip()
    if commit != main_head:
        raise RuntimeError("Version tag must point to the current origin/main commit")

    data = github_json("actions/workflows/release.yml/runs", {
        "branch": "main", "event": "push", "head_sha": commit, "per_page": "100",
    })
    run = require_successful_run(data["workflow_runs"], commit)
    jobs = github_json(f"actions/runs/{run['id']}/jobs", {
        "filter": "latest", "per_page": "100",
    })
    require_successful_jobs(jobs["jobs"])
    print(f"Tagged commit passed main CI: {run['html_url']}")


if __name__ == "__main__":
    main()