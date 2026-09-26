"""Skill generation contracts through the CLI and dataset API."""

from pathlib import Path
import hashlib
import json

import pytest

from lladar.api import create_test_dataset
from lladar.cli import build_parser


@pytest.mark.parametrize("option", [
    ["--chunk-size", "100"], ["--overlap", "0.2"], ["--strict"],
    ["--prompt", "Write in Chinese"], ["--prompt-file", "guidance.txt"],
    ["--method", "simple"],
])
def test_create_rejects_retired_options(option):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["create", "test-dataset", "--knowledge", "notes.md", *option])
    assert error.value.code == 2


FACTS = ["Service A keeps data for 30 days.", "Service B keeps data for 60 days.", "Support opens at 09:00."]
QUESTIONS = ["How long does A keep data?", "How long does B keep data?", "When does support open?"]
ANSWERS = ["30 days.", "60 days.", "09:00."]


@pytest.fixture
def generation_inputs(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("\n".join(FACTS), encoding="utf-8")
    skill = tmp_path / "knowledge-point-qa"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: knowledge-point-qa\ndescription: Extract facts and generate QA.\n---\n"
        "Read the source, submit separate knowledge points, then generate one QA per point.\n",
        encoding="utf-8",
    )
    return source, skill


class ScriptedSkillAgent:
    """External agent substitute: uses only the runtime-provided tool interface."""

    def __init__(self, **options):
        self.options = options

    def __call__(self, request):
        assert "guidance" not in request
        skill = Path(self.options["skills"][0])
        assert (skill / "SKILL.md").read_text(encoding="utf-8").strip()
        tools = self.options["tools"]
        if request["stage"] == "knowledge_points":
            source_id = request["source_id"]
            page = tools["read_source"](source_id=source_id)
            result = tools["submit_knowledge_points"](points=[
                {"statement": fact, "topic": "Service", "evidence": [
                    {"read_id": page["read_id"], "quote": fact},
                ]}
                for fact in FACTS
            ])
            assert len(result["accepted"]) == 3
        else:
            point = tools["read_knowledge_point"](knowledge_point_id=request["knowledge_point_id"])
            index = FACTS.index(point["statement"])
            result = tools["submit_qa"](record={
                "knowledge_point_id": point["id"],
                "question": QUESTIONS[index], "expected_answer": ANSWERS[index],
            })
            assert result["accepted"]
        return {"loaded_skills": [skill.name]}


def test_local_skill_generates_one_question_per_knowledge_point(generation_inputs):
    source, skill = generation_inputs
    records = create_test_dataset(
        source, skill=skill, skill_agent_factory=ScriptedSkillAgent, verbose=False,
    )
    assert {row["question"] for row in records} == set(QUESTIONS)
    assert all(set(row) == {"question", "expected_answer", "actual_response"} for row in records)
    assert all(row["actual_response"] is None for row in records)


def test_bundled_skill_is_default_independent_of_working_directory(generation_inputs, tmp_path, monkeypatch):
    source, _ = generation_inputs
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "default.jsonl"
    records = create_test_dataset(
        source, output=output, skill_agent_factory=ScriptedSkillAgent, verbose=False,
    )
    provenance = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    bundled = Path(__import__("lladar").__file__).resolve().parent / "skill_assets" / "knowledge-point-qa"
    assert len(records) == 3
    assert provenance["skill"]["path"] == str(bundled)
    assert provenance["skill"]["files"]["SKILL.md"] == hashlib.sha256((bundled / "SKILL.md").read_bytes()).hexdigest()
    assert "guidance_sha256" not in provenance["settings"]


def test_cli_uses_bundled_skill_by_default(generation_inputs, tmp_path, capsys):
    from lladar.cli import main

    source, _ = generation_inputs
    output = tmp_path / "default-cli.jsonl"
    assert main(["create", "test-dataset", "--knowledge", str(source),
                 "--output", str(output), "--no-verbose"],
                skill_agent_factory=ScriptedSkillAgent) == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 3
    assert "status=complete" in capsys.readouterr().out
    assert Path(str(output) + ".generation.json").exists()


