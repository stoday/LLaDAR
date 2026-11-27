import hashlib
import json
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

from lladar.api import create_test_dataset
from lladar.combined_generation import select_audit_batches, source_batches
from lladar.knowledge_coverage import render_coverage_report
from lladar.skill_agent import AKASHA_SKILL_PRELOAD_AVAILABLE, AkashaSkillAgent
from lladar.work_session import digest
from test_skill_agent import ToolScriptModel


FACTS = ("The East counter opens at 08:00.", "The West counter opens at 09:00.",
         "The South counter opens at 10:00.")


def fact(index, *, source_id="source_001"):
    return {"id": str(index), "statement": FACTS[index], "topic": "Opening times",
            "evidence": [{"source_id": source_id, "quote": FACTS[index]}], "answerability": "eligible"}


def question(index, *, key=None):
    return {"record": {"knowledge_point_id": str(index) if key is None else key,
                       "question": f"What is the opening time of counter {index}?",
                       "expected_answer": FACTS[index][-6:-1]},
            "coverage_claims": [{"knowledge_point_id": str(index) if key is None else key,
                                 "covers_entire_point": True, "reason": "The answer requires this time."}]}


def read_sidecar(output):
    return json.loads(Path(f"{output}.generation.json").read_text(encoding="utf-8"))


class BatchAgent:
    def __init__(self, *, skills, tools, **options):
        self.skill = Path(skills[0])
        self.tools = tools

    def evidence(self):
        return {"loaded_skills": [self.skill.name],
                "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}


@pytest.mark.parametrize("custom_skill", [False, True], ids=["builtin", "custom"])
@pytest.mark.parametrize("level", [None, 2], ids=["default", "level-2"])
def test_combined_workflow_uses_one_provider_response_for_each_batch(tmp_path, monkeypatch, capsys, custom_skill, level):
    from lladar.cli import main

    source, output = tmp_path / "source.txt", tmp_path / "out.jsonl"
    source.write_text(" ".join(FACTS), encoding="utf-8")
    reply = AIMessage(content="", tool_calls=[{"name": "submit_dataset_batch", "id": "submit", "args": {
        "points": [fact(i) for i in range(3)], "questions": [question(0), question(1)], "complete": True}}],
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140})
    model = ToolScriptModel(replies=[reply])
    native_agents = []

    def generation_agent(**kwargs):
        adapter = AkashaSkillAgent(**{**kwargs, "model": model})
        native_agents.append(adapter.agent)
        return adapter

    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent", generation_agent)
    args = ["create", "test-dataset", "--knowledge", str(source), "--output", str(output),
            "--count", "1", "--no-verbose", "--lang", "en"]
    skill_name = "knowledge-point-qa"
    if custom_skill:
        skill_name = "counter-questions"
        skill = tmp_path / skill_name
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: counter-questions\ndescription: Generate counter questions.\n---\n"
            "Use the opening times when writing questions.\n",
            encoding="utf-8",
        )
        args.extend(["--skill", str(skill)])

    class AuditAgent(BatchAgent):
        def __call__(self, request):
            assert request["stage"] == "inventory_audit"
            self.tools["submit_dataset_batch"]([fact(i) for i in range(3)], [], True)
            return self.evidence()

    if level is not None:
        args.extend(["--workflow-level", str(level)])
    assert main(args, coverage_agent_factory=AuditAgent) == 0
    saved = read_sidecar(output)
    assert saved["workflow_level"] == (level or 1)
    assert saved["token_usage"]["stages"]["generation"]["reported_calls"] == 1
    assert saved["token_usage"]["total_tokens"] == 140
    assert model.offered == [["submit_dataset_batch"]]
    assert len(model.prompts) == 1
    native_agent, = native_agents
    if AKASHA_SKILL_PRELOAD_AVAILABLE:
        assert native_agent.skill_middleware.loaded_skill_names == [skill_name]
        assert native_agent.skill_middleware.preloaded_references
    if custom_skill:
        assert any("Use the opening times when writing questions." in message.content
                   for message in model.prompts[0] if isinstance(message.content, str))
    assert saved["coverage"]["audit_status"] == ("sampled" if level == 2 else "generation_only")
    assert saved["coverage"]["summary"]["pending_points"] == 2
    execution, = [item for item in saved["executions"] if item["stage"] == "generation"]
    assert execution["loaded_skills"] == [skill_name]
    assert len(execution["skill_files"]["SKILL.md"]) == 64
    assert f"Workflow level {level or 1} · Combined generation" in capsys.readouterr().out
    if level is None:
        assert "Source inventory and coverage claims were generated in one pass." in render_coverage_report(saved)


