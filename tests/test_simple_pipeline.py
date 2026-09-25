from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lladar.cli import build_parser, main
from lladar.browser_target import BrowserConfirmationRequired
from lladar.answer_extraction import ExtractionError
from lladar.evaluation import evaluate
from lladar.records import read_records
from lladar.reporting import create_report
from lladar.runner import run_agent
from fixture_extraction import browser_cli_terminal


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class StrategySkillAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, _request):
        self.tools["read_dataset"]()
        self.tools["write_strategy"](
            "def select_cases(cases, schedule):\n"
            "    for case in cases:\n"
            "        schedule(case, repeats=3)\n"
        )
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "test-skill"}}


class RandomSampleSkillAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, _request):
        self.tools["read_dataset"]()
        self.tools["write_strategy"](
            "def select_cases(cases, schedule):\n"
            "    for case in random.sample(cases, min(2, len(cases))):\n"
            "        schedule(case)\n"
        )
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "test-skill"}}


class EvaluationSkillAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, request):
        if request["stage"] == "plan":
            self.tools["submit_plan"]({
                "title": "Correctness", "approach": "Compare answers.",
                "dimensions": [{"name": "correct", "description": "Correct answer", "kind": "boolean"}],
                "limitations": ["Fixture evidence only."],
            })
        else:
            self.tools["submit_judgment"]({
                "values": {"correct": request["actual_response"] == request["expected_answer"]},
                "reason": "Compared the two answers.",
            })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "test-skill"}}


class ReportSkillAgent:
    def __init__(self, *, skills, tools, **_options) -> None:
        self.name, self.tools = Path(skills[0]).name, tools

    def __call__(self, _request):
        self.tools["submit_report"]({
            "overview": "This report uses the saved evaluation evidence.",
            "findings": "The deterministic tables contain the measured outcomes.",
            "limitations": "Results are limited to the supplied records.",
        })
        return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "test-skill"}}


class BrowserFixtureTarget:
    evidence = {
        "mode": "browser",
        "origin": "https://example.test",
        "response_protocol": "text/event-stream",
        "response_plan_sha256": "fixture-plan",
    }

    def prepare(self, _probes, *, interactive, record_count, request_count):
        return None

    def answer(self, question: str, request_id: str) -> str:
        return f"browser:{question}"

    def close(self) -> None:
        return None


class BlockingBrowserFixtureTarget:
    def __init__(self) -> None:
        self.evidence = {
            "mode": "browser",
            "origin": "https://example.test",
            "path_shape": "/api/{opaque}/ask",
            "status": "confirmation_required",
        }

    def prepare(self, _probes, *, interactive, record_count, request_count):
        raise BrowserConfirmationRequired("fixture decline")

    def close(self) -> None:
        return None


def make_dataset(path: Path, count: int = 2) -> None:
    write_jsonl(path, [
        {"question": f"question-{index}", "expected_answer": f"answer-{index}", "actual_response": None}
        for index in range(1, count + 1)
    ])


