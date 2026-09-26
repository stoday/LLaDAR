"""Require a successful main push CI run before publishing a version tag."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUIRED_JOBS = {
    "Test (Python 3.11)",
    "Test (Python 3.12)",
    "Live vibe-testing (Gemini)",
}
WAIT_TIMEOUT_SECONDS = 35 * 60
POLL_INTERVAL_SECONDS = 15


def _latest_main_push_run(runs: list[dict], commit: str) -> dict | None:
    matching = [run for run in runs if run.get("head_sha") == commit
                and run.get("head_branch") == "main" and run.get("event") == "push"]
    if matching:
        return max(matching, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
    return None


def require_successful_run(runs: list[dict], commit: str) -> dict:
    latest = _latest_main_push_run(runs, commit)
    if latest is None:
        raise RuntimeError("No main push CI run exists for the tagged commit")
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise RuntimeError("The latest main push CI run for the tagged commit has not passed")
    return latest


def wait_for_successful_run(
    fetch_runs: Callable[[], list[dict]],
    commit: str,
    *,
    timeout_seconds: float = WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    deadline = monotonic() + timeout_seconds
    while True:
        runs = fetch_runs()
        latest = _latest_main_push_run(runs, commit)
        if latest is not None:
            if latest.get("status") == "completed":
                return require_successful_run(runs, commit)
            state = latest.get("status") or "unknown"
        else:
            state = "not visible yet"

        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "Timed out waiting for the main push CI run for the tagged commit "
                f"(last state: {state})"
            )
        delay = min(poll_interval_seconds, remaining)
        print(f"Main push CI is {state}; retrying in {delay:g} seconds", flush=True)
        sleep(delay)


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

    def fetch_runs() -> list[dict]:
        data = github_json("actions/workflows/release.yml/runs", {
            "branch": "main", "event": "push", "head_sha": commit, "per_page": "100",
        })
        return data["workflow_runs"]

    run = wait_for_successful_run(fetch_runs, commit)
    jobs = github_json(f"actions/runs/{run['id']}/jobs", {
        "filter": "latest", "per_page": "100",
    })
    require_successful_jobs(jobs["jobs"])
    print(f"Tagged commit passed main CI: {run['html_url']}")


if __name__ == "__main__":
    main()