@pytest.mark.skipif(not AKASHA_SKILL_PRELOAD_AVAILABLE, reason="requires an Akasha runtime with Skill preloading")
def test_level_three_cli_uses_native_skill_from_the_first_turn(tmp_path, monkeypatch):
    from coverage_test_support import SourceCoverageAgent
    from lladar.cli import main

    source, output = tmp_path / "source.txt", tmp_path / "out.jsonl"
    source.write_text(FACTS[0], encoding="utf-8")
    source_sha = hashlib.sha256(FACTS[0].encode("utf-8")).hexdigest()
    ref = "ref_" + hashlib.sha256(f"source_001:{source_sha}:0:{len(FACTS[0])}".encode("utf-8")).hexdigest()[:24]
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "read_source", "id": "read", "args": {"source_id": "source_001"}}]),
        AIMessage(content="", tool_calls=[{"name": "submit_question", "id": "submit", "args": {
            "request_id": "fixture", "question": "When does the East counter open?", "expected_answer": "08:00",
            "source_refs": [ref], "answer_basis": "source_annotation", "rationale": "The time is stated in the source."}}]),
        AIMessage(content="Complete."),
    ])
    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent",
                        lambda **kwargs: AkashaSkillAgent(**{**kwargs, "model": model}))
    assert main(["create", "test-dataset", "--knowledge", str(source), "--output", str(output),
                 "--workflow-level", "3", "--count", "1", "--no-verbose", "--lang", "en"],
                coverage_agent_factory=SourceCoverageAgent) == 0
    assert {"read_skill_resource", "python_execute", "submit_question"} <= set(model.offered[0])
    assert all("load_skill" not in offered for offered in model.offered)
    assert any("For free questions prefer" in message.content
               for message in model.prompts[0] if isinstance(message.content, str))
    assert read_sidecar(output)["workflow_level"] == 3


@pytest.mark.parametrize("level", [1, 2])
@pytest.mark.parametrize(("requested", "wire", "protocol", "right", "answer"), [
    ("free", "free", "natural_language", [], "08:00"),
    ("auto", "single_choice", "one_option_id", ["A"], "A"),
    ("single-choice", "single_choice", "one_option_id", ["A"], "A"),
    ("multiple-choice", "multiple_choice", "option_id_list", ["A", "B"], "A,B"),
    ("ranking", "ranking", "ordered_option_ids", ["A", "B", "C"], "A>B>C"),
])
def test_combined_workflows_use_existing_question_contracts(tmp_path, level, requested, wire, protocol, right, answer):
    source, output = tmp_path / "text.md", tmp_path / "qa.jsonl"
    source.write_text("\n".join(FACTS), encoding="utf-8")
    checkpoints = []

    class Agent(BatchAgent):
        def __call__(self, request):
            checkpoints.append(request["stage"])
            questions = []
            if request["stage"] == "generation":
                candidate = question(0)
                if wire != "free":
                    candidate["record"] = {"question": "Which counters match the opening time criteria?",
                        "question_type": wire, "correct_option_ids": right, "option_point_ids": ["0", "1", "2"]}
                    if wire == "ranking":
                        candidate["record"].update(ranking_axis="Opening time", direction="ascending")
                questions.append(candidate)
            points = [fact(i) for i in range(3)]
            if wire == "ranking":
                for point in points:
                    point["local_id"] = point.pop("id")
            self.tools["submit_dataset_batch"](points, questions, True)
            return self.evidence()

    rows = create_test_dataset(source, output=output, workflow_level=level, question_type=requested,
                               skill_agent_factory=Agent, count=1, verbose=False)
    assert set(rows[0]) == {"question", "expected_answer", "actual_response"}
    line = read_sidecar(output)["dataset"]["lines"][0]
    assert (line["question_type"], line["answer_protocol"], rows[0]["expected_answer"]) == (wire, protocol, answer)
    if wire != "free":
        assert all(f"{label}. {text}" in rows[0]["question"] for label, text in zip("ABC", FACTS))
    assert checkpoints == (["generation"] if level == 1 else ["generation", "inventory_audit"])