@pytest.mark.parametrize("option", ["method", "provider", "chunk_size", "overlap", "strict",
                                    "prompt", "prompt_file"])
def test_python_create_interface_rejects_removed_options(generation_inputs, option):
    source, _ = generation_inputs
    with pytest.raises(TypeError, match=option):
        create_test_dataset(source, **{option: "obsolete"})


def test_missing_bundled_skill_fails_without_falling_back(generation_inputs, tmp_path, monkeypatch):
    import lladar.api as api

    source, _ = generation_inputs
    monkeypatch.setattr(api, "BUILTIN_SKILL_DIR", tmp_path / "missing-bundled-skill")

    def unexpected_agent(**kwargs):
        pytest.fail("missing bundled method must fail before agent initialization")

    with pytest.raises(FileNotFoundError, match="SKILL.md"):
        create_test_dataset(source, skill_agent_factory=unexpected_agent, verbose=False)


def test_skill_can_correct_rejected_points_without_losing_valid_points(generation_inputs):
    source, skill = generation_inputs

    class CorrectingAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request["stage"] == "knowledge_points":
                tools = self.options["tools"]
                page = tools["read_source"](source_id=request["source_id"])
                bad = {"statement": "Invented fact", "topic": "Service", "evidence": [
                    {"read_id": page["read_id"], "quote": "The service is free."},
                ]}
                result = tools["submit_knowledge_points"](points=[bad])
                assert result["accepted"] == []
                assert "evidence" in result["rejected"][0]["error"]
            return super().__call__(request)

    records = create_test_dataset(source, skill=skill, skill_agent_factory=CorrectingAgent, verbose=False)
    assert len(records) == 3