def test_cli_exposes_local_skills_and_rejects_removed_prompt_options():
    commands = build_parser()._subparsers._group_actions[0].choices
    runner, evaluation, report = commands["run-agent"], commands["eval"], commands["report"]
    assert {action.dest for action in runner._actions} >= {"skill", "seed"}
    assert "prompt" not in {action.dest for action in runner._actions}
    assert "max_cases" not in {action.dest for action in runner._actions}
    assert {action.dest for action in evaluation._actions} >= {"skill"}
    assert "prompt" not in {action.dest for action in evaluation._actions}
    assert "prompt_file" not in {action.dest for action in evaluation._actions}
    assert {action.dest for action in report._actions} >= {"skill"}
    for argv in (
        ["run-agent", "dataset.jsonl", "--prompt", "old"],
        ["run-agent", "dataset.jsonl", "--max-cases", "1"],
        ["eval", "responses.jsonl", "--prompt", "old"],
        ["eval", "responses.jsonl", "--prompt-file", "old.md"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(argv)
    help_text = re.sub(r"\s+", " ", runner.format_help())
    assert "local method directory containing SKILL.md" in help_text
    assert "Deterministic random selection seed" in help_text


def test_run_agent_cli_accepts_one_project_or_browser_target():
    parser = build_parser()

    browser = parser.parse_args([
        "run-agent", "dataset.jsonl", "--page-url", "https://example.test/chat",
        "--confirm-browser-run", "--fresh-browser-profile",
    ])

    assert browser.page_url == "https://example.test/chat" and browser.project is None
    assert browser.confirm_browser_run is True and browser.interactive is None
    assert browser.fresh_browser_profile is True
    with pytest.raises(SystemExit):
        parser.parse_args([
            "run-agent", "dataset.jsonl", "--project", ".",
            "--page-url", "https://example.test/chat",
        ])


def test_run_agent_help_explains_browser_setup_handoffs_and_artifacts(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["run-agent", "--help"])

    assert raised.value.code == 0
    help_text = re.sub(r"\s+", " ", capsys.readouterr().out)
    for explanation in (
        "python -m playwright install chromium",
        "exact calibration question",
        "401/403",
        "raw streams",
        "--service-url does not discover an arbitrary API",
        "3600 seconds / 60 minutes",
        "300 seconds (5 minutes)",
        "--timeout controls website requests, not navigation",
        "--allow-response-model-transfer",
        "real response content",
        "gemini:gemini-3.8-flash",
        "N+2 model calls",
        "Approval flags do not skip review",
        "One YES approves BOTH",
        "color terminal review (stderr)",
        "NO_COLOR",
        "redirected review output is refused",
        "Preapprove only the browser verification",
        "project mode only",
        "requires terminal stdin and stderr",
        "stops before opening the browser",
    ):
        assert explanation in help_text


@pytest.mark.parametrize("entry", ["cli", "api"])
@pytest.mark.parametrize("override", [None, 17])
def test_agent_wait_defaults_to_one_hour_and_still_allows_a_shorter_timeout(tmp_path, entry, override):
    dataset, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(dataset, count=1)
    configured_timeouts = []

    def target_factory(**options):
        configured_timeouts.append(options["timeout"])
        return BrowserFixtureTarget()

    if entry == "cli":
        argv = ["run-agent", str(dataset), "--page-url", "https://example.test/chat", "--output", str(output)]
        if override is not None:
            argv += ["--timeout", str(override)]
        with browser_cli_terminal():
            assert main(argv, browser_target_factory=target_factory) == 0
    else:
        options = {} if override is None else {"timeout": override}
        assert run_agent(dataset, output, page_url="https://example.test/chat",
                         browser_target_factory=target_factory, verbose=False, **options) == 1

    assert configured_timeouts == [3600 if override is None else override]


@pytest.mark.parametrize("browser_option", ["confirm_browser_run", "fresh_browser_profile"])
def test_browser_only_options_require_page_url(tmp_path: Path, browser_option: str):
    source = tmp_path / "dataset.jsonl"
    make_dataset(source, count=1)

    options = {browser_option: True}
    with pytest.raises(ValueError, match="requires --page-url"):
        run_agent(
            source,
            tmp_path / "responses.jsonl",
            answer=lambda question: question,
            verbose=False,
            **options,
        )


def test_invalid_page_url_is_rejected_before_strategy_or_browser_activity(tmp_path: Path):
    source = tmp_path / "dataset.jsonl"
    make_dataset(source, count=1)

    def forbidden_agent_factory(**_options):
        raise AssertionError("strategy Agent must not run")

    def forbidden_browser_factory(**_options):
        raise AssertionError("browser must not start")

    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        run_agent(
            source,
            tmp_path / "responses.jsonl",
            page_url="javascript:alert(1)",
            strategy_agent_factory=forbidden_agent_factory,
            browser_target_factory=forbidden_browser_factory,
            verbose=False,
        )


@pytest.mark.parametrize("suffix", ["", ".run.json", ".trials.jsonl"])
def test_browser_output_collision_stops_before_browser_or_model_start(tmp_path: Path, suffix: str):
    dataset = tmp_path / "dataset.jsonl"
    make_dataset(dataset, count=1)
    output = tmp_path / "responses.jsonl"
    protected = output.with_name(output.name + suffix)
    protected.write_text("keep existing evidence", encoding="utf-8")

    def forbidden_factory(**_options):
        raise AssertionError("No browser or model may start before output protection")

    with pytest.raises(FileExistsError):
        run_agent(
            dataset, output, page_url="https://example.test/chat",
            strategy_agent_factory=forbidden_factory,
            browser_target_factory=forbidden_factory, verbose=False,
        )

    assert protected.read_text(encoding="utf-8") == "keep existing evidence"


def test_stability_skill_repeats_all_records_and_preserves_trial_evidence(tmp_path: Path):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source)
    calls: list[str] = []
    assert run_agent(source, output, answer=lambda q: calls.append(q) or q.replace("question", "answer"),
                     strategy_agent_factory=StrategySkillAgent, seed=17, verbose=False) == 2
    assert [row["actual_response"] for row in read_records(output)] == ["answer-1", "answer-2"]
    assert calls == ["question-1"] * 3 + ["question-2"] * 3
    trials = [json.loads(line) for line in (tmp_path / "responses.jsonl.trials.jsonl").read_text().splitlines()]
    assert [(row["record_index"], row["trial"]) for row in trials] == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    run = json.loads((tmp_path / "responses.jsonl.run.json").read_text())
    assert run["skill"]["selected"] == 2
    assert run["skill"]["trials"] == 6
    assert run["skill"]["seed"] == 17
    assert "strategy" not in run


def test_run_agent_browser_target_fills_responses_and_records_safe_target_evidence(tmp_path: Path):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)

    completed = run_agent(
        source,
        output,
        page_url="https://example.test/chat",
        browser_target_factory=lambda **_options: BrowserFixtureTarget(),
        strategy_agent_factory=StrategySkillAgent,
        interactive=True,
        verbose=False,
    )

    assert completed == 1
    assert read_records(output)[0]["actual_response"] == "browser:question-1"
    run = json.loads((tmp_path / "responses.jsonl.run.json").read_text(encoding="utf-8"))
    assert run["target"] == BrowserFixtureTarget.evidence