def test_sample_review_rejects_partial_mapping_and_repairs_a_found_gap(tmp_path):
    source, output = tmp_path / "facts.md", tmp_path / "qa.jsonl"
    source.write_text(" ".join(FACTS), encoding="utf-8")
    requests = []

    class Agent(BatchAgent):
        def __call__(self, request):
            requests.append(request)
            stage = request["stage"]
            if stage == "generation":
                candidate = question(0)
                candidate["coverage_claims"].extend(question(1)["coverage_claims"])
                self.tools["submit_dataset_batch"]([fact(0), fact(1)], [candidate], True)
            elif stage == "inventory_audit":
                assert "knowledge_points" not in request and "questions" not in request
                self.tools["submit_dataset_batch"]([fact(i) for i in range(3)], [], True)
            elif stage == "inventory_reconciliation":
                self.tools["finish_inventory_reconciliation"]("Distinct opening times.", [])
            elif stage == "coverage_review":
                questions = request["questions"]
                self.tools["submit_coverage_review"]([{"knowledge_point_id": p["id"],
                    "answerability": "eligible", "reason": "The question must ask about this specific counter.",
                    "question_ids": [q["id"] for q in questions if p["statement"].split()[-1][:-1] == q["expected_answer"]]}
                    for p in request["points"]])
            elif stage == "coverage_repair":
                targets = set(request["coverage_targets"])
                choices = [question(FACTS.index(p["statement"]), key=p["id"]) for p in request["knowledge_points"]
                           if p["id"] in targets]
                assert len(choices) == 2
                self.tools["submit_dataset_batch"]([], choices, True)
            return self.evidence()

    rows = create_test_dataset(source, output=output, workflow_level=2, skill_agent_factory=Agent, verbose=False)
    metadata = read_sidecar(output)
    assert len(rows) == 3
    assert metadata["coverage"]["summary"]["covered_points"] == 3
    sample = metadata["inventory"]["sample_audit"]
    assert len(sample["selected_batches"]) == len(sample["new_point_ids"]) == 1
    assert "sampled 1 / 1 batches" in render_coverage_report(metadata)
    assert any(request["stage"] == "coverage_repair" for request in requests)


def test_sampling_keeps_priority_and_ordinary_source_batches():
    sources = {str(i): {"text": "If access fails, wait." if i % 2 else "The arch is red."} for i in range(20)}
    batches = source_batches(sources, 70)
    result = select_audit_batches(batches)
    assert result == select_audit_batches(batches)
    assert len(result) == 2
    assert {batch["priority"] for batch in result} == {True, False}
    assert select_audit_batches(batches[:1]) == batches[:1]


def test_resume_reuses_accepted_batches_and_requires_same_level(tmp_path):
    inputs = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for i, path in enumerate(inputs):
        path.write_text(FACTS[i], encoding="utf-8")
    output = tmp_path / "qa.jsonl"
    calls = []

    class Agent(BatchAgent):
        def __call__(self, request):
            source_id = request["source"]["source_id"]
            calls.append(source_id)
            if calls == ["source_001", "source_002"]:
                raise RuntimeError("interrupted")
            index = int(source_id[-1]) - 1
            self.tools["submit_dataset_batch"]([fact(index, source_id=source_id)], [question(index)], True)
            return self.evidence()

    options = dict(output=output, skill_agent_factory=Agent, verbose=False)
    with pytest.raises(RuntimeError, match="interrupted"):
        create_test_dataset(inputs, **options)
    resume = Path(f"{output}.work")
    with pytest.raises(ValueError, match="workflow_level"):
        create_test_dataset(inputs, workflow_level=2, resume=resume, **options)
    assert len(create_test_dataset(inputs, resume=resume, **options)) == 2
    assert calls == ["source_001", "source_002", "source_002"]
    first, second = read_sidecar(output)["executions"]
    assert first["skill_files"] == second["skill_files"]
    assert first["loaded_skills"] == second["loaded_skills"] == ["knowledge-point-qa"]


@pytest.mark.parametrize("legacy_checkpoint", [False, True], ids=["saved-level", "legacy"])
def test_cli_resume_uses_staged_path_for_existing_work(tmp_path, legacy_checkpoint):
    from lladar.api import BUILTIN_SKILL_DIR
    from lladar.cli import main
    from test_knowledge_coverage import CoverageAgent

    source, output = tmp_path / "knowledge.md", tmp_path / "qa.jsonl"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")
    options = dict(output=output, skill=BUILTIN_SKILL_DIR, skill_agent_factory=CoverageAgent, verbose=False)
    rows = create_test_dataset(source, workflow_level=3, **options)
    resume = Path(f"{output}.work")
    if legacy_checkpoint:
        path = resume / "session.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        del saved["data"]["configuration"]["workflow_level"]
        saved["sha256"] = digest(saved["data"])
        path.write_text(json.dumps(saved), encoding="utf-8")

    assert main(["create", "test-dataset", "--knowledge", str(source), "--skill", str(BUILTIN_SKILL_DIR),
                 "--resume", str(resume), "--no-verbose"], skill_agent_factory=CoverageAgent) == 0
    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == rows
    assert read_sidecar(output)["workflow_level"] == 3