def test_saved_dataset_has_traceable_sidecar_and_runs_with_existing_runner(generation_inputs, tmp_path):
    from lladar.records import read_records
    from lladar.runner import run_agent

    source, skill = generation_inputs
    output = tmp_path / "dataset.jsonl"
    rows = create_test_dataset(source, skill=skill, output=output,
                               skill_agent_factory=ScriptedSkillAgent, verbose=False)
    provenance = json.loads(Path(str(output) + ".generation.json").read_text())
    assert provenance["schema_version"] == 1
    assert provenance["status"] == "complete"
    assert provenance["dataset"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert provenance["skill"]["path"] == str(skill.resolve())
    assert provenance["skill"]["files"]["SKILL.md"] == hashlib.sha256((skill / "SKILL.md").read_bytes()).hexdigest()
    points = {point["id"]: point for point in provenance["knowledge_points"]}
    assert len(points) == 3
    assert len(provenance["dataset"]["lines"]) == 3
    for line in provenance["dataset"]["lines"]:
        assert line["line"] in (1, 2, 3)
        point = points[line["knowledge_point_ids"][0]]
        evidence = point["evidence"][0]
        assert source.read_text()[evidence["start_char"]:evidence["end_char"]] == evidence["quote"]
    assert read_records(output) == rows
    responses = tmp_path / "responses.jsonl"

    class AllCasesSkillAgent:
        def __init__(self, *, skills, tools, **_options):
            self.name, self.tools = Path(skills[0]).name, tools

        def __call__(self, _request):
            self.tools["read_dataset"]()
            self.tools["write_strategy"](
                "def select_cases(cases, schedule):\n"
                "    for case in cases:\n"
                "        schedule(case)\n"
            )
            return {"loaded_skills": [self.name], "skill_files": {"SKILL.md": "fixture"}}

    assert run_agent(output, responses, answer=lambda question: "Received: " + question,
                     strategy_agent_factory=AllCasesSkillAgent, verbose=False) == 3
    actual = read_records(responses)
    assert all(row["actual_response"] == "Received: " + row["question"] for row in actual)


@pytest.mark.parametrize("existing", ["dataset.jsonl", "dataset.jsonl.generation.json"])
def test_existing_output_is_rejected_before_model_work(generation_inputs, tmp_path, existing):
    source, skill = generation_inputs
    protected = tmp_path / existing
    protected.write_text("keep me")

    def unexpected_agent(**kwargs):
        pytest.fail("output preflight must run before agent initialization")

    with pytest.raises(FileExistsError):
        create_test_dataset(source, skill=skill, output=tmp_path / "dataset.jsonl",
                            skill_agent_factory=unexpected_agent, verbose=False)
    assert protected.read_text() == "keep me"


@pytest.mark.parametrize("old_outputs", [False, True])
def test_failed_pair_publication_restores_outputs(generation_inputs, tmp_path, monkeypatch, old_outputs):
    import os

    source, skill = generation_inputs
    output = tmp_path / "dataset.jsonl"
    sidecar = Path(str(output) + ".generation.json")
    if old_outputs:
        output.write_text("previous dataset")
        sidecar.write_text("previous provenance")
    replace = os.replace

    def fail_sidecar_publication(src, dst):
        if Path(dst) == sidecar and str(src).endswith(".tmp"):
            raise OSError("simulated publication failure")
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_sidecar_publication)
    with pytest.raises(OSError, match="publication failure"):
        create_test_dataset(source, skill=skill, output=output, force=old_outputs,
                            skill_agent_factory=ScriptedSkillAgent, verbose=False)
    if old_outputs:
        assert output.read_text() == "previous dataset"
        assert sidecar.read_text() == "previous provenance"
    else:
        assert not output.exists()
        assert not sidecar.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_reread_deduplicates_points_and_preserves_all_quote_positions(generation_inputs, tmp_path):
    source, skill = generation_inputs
    source.write_text(FACTS[0] + "\n" + FACTS[0])

    class RereadingAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request["stage"] != "knowledge_points":
                return super().__call__(request)
            tools = self.options["tools"]
            ids = []
            for _ in range(2):
                page = tools["read_source"](request["source_id"])
                result = tools["submit_knowledge_points"]([{
                    "statement": FACTS[0], "topic": "Service", "evidence": [
                        {"read_id": page["read_id"], "quote": FACTS[0]},
                    ],
                }])
                ids.extend(result["accepted"])
            assert ids == ["kp_000001", "kp_000001"]
            return {"loaded_skills": [skill.name]}

    output = tmp_path / "repeated.jsonl"
    rows = create_test_dataset(source, skill=skill, output=output,
                               skill_agent_factory=RereadingAgent, verbose=False)
    assert len(rows) == 1
    provenance = json.loads(Path(str(output) + ".generation.json").read_text())
    assert len(provenance["knowledge_points"]) == 1
    evidence = provenance["knowledge_points"][0]["evidence"]
    assert {item["start_char"] for item in evidence} == {0, 34}
    assert {item["read_id"] for item in evidence} == {"read_000001", "read_000002"}


def test_failed_point_retries_three_times_and_other_points_still_publish(generation_inputs, tmp_path):
    source, skill = generation_inputs
    attempts = []

    class FailingPointAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request.get("knowledge_point_id") == "kp_000002":
                attempts.append(request["attempt"])
                error = RuntimeError("provider turn failed")
                error.skill_evidence = {"loaded_skills": [skill.name], "max_round": 30, "tool_call_limit": 40}
                raise error
            return super().__call__(request)

    output = tmp_path / "partial.jsonl"
    rows = create_test_dataset(source, skill=skill, output=output,
                               skill_agent_factory=FailingPointAgent, verbose=False)
    assert len(rows) == 2
    assert attempts == [1, 2, 3]
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text())
    assert sidecar["status"] == "partial"
    failed = next(point for point in sidecar["knowledge_points"] if point["id"] == "kp_000002")
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3
    assert len(failed["errors"]) == 3
    assert sidecar["stats"]["failed_points"] == 1
    assert sidecar["stats"]["valid_qa_points"] == 2
    failed_turns = [item for item in sidecar["executions"] if item.get("error_type")]
    assert all(item["loaded_skills"] == [skill.name] for item in failed_turns)
    assert all(item["tool_call_limit"] == 40 for item in failed_turns)