def test_browser_progress_redacts_private_page_path_and_query(tmp_path: Path, capsys):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)
    page_url = "https://example.test/private-page-secret/chat?token=fixture-url-secret"
    received: list[str] = []

    def browser_factory(**options):
        received.append(options["page_url"])
        return BrowserFixtureTarget()

    run_agent(
        source, output, page_url=page_url, browser_target_factory=browser_factory,
        interactive=True, verbose=True,
    )

    assert received == [page_url]
    captured = capsys.readouterr()
    evidence = captured.out + captured.err + "".join(
        path.read_text(encoding="utf-8") for path in tmp_path.glob("responses.jsonl*")
    )
    assert "fixture-url-secret" not in evidence
    assert "private-page-secret" not in evidence


def test_browser_execution_failure_persists_no_raw_transport_diagnostic(tmp_path: Path, capsys):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)

    class FailingTransport(BrowserFixtureTarget):
        def answer(self, question, request_id):
            raise RuntimeError("transport failed: Cookie=fixture-error-secret; body=fixture-private-body")

    completed = run_agent(
        source, output, page_url="https://example.test/chat",
        browser_target_factory=lambda **_options: FailingTransport(),
        interactive=True, verbose=True,
    )

    assert completed == 0
    assert read_records(output)[0]["actual_response"] is None
    run = json.loads(output.with_name(output.name + ".run.json").read_text(encoding="utf-8"))
    assert run["failed"] == 3
    captured = capsys.readouterr()
    evidence = captured.out + captured.err + "".join(
        path.read_text(encoding="utf-8") for path in tmp_path.glob("responses.jsonl*")
    )
    assert "fixture-error-secret" not in evidence
    assert "fixture-private-body" not in evidence
    assert "RuntimeError" in evidence


@pytest.mark.parametrize("phase", ["open", "prepare"])
@pytest.mark.parametrize("error_kind", ["native", "runtime"])
def test_browser_cli_redacts_native_browser_errors(tmp_path: Path, capsys, phase: str, error_kind: str):
    from playwright.sync_api import Error as PlaywrightError

    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)

    def fail():
        error_type = PlaywrightError if error_kind == "native" else RuntimeError
        raise error_type("Page.goto failed at https://example.test/private?token=fixture-native-secret")

    class FailingTransport(BrowserFixtureTarget):
        def prepare(self, *_args, **_options):
            fail()

    def browser_factory(**_options):
        if phase == "open":
            fail()
        return FailingTransport()

    with browser_cli_terminal():
        exit_code = main(
            ["run-agent", str(source), "--page-url", "https://example.test/chat", "--output", str(output)],
            browser_target_factory=browser_factory,
        )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "fixture-native-secret" not in captured.out + captured.err
    assert "browser" in captured.err.lower()
    assert not output.exists()