@pytest.mark.parametrize("count, expected_rows, skipped", [(0, 1, 0), (1, 1, 2), (2, 1, 0)])
def test_count_applies_after_qa_dedup_and_retains_all_attempted_provenance(
    generation_inputs, tmp_path, count, expected_rows, skipped,
):
    source, skill = generation_inputs

    class DuplicateQAAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request["stage"] == "knowledge_points":
                return super().__call__(request)
            self.options["tools"]["submit_qa"]({
                "knowledge_point_id": request["knowledge_point_id"],
                "question": "Same  question?" if request["knowledge_point_id"] == "kp_000001" else "Same question?",
                "expected_answer": "Same answer.",
            })
            return {"loaded_skills": [skill.name]}

    output = tmp_path / "dedup.jsonl"
    rows = create_test_dataset(source, skill=skill, output=output, count=count,
                               skill_agent_factory=DuplicateQAAgent, verbose=False)
    assert len(rows) == expected_rows
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text())
    assert sidecar["status"] == "complete"
    assert len(sidecar["dataset"]["lines"][0]["knowledge_point_ids"]) == 3 - skipped
    assert sum(p["status"] == "not_attempted_count_limit" for p in sidecar["knowledge_points"]) == skipped
    assert sidecar["stats"]["not_attempted_count_limit"] == skipped
    assert sidecar["stats"]["failed_points"] == 0


@pytest.mark.parametrize("finish_reading", [False, True])
def test_skill_controls_paged_reading_and_unread_source_is_partial(generation_inputs, tmp_path, finish_reading):
    source, skill = generation_inputs
    original = FACTS[0] + "\n" + ("Context. " * 2000) + "\n" + FACTS[1]
    source.write_text(original)

    class PagedAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request["stage"] != "knowledge_points":
                return super().__call__(request)
            tools = self.options["tools"]
            first = tools["read_source"](request["source_id"])
            assert first["text"] == original[:first["end_char"]]
            assert first["next_start"] is not None
            evidence = [{"read_id": first["read_id"], "quote": FACTS[0]}]
            page = first
            if finish_reading:
                while page["next_start"] is not None:
                    page = tools["read_source"](request["source_id"], start_char=page["next_start"])
                evidence.append({"read_id": page["read_id"], "quote": FACTS[1]})
            tools["submit_knowledge_points"]([{"statement": FACTS[0], "topic": "Service", "evidence": evidence}])
            return {"loaded_skills": [skill.name]}

    output = tmp_path / "paged.jsonl"
    rows = create_test_dataset(source, skill=skill, output=output,
                               skill_agent_factory=PagedAgent, verbose=False)
    assert len(rows) == 1
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text())
    assert sidecar["status"] == ("complete" if finish_reading else "partial")
    trace = sidecar["sources"][0]
    if finish_reading:
        assert trace["read_ranges"] == [[0, len(original)]]
        assert trace["unread_ranges"] == []
        assert len(sidecar["knowledge_points"][0]["evidence"]) == 2
    else:
        assert trace["unread_ranges"] == [[12000, len(original)]]
        assert trace["attempts"] == 3


def test_assigned_point_is_immutable_and_first_accepted_qa_is_preserved(generation_inputs, tmp_path):
    source, skill = generation_inputs

    class MutatingAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request["stage"] == "knowledge_points":
                return super().__call__(request)
            tools = self.options["tools"]
            point_id = request["knowledge_point_id"]
            point = tools["read_knowledge_point"](point_id)
            point["evidence"][0]["quote"] = "tampered"
            assert tools["read_knowledge_point"](point_id)["evidence"][0]["quote"] != "tampered"
            result = super().__call__(request)
            with pytest.raises(ValueError, match="already"):
                tools["submit_qa"]({"knowledge_point_id": point_id, "question": "Replacement?", "expected_answer": "No."})
            return result

    rows = create_test_dataset(source, skill=skill, skill_agent_factory=MutatingAgent, verbose=False)
    assert {row["question"] for row in rows} == set(QUESTIONS)