@pytest.mark.parametrize(("error", "instruction"), [
    (RuntimeError("Playwright Chromium is unavailable; run: python -m playwright install chromium"),
     "python -m playwright install chromium"),
    (BrowserConfirmationRequired("fixture-internal-secret"), "before typing YES"),
    (ExtractionError("fixture-internal-secret"), "private diagnostics withheld"),
    (TimeoutError("fixture-internal-secret"), "use --timeout"),
])
def test_safe_browser_cli_errors_keep_actionable_instructions(tmp_path: Path, capsys, error, instruction):
    dataset = tmp_path / "dataset.jsonl"
    make_dataset(dataset, count=1)

    def browser_factory(**_options):
        raise error

    with browser_cli_terminal():
        exit_code = main(
            ["run-agent", str(dataset), "--page-url", "https://example.test/chat",
             "--output", str(tmp_path / "responses.jsonl"), "--no-verbose"],
            browser_target_factory=browser_factory,
        )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert instruction in captured.err
    assert "fixture-internal-secret" not in captured.out + captured.err


def test_run_agent_page_url_cli_uses_browser_target(tmp_path: Path):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)

    with browser_cli_terminal():
        exit_code = main(
            [
                "run-agent", str(source), "--page-url", "https://example.test/chat",
                "--output", str(output), "--no-verbose",
            ],
            skill_agent_factory=StrategySkillAgent,
            browser_target_factory=lambda **_options: BrowserFixtureTarget(),
        )

    assert exit_code == 0 and read_records(output)[0]["actual_response"] == "browser:question-1"


def test_browser_preparation_blocker_writes_safe_run_evidence_without_response_files(tmp_path: Path):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=1)

    with pytest.raises(BrowserConfirmationRequired):
        run_agent(
            source,
            output,
            page_url="https://example.test/chat",
            browser_target_factory=lambda **_options: BlockingBrowserFixtureTarget(),
            strategy_agent_factory=StrategySkillAgent,
            interactive=False,
            verbose=False,
        )

    assert not output.exists()
    assert not output.with_name(output.name + ".trials.jsonl").exists()
    run = json.loads(output.with_name(output.name + ".run.json").read_text(encoding="utf-8"))
    assert run["status"] == "blocked"
    assert run["blocker"] == {
        "stage": "browser_prepare",
        "error_type": "BrowserConfirmationRequired",
    }
    assert run["target"]["status"] == "confirmation_required"
    assert "fixture decline" not in json.dumps(run)


def test_random_skill_selection_is_replayable_from_seed(tmp_path: Path):
    source = tmp_path / "dataset.jsonl"
    make_dataset(source, count=4)
    selections: list[list[int]] = []
    for name in ("first", "second"):
        output = tmp_path / f"{name}.jsonl"
        run_agent(source, output, answer=lambda _: "answer", strategy_agent_factory=RandomSampleSkillAgent,
                  seed=42, verbose=False)
        run = json.loads((tmp_path / f"{name}.jsonl.run.json").read_text())
        selections.append([row["record_index"] for row in run["skill"]["schedule"]])
    assert selections[0] == selections[1]
    assert len(selections[0]) == 2


def test_bundled_random_sample_runs_without_a_strategy_model(tmp_path: Path, monkeypatch):
    source = tmp_path / "dataset.jsonl"
    make_dataset(source, count=6)
    bundled = (
        Path(__import__("lladar").__file__).resolve().parent
        / "skill_assets"
        / "run-agent-random-sample"
    )

    class ForbiddenSkillAgent:
        def __init__(self, **_options):
            raise AssertionError("bundled deterministic strategy must not initialize a model Agent")

    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent", ForbiddenSkillAgent)
    schedules: list[list[int]] = []
    for name in ("first", "second"):
        output = tmp_path / f"{name}.jsonl"
        assert run_agent(
            source,
            output,
            answer=lambda _question: "answer",
            skill=bundled,
            seed=42,
            verbose=False,
        ) == 5
        run = json.loads(output.with_name(output.name + ".run.json").read_text())
        schedules.append([item["record_index"] for item in run["skill"]["schedule"]])
        assert set(run["skill"]["files"]) == {"SKILL.md", "strategy.py"}

    assert schedules[0] == schedules[1]