def test_cli_accepts_one_local_skill_and_rejects_repetition(generation_inputs, tmp_path, capsys):
    from lladar.cli import main
    from lladar.records import read_records

    source, skill = generation_inputs
    output = tmp_path / "cli.jsonl"
    args = ["create", "test-dataset", "--knowledge", str(source), "--skill", str(skill),
            "--output", str(output), "--no-verbose"]
    assert main(args, skill_agent_factory=ScriptedSkillAgent) == 0
    assert len(read_records(output)) == 3
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(args + ["--skill", str(skill)])
    assert error.value.code == 2
    assert "only one" in capsys.readouterr().err


@pytest.mark.parametrize("document", ["No frontmatter", "---\nname: [\n---\nInstructions.",
                                        "---\nname: wrong\ndescription: Bad name.\n---\nDo work.",
                                        "---\nname: knowledge-point-qa\ndescription: Empty.\n---\n"])
def test_invalid_native_skill_fails_before_agent_initialization(generation_inputs, document):
    source, skill = generation_inputs
    (skill / "SKILL.md").write_text(document)

    def unexpected_agent(**kwargs):
        pytest.fail("skill validation must precede agent initialization")

    with pytest.raises(ValueError, match="skill|SKILL"):
        create_test_dataset(source, skill=skill, skill_agent_factory=unexpected_agent, verbose=False)


def test_source_tools_expire_when_work_item_changes(generation_inputs, tmp_path):
    source, skill = generation_inputs
    another = tmp_path / "another.md"
    another.write_text(source.read_text())
    previous = []

    class HoldingToolsAgent(ScriptedSkillAgent):
        def __call__(self, request):
            tools = self.options["tools"]
            if request["stage"] == "knowledge_points":
                if previous:
                    with pytest.raises(ValueError, match="expired"):
                        previous[0](request["source_id"])
                previous[:] = [tools["read_source"]]
            else:
                with pytest.raises(ValueError):
                    previous[0]("source_001")
                assert set(tools) == {"read_knowledge_point", "submit_qa"}
            return super().__call__(request)

    rows = create_test_dataset([source, another], skill=skill,
                               skill_agent_factory=HoldingToolsAgent, verbose=False)
    assert len(rows) == 3


def test_provenance_counts_rejected_candidates_and_links_qa_ids(generation_inputs, tmp_path):
    from lladar.exceptions import DatasetValidationError

    source, skill = generation_inputs

    class RejectingAgent(ScriptedSkillAgent):
        def __call__(self, request):
            tools = self.options["tools"]
            if request["stage"] == "knowledge_points":
                assert tools["submit_knowledge_points"]([{"statement": "unsupported"}])["rejected"]
            else:
                with pytest.raises(DatasetValidationError):
                    tools["submit_qa"]({"knowledge_point_id": request["knowledge_point_id"],
                                        "question": "Bad answer?", "expected_answer": ""})
            return super().__call__(request)

    output = tmp_path / "statistics.jsonl"
    create_test_dataset(source, skill=skill, output=output, skill_agent_factory=RejectingAgent, verbose=False)
    provenance = json.loads(Path(str(output) + ".generation.json").read_text())
    stats = provenance["stats"]
    assert stats["point_candidates"] == 4
    assert stats["rejected_points"] == 1
    assert stats["deduplicated_points"] == 0
    assert stats["qa_candidates"] == 6
    assert stats["rejected_qa"] == 3
    qa = {item["id"]: item for item in provenance["qa"]}
    for line in provenance["dataset"]["lines"]:
        assert {qa[key]["knowledge_point_id"] for key in line["qa_ids"]} == set(line["knowledge_point_ids"])
    assert all(item["host_tool_events"] for item in provenance["executions"])
    assert provenance["settings"]["max_attempts"] == 3