def test_default_bundled_stability_runs_without_a_strategy_model(tmp_path: Path, monkeypatch):
    source, output = tmp_path / "dataset.jsonl", tmp_path / "responses.jsonl"
    make_dataset(source, count=2)

    class ForbiddenSkillAgent:
        def __init__(self, **_options):
            raise AssertionError("bundled deterministic strategy must not initialize a model Agent")

    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent", ForbiddenSkillAgent)
    calls: list[str] = []
    assert run_agent(
        source,
        output,
        answer=lambda question: calls.append(question) or "answer",
        verbose=False,
    ) == 2

    assert calls == ["question-1"] * 3 + ["question-2"] * 3
    run = json.loads(output.with_name(output.name + ".run.json").read_text())
    assert set(run["skill"]["files"]) == {"SKILL.md", "strategy.py"}


def test_eval_uses_trials_sidecar_and_calculates_stability(tmp_path: Path):
    responses = tmp_path / "responses.jsonl"
    write_jsonl(responses, [{"question": "Capital?", "expected_answer": "Taipei", "actual_response": "Taipei"}])
    write_jsonl(responses.with_name(responses.name + ".trials.jsonl"), [
        {"record_index": 1, "trial": 1, "question": "Capital?", "expected_answer": "Taipei", "actual_response": "Taipei", "status": "ok"},
        {"record_index": 1, "trial": 2, "question": "Capital?", "expected_answer": "Taipei", "actual_response": "Kaohsiung", "status": "ok"},
        {"record_index": 1, "trial": 3, "question": "Capital?", "expected_answer": "Taipei", "status": "execution_error", "error": "offline"},
    ])
    result = evaluate(responses, output=tmp_path / "evaluation.json", skill_agent_factory=EvaluationSkillAgent)
    assert result["summary"] == {"records": 1, "scheduled_trials": 3, "evaluated": 2, "execution_error": 1, "judge_error": 0, "coverage": pytest.approx(2 / 3)}
    stability = result["stability"]["records"][0]
    assert stability["correct_rate"] == pytest.approx(1 / 3)
    assert stability["fully_correct"] is False
    assert stability["outcome_consistent"] is False
    assert [item["trial"] for item in result["items"]] == [1, 2, 3]


def test_report_skill_renders_trial_and_stability_evidence(tmp_path: Path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "source": "responses.jsonl", "trials_source": "responses.jsonl.trials.jsonl",
        "skill": {"name": "answer-verdict", "path": "skill", "files": {"SKILL.md": "hash"}}, "evaluator_model": "test-model",
        "plan": {"title": "Correctness", "approach": "Compare answers.", "dimensions": [{"name": "correct", "description": "Correct", "kind": "boolean"}], "limitations": []},
        "summary": {"records": 1, "scheduled_trials": 2, "evaluated": 2, "execution_error": 0, "judge_error": 0, "coverage": 1.0},
        "aggregates": {"correct": {"kind": "boolean", "description": "Correct", "count": 2, "missing": 0, "true": 1, "false": 1, "true_rate": 0.5}},
        "stability": {"records": [{"record_index": 1, "scheduled_trials": 2, "correct": 1, "incorrect": 1, "execution_error": 0, "judge_error": 0, "correct_rate": 0.5, "fully_correct": False, "outcome_consistent": False}]},
        "items": [
            {"record_index": 1, "trial": 1, "question": "Capital?", "expected_answer": "Taipei", "actual_response": "Taipei", "status": "evaluated", "values": {"correct": True}, "reason": "Matches."},
            {"record_index": 1, "trial": 2, "question": "Capital?", "expected_answer": "Taipei", "actual_response": "Kaohsiung", "status": "evaluated", "values": {"correct": False}, "reason": "Differs."},
        ],
    }), encoding="utf-8")
    report = tmp_path / "report.md"
    assert create_report(evaluation, report, skill_agent_factory=ReportSkillAgent) == report
    rendered = report.read_text(encoding="utf-8")
    assert "## Stability" in rendered
    assert "| scheduled_trials | 2 |" in rendered
    assert "| 1 | 2 | 1 | 1 |" in rendered
    assert "| 1 | 2 | Capital? | Taipei | Kaohsiung | evaluated" in rendered