def test_verbose_skill_run_reports_stages(generation_inputs, capsys):
    source, skill = generation_inputs
    create_test_dataset(source, skill=skill, skill_agent_factory=ScriptedSkillAgent,
                        verbose=True)
    captured = capsys.readouterr()
    assert "[KNOWLEDGE_POINTS]" in captured.err
    assert "[QA]" in captured.err
    assert "[DONE]" in captured.err


@pytest.mark.parametrize("failure_mode", ["initialization", "not_loaded", "no_qa", "serialization"])
def test_fatal_or_empty_generation_never_replaces_existing_outputs(generation_inputs, tmp_path, failure_mode):
    from lladar.exceptions import DatasetValidationError

    source, skill = generation_inputs
    output = tmp_path / "protected.jsonl"
    sidecar = Path(str(output) + ".generation.json")
    output.write_text("old dataset")
    sidecar.write_text("old sidecar")
    attempts = []

    class BrokenAgent(ScriptedSkillAgent):
        def __init__(self, **options):
            if failure_mode == "initialization":
                raise OSError("cannot initialize runtime")
            super().__init__(**options)

        def __call__(self, request):
            attempts.append(request["stage"])
            if failure_mode == "no_qa" and request["stage"] == "qa":
                return {"loaded_skills": [skill.name]}
            result = super().__call__(request)
            if failure_mode == "not_loaded":
                result["loaded_skills"] = []
            if failure_mode == "serialization":
                result["unserializable"] = {object()}
            return result

    with pytest.raises((OSError, DatasetValidationError, TypeError)):
        create_test_dataset(source, skill=skill, output=output, force=True,
                            skill_agent_factory=BrokenAgent, verbose=False)
    assert output.read_text() == "old dataset"
    assert sidecar.read_text() == "old sidecar"
    if failure_mode == "not_loaded":
        assert attempts == ["knowledge_points"]
    if failure_mode == "no_qa":
        assert attempts.count("qa") == 9


def test_tool_contract_rejects_wrong_ids_ranges_and_extra_fields(generation_inputs):
    from lladar.exceptions import DatasetValidationError

    source, skill = generation_inputs

    class BoundaryAgent(ScriptedSkillAgent):
        def __call__(self, request):
            tools = self.options["tools"]
            if request["stage"] == "knowledge_points":
                assert "submit_qa" not in tools
                for args in [("../../private",), (request["source_id"], -1), (request["source_id"], True),
                             (request["source_id"], 0, 999999)]:
                    with pytest.raises(ValueError):
                        tools["read_source"](*args)
                for evidence in [{"read_id": "invented", "quote": FACTS[0]},
                                 {"read_id": "invented", "quote": FACTS[0], "start_char": 0}]:
                    assert tools["submit_knowledge_points"]([{
                        "statement": FACTS[0], "topic": "Service", "evidence": [evidence],
                    }])["accepted"] == []
            else:
                point_id = request["knowledge_point_id"]
                wrong_id = "kp_000002" if point_id != "kp_000002" else "kp_000001"
                with pytest.raises(ValueError):
                    tools["read_knowledge_point"](wrong_id)
                for record in [
                    {"knowledge_point_id": wrong_id, "question": "Wrong point?", "expected_answer": "No"},
                    {"knowledge_point_id": point_id, "question": "Extra?", "expected_answer": "No", "score": 1},
                    {"knowledge_point_id": point_id, "question": "", "expected_answer": "No"},
                ]:
                    with pytest.raises((ValueError, DatasetValidationError)):
                        tools["submit_qa"](record)
            return super().__call__(request)

    assert len(create_test_dataset(source, skill=skill, skill_agent_factory=BoundaryAgent, verbose=False)) == 3


def test_skill_file_cannot_change_mid_run_without_invalidating_provenance(generation_inputs, tmp_path):
    from lladar.exceptions import DatasetValidationError

    source, skill = generation_inputs

    class ChangedSkillAgent(ScriptedSkillAgent):
        def __call__(self, request):
            result = super().__call__(request)
            result["skill_files"] = {"SKILL.md": "changed-content-hash"}
            return result

    output = tmp_path / "changed.jsonl"
    with pytest.raises(DatasetValidationError, match="skill.*changed"):
        create_test_dataset(source, skill=skill, output=output,
                            skill_agent_factory=ChangedSkillAgent, verbose=False)
    assert not output.exists()


def test_fully_read_source_with_unresolved_rejections_is_not_complete(generation_inputs, tmp_path):
    source, skill = generation_inputs
    second = tmp_path / "second.md"
    second.write_text(source.read_text())

    class RejectedSourceAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request.get("source_id") == "source_001":
                tools = self.options["tools"]
                page = tools["read_source"](request["source_id"])
                tools["submit_knowledge_points"]([{"statement": "Unsupported", "topic": "Service", "evidence": [
                    {"read_id": page["read_id"], "quote": "Invented quotation"},
                ]}])
                return {"loaded_skills": [skill.name]}
            return super().__call__(request)

    output = tmp_path / "rejected-source.jsonl"
    create_test_dataset([source, second], skill=skill, output=output,
                        skill_agent_factory=RejectedSourceAgent, verbose=False)
    provenance = json.loads(Path(str(output) + ".generation.json").read_text())
    assert provenance["status"] == "partial"
    assert provenance["sources"][0]["unread_ranges"] == []
    assert provenance["sources"][0]["status"] == "failed"
    assert provenance["sources"][0]["attempts"] == 3


def test_cli_reports_partial_even_when_verbose_is_disabled(generation_inputs, tmp_path, capsys):
    from lladar.cli import main

    source, skill = generation_inputs

    class PartialAgent(ScriptedSkillAgent):
        def __call__(self, request):
            if request.get("knowledge_point_id") == "kp_000002":
                raise RuntimeError("work failed")
            return super().__call__(request)

    assert main(["create", "test-dataset", "--knowledge", str(source), "--skill", str(skill),
                 "--output", str(tmp_path / "partial.jsonl"), "--no-verbose"],
                skill_agent_factory=PartialAgent) == 0
    assert "status=partial" in capsys.readouterr().out.lower()


def test_force_successfully_replaces_both_outputs_without_backup_residue(generation_inputs, tmp_path):
    from lladar.records import read_records

    source, skill = generation_inputs
    output = tmp_path / "replace.jsonl"
    sidecar = Path(str(output) + ".generation.json")
    output.write_text("old dataset")
    sidecar.write_text("old provenance")
    rows = create_test_dataset(source, skill=skill, output=output, force=True,
                               skill_agent_factory=ScriptedSkillAgent, verbose=False)
    assert read_records(output) == rows
    assert json.loads(sidecar.read_text())["dataset"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert not list(tmp_path.glob(".*.bak"))
    assert not list(tmp_path.glob(".*.generation.lock"))


def test_missing_skill_entry_is_reported_without_model_work(generation_inputs):
    source, skill = generation_inputs
    missing = skill / "not-a-skill"

    def unexpected_agent(**kwargs):
        pytest.fail("missing skill must fail before model work")

    with pytest.raises(FileNotFoundError, match="SKILL.md"):
        create_test_dataset(source, skill=missing, skill_agent_factory=unexpected_agent, verbose=False)


def test_no_force_preserves_output_created_during_publication(generation_inputs, tmp_path, monkeypatch):
    source, skill = generation_inputs
    output = tmp_path / "racing.jsonl"
    original_open = Path.open
    injected = False

    def concurrent_writer(path, *args, **kwargs):
        nonlocal injected
        if path.name.endswith(".tmp") and not injected:
            injected = True
            with original_open(output, "w", encoding="utf-8") as handle:
                handle.write("concurrent writer's data")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", concurrent_writer)
    with pytest.raises(FileExistsError):
        create_test_dataset(source, skill=skill, output=output,
                            skill_agent_factory=ScriptedSkillAgent, verbose=False)
    assert output.read_text() == "concurrent writer's data"
    assert not Path(str(output) + ".generation.json").exists()
