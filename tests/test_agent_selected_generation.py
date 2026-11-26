"""Dataset generation methods exercised through the public generation API."""

import hashlib
import json
from pathlib import Path

import pytest

from coverage_test_support import create_test_dataset
from lladar.exceptions import DatasetValidationError


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("Service A keeps data for 30 days.", encoding="utf-8")
    skill = tmp_path / "direct-qa"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: direct-qa\ndescription: Direct questions.\n---\n"
        "Read the source and generate one question per meaningful passage without a graph.\n",
        encoding="utf-8",
    )
    return source, skill


@pytest.fixture
def python_inputs(inputs):
    """A skill that opts into Akasha's python_execute and therefore candidate-file delivery."""
    source, skill = inputs
    (skill / "SKILL.md").write_text(
        "---\nname: direct-qa\ndescription: Direct questions.\nallowed-tools: python_execute\n---\n"
        "Read the source and generate one question per meaningful passage without a graph.\n",
        encoding="utf-8",
    )
    return source, skill


class DirectAgent:
    """Replace the external model, keeping source and result validation real."""

    def __init__(self, *, skills, tools, **_options):
        self.skill = Path(skills[0])
        self.tools = tools

    def evidence(self):
        return {
            "loaded_skills": [self.skill.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }

    def __call__(self, request):
        for source in self.tools["list_sources"]():
            page = self.tools["read_source"](source["source_id"])
            accepted = self.tools["submit_knowledge_points"]([{
                "statement": page["text"], "topic": "Retention",
                "evidence": [{"read_id": page["read_id"], "quote": page["text"]}],
            }])
            self.tools["submit_qa"]({
                "knowledge_point_id": accepted["accepted"][0],
                "question": "How long does Service A keep data?", "expected_answer": "30 days",
            })
        return self.evidence()


@pytest.mark.parametrize("verbose", [True, False])
def test_generation_previews_are_available_before_agent_returns(inputs, tmp_path, capsys, verbose):
    source, skill = inputs

    class ObservedAgent(DirectAgent):
        def __call__(self, request):
            result = super().__call__(request)
            preview = capsys.readouterr().err
            assert ("candidate qa=" in preview) == verbose
            assert ("How long does Service A keep data?" in preview) == verbose
            assert ("30 days" in preview) == verbose
            assert "[RESPONSE]" not in preview
            return result

    create_test_dataset(source, output=tmp_path / "preview.jsonl", skill=skill,
                        skill_agent_factory=ObservedAgent, verbose=verbose)
    final = capsys.readouterr().err
    assert ("generated record=1" in final) == verbose
    assert "[RESPONSE]" not in final


def test_direct_skill_can_publish_without_a_graph(inputs, tmp_path):
    source, skill = inputs
    output = tmp_path / "dataset.jsonl"

    records = create_test_dataset(
        source, skill=skill, output=output, skill_agent_factory=DirectAgent, verbose=False,
    )

    assert records == [{
        "question": "How long does Service A keep data?", "expected_answer": "30 days", "actual_response": None,
    }]
    assert not Path(str(output) + ".graph.json").exists()
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "complete"
    assert sidecar["dataset"]["lines"][0]["generation_method"] == "direct_qa"


def test_python_candidate_file_can_publish_without_submission_tools(python_inputs, tmp_path):
    source, skill = python_inputs
    output = tmp_path / "python.jsonl"

    class FileAgent(DirectAgent):
        def __call__(self, request):
            text = source.read_text(encoding="utf-8")
            Path(request["candidate_path"]).write_text(json.dumps({
                "reads": [{"read_id": "python_read", "source_id": "source_001",
                           "start_char": 0, "end_char": len(text)}],
                "knowledge_points": [{"id": "python_point", "statement": text, "topic": "Retention",
                                      "evidence": [{"read_id": "python_read", "quote": text}]}],
                "qa": [{"knowledge_point_id": "python_point", "question": "What is A's retention period?",
                        "expected_answer": "30 days"}],
                "method_reason": "The source states one directly answerable fact.",
            }), encoding="utf-8")
            return self.evidence()

    records = create_test_dataset(
        source, skill=skill, output=output, skill_agent_factory=FileAgent, verbose=False,
    )

    assert records == [{"question": "What is A's retention period?", "expected_answer": "30 days", "actual_response": None}]
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert sidecar["validation"]["status"] == "passed"
    assert sidecar["method_reason"] == "The source states one directly answerable fact."


def test_an_empty_graph_is_not_published_even_with_valid_direct_questions(inputs, tmp_path):
    source, skill = inputs
    output = tmp_path / "invalid.jsonl"

    class EmptyGraphAgent(DirectAgent):
        def __call__(self, request):
            result = super().__call__(request)
            try:
                self.tools["submit_semantic_graph"]({"nodes": [], "edges": [], "facts": []})
            except ValueError:
                # Returning an empty graph by Python must face the same final validation.
                import gc
                from lladar.skill_generation import GenerationWorkspace
                workspace = next(obj for obj in gc.get_objects() if isinstance(obj, GenerationWorkspace)
                                 and obj.current_stage == "generation"
                                 and obj.sources["source_001"]["path"] == str(source.resolve()))
                workspace.semantic_graph = {"nodes": [], "edges": [], "facts": []}
            return result

    with pytest.raises(DatasetValidationError, match="graph"):
        create_test_dataset(source, skill=skill, output=output, skill_agent_factory=EmptyGraphAgent, verbose=False)
    assert not output.exists()
    assert not Path(str(output) + ".generation.json").exists()


def test_valid_delivery_survives_agent_recursion_error_without_duplicate_questions(inputs, tmp_path):
    from langgraph.errors import GraphRecursionError

    source, skill = inputs
    output = tmp_path / "recovered.jsonl"

    class ExhaustedAgent(DirectAgent):
        def __call__(self, request):
            super().__call__(request)
            error = GraphRecursionError("Model kept talking after delivery")
            error.skill_evidence = self.evidence()
            raise error

    records = create_test_dataset(
        source, skill=skill, output=output, skill_agent_factory=ExhaustedAgent, verbose=False,
    )

    assert len(records) == 1
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "complete"
    assert sidecar["executions"][0]["error_type"] == "GraphRecursionError"
    assert sidecar["sources"][0]["attempts"] == 1


def test_skill_can_ask_multiple_questions_about_one_meaningful_passage(inputs):
    source, skill = inputs

    class TwoQuestionsAgent(DirectAgent):
        def __call__(self, request):
            result = super().__call__(request)
            point = self.tools["list_knowledge_points"]()[0]
            self.tools["submit_qa"]({"knowledge_point_id": point["id"],
                                    "question": "Which service keeps data for 30 days?",
                                    "expected_answer": "Service A"})
            return result

    records = create_test_dataset(source, skill=skill, skill_agent_factory=TwoQuestionsAgent, verbose=False)
    assert {(row["question"], row["expected_answer"]) for row in records} == {
        ("How long does Service A keep data?", "30 days"),
        ("Which service keeps data for 30 days?", "Service A"),
    }


def test_mixed_methods_never_publish_half_a_controlled_pair_after_deduplication(inputs, tmp_path):
    from test_controlled_variant_dataset import GenericGraphSkillAgent, SOURCE_FACTS

    source, skill = inputs
    source.write_text("\n".join(SOURCE_FACTS), encoding="utf-8")
    output = tmp_path / "mixed.jsonl"

    class MixedAgent(GenericGraphSkillAgent):
        def __call__(self, request):
            result = super().__call__(request)
            point_id = self.tools["list_knowledge_points"]()[0]["id"]
            self.tools["submit_qa"]({
                "knowledge_point_id": point_id,
                "question": "According to the source, how many seats does a plan include for a new customer?",
                "expected_answer": "Starter: 10 seats; Growth: 25 seats",
            })
            return result

    create_test_dataset(source, skill=skill, output=output, controlled_variant_topics=("customer_context",),
                        skill_agent_factory=MixedAgent, verbose=False)
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    pair = [line for line in sidecar["dataset"]["lines"] if line["plan_type"] == "controlled_invariance"]
    assert len(pair) in (0, 2)


def test_force_replacing_a_graph_dataset_with_direct_qa_removes_the_old_graph(inputs, tmp_path):
    from test_generation_skills import SourceGraphAgent, FACTS

    source, skill = inputs
    source.write_text("\n".join(FACTS), encoding="utf-8")
    output = tmp_path / "replaced.jsonl"
    create_test_dataset(source, skill=skill, output=output, skill_agent_factory=SourceGraphAgent, verbose=False)

    records = create_test_dataset(source, skill=skill, output=output, force=True,
                                  skill_agent_factory=DirectAgent, verbose=False)

    assert len(records) == 1
    assert not Path(str(output) + ".graph.json").exists()


def test_explicit_controlled_probe_request_cannot_silently_become_direct_qa(inputs):
    source, skill = inputs
    with pytest.raises(DatasetValidationError, match="controlled"):
        create_test_dataset(source, skill=skill, skill_agent_factory=DirectAgent, verbose=False,
                            controlled_variant_selector=lambda _dimensions: ())


def test_a_requested_typed_question_cannot_be_published_as_a_free_graph_fact(inputs):
    from test_generation_skills import SourceGraphAgent, FACTS

    source, skill = inputs
    source.write_text("\n".join(FACTS), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="question-type"):
        create_test_dataset(source, skill=skill, skill_agent_factory=SourceGraphAgent,
                            question_type="single-choice", verbose=False)


def test_cli_can_complete_direct_qa_and_report_that_no_graph_was_used(inputs, tmp_path, capsys):
    from coverage_test_support import main

    source, skill = inputs
    output = tmp_path / "cli.jsonl"
    assert main(["create", "test-dataset", "--knowledge", str(source), "--skill", str(skill),
                 "--output", str(output), "--no-verbose"], skill_agent_factory=DirectAgent) == 0
    assert "graph_used=false" in capsys.readouterr().out


def test_native_akasha_python_can_deliver_a_candidate_file(python_inputs, tmp_path):
    from langchain_core.messages import AIMessage
    from lladar.skill_agent import AkashaSkillAgent
    from test_skill_agent import ToolScriptModel

    source, skill = python_inputs
    text = source.read_text(encoding="utf-8")
    candidate = {
        "reads": [{"read_id": "python_read", "source_id": "source_001", "start_char": 0, "end_char": len(text)}],
        "knowledge_points": [{"id": "point", "statement": text, "topic": "Retention",
                              "evidence": [{"read_id": "python_read", "quote": text}]}],
        "qa": [{"knowledge_point_id": "point", "question": "How long is A's retention?", "expected_answer": "30 days"}],
    }

    class PythonModelAgent:
        def __init__(self, **options):
            self.options = options

        def __call__(self, request):
            code = ("from pathlib import Path\n"
                    f"Path({request['candidate_path']!r}).write_text({json.dumps(candidate)!r}, encoding='utf-8')")
            model = ToolScriptModel(replies=[
                AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": skill.name}, "id": "load"}]),
                AIMessage(content="", tool_calls=[{"name": "python_execute", "args": {"skill": skill.name, "source": code}, "id": "python"}]),
                AIMessage(content="Delivered."),
            ])
            options = {**self.options, "model": model}
            return AkashaSkillAgent(**options)(request)

    records = create_test_dataset(source, skill=skill, output=tmp_path / "native.jsonl",
                                  skill_agent_factory=PythonModelAgent, verbose=False)
    assert records[0]["expected_answer"] == "30 days"


@pytest.mark.parametrize("graph_method", [False, True])
def test_generated_datasets_continue_through_run_eval_and_report(inputs, tmp_path, graph_method):
    from lladar.runner import run_agent
    from lladar.evaluation import evaluate
    from lladar.reporting import create_report
    from test_controlled_variant_dataset import GenericGraphSkillAgent, SOURCE_FACTS
    from test_semantic_graph_probes import AllCasesStrategyAgent, ReportAgent
    from test_simple_pipeline import EvaluationSkillAgent

    source, skill = inputs
    if graph_method:
        source.write_text("\n".join(SOURCE_FACTS), encoding="utf-8")
    dataset, responses, evaluation_path = (tmp_path / name for name in ("dataset.jsonl", "responses.jsonl", "evaluation.json"))
    records = create_test_dataset(source, skill=skill, output=dataset,
                                  controlled_variant_topics=("customer_context",) if graph_method else (),
                                  skill_agent_factory=GenericGraphSkillAgent if graph_method else DirectAgent, verbose=False)
    answers = {row["question"]: row["expected_answer"] for row in records}
    run_agent(dataset, responses, answer=lambda question: answers[question],
              strategy_agent_factory=AllCasesStrategyAgent, verbose=False)
    evaluation = evaluate(responses, output=evaluation_path, skill_agent_factory=EvaluationSkillAgent)
    report = create_report(evaluation_path, tmp_path / "report.md", skill_agent_factory=ReportAgent).read_text(encoding="utf-8")
    assert all(item["status"] == "evaluated" for item in evaluation["items"])
    assert ("## Semantic probes" in report) is graph_method


@pytest.mark.parametrize("corruption", ["graph_evidence", "source_quote"])
def test_final_validation_rejects_python_mutations_and_preserves_old_output(inputs, tmp_path, corruption):
    import gc
    from lladar.skill_generation import GenerationWorkspace
    from test_generation_skills import SourceGraphAgent, FACTS

    source, skill = inputs
    source.write_text("\n".join(FACTS), encoding="utf-8")
    output = tmp_path / "protected.jsonl"
    output.write_text("previous output", encoding="utf-8")

    class MutatingAgent(SourceGraphAgent):
        def __call__(self, request):
            if not request["existing_results"]["graph_present"]:
                super().__call__(request)
            workspace = next(obj for obj in gc.get_objects() if isinstance(obj, GenerationWorkspace)
                             and obj.current_stage == "generation"
                             and obj.sources["source_001"]["path"] == str(source.resolve()))
            if corruption == "graph_evidence":
                workspace.semantic_graph["facts"][0]["evidence_ref"] = "missing_point"
            else:
                workspace.points["kp_000001"]["evidence"][0]["quote"] = "Invented retention period."
            return {"loaded_skills": [self.name], "skill_files": {
                "SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}

    with pytest.raises(DatasetValidationError):
        create_test_dataset(source, skill=skill, output=output, force=True,
                            skill_agent_factory=MutatingAgent, verbose=False)
    assert output.read_text(encoding="utf-8") == "previous output"


@pytest.mark.parametrize("question_type,protocol,correct,expected", [
    ("single-choice", "one_option_id", ["A"], "A"),
    ("multiple-choice", "option_id_list", ["A", "B"], "A,B"),
    ("ranking", "ordered_option_ids", ["A", "B", "C"], "A>B>C"),
])
def test_direct_typed_qa_preserves_its_contract_without_a_graph(inputs, tmp_path, question_type, protocol, correct, expected):
    source, skill = inputs
    facts = ["A keeps data for 30 days.", "B keeps data for 60 days.", "C keeps data for 90 days."]
    source.write_text("\n".join(facts), encoding="utf-8")
    output = tmp_path / "typed.jsonl"

    class TypedAgent(DirectAgent):
        def __call__(self, request):
            page = self.tools["read_source"]("source_001")
            ids = self.tools["submit_knowledge_points"]([
                {"statement": fact, "topic": "Retention", "evidence": [{"read_id": page["read_id"], "quote": fact}]}
                for fact in facts
            ])["accepted"]
            options = [{"id": label, "text": fact, "knowledge_point_id": point}
                       for label, fact, point in zip("ABC", facts, ids)]
            question = ("Order the services by retention." if question_type == "ranking" else
                        "Which services keep data for at most 60 days?" if question_type == "multiple-choice" else
                        "Which service keeps data for 30 days?")
            record = {"knowledge_point_id": ids[0], "knowledge_point_ids": ids,
                      "question": question + "\n" + "\n".join(f"{opt['id']}. {opt['text']}" for opt in options),
                      "expected_answer": expected, "question_type": question_type.replace("-", "_"),
                      "answer_protocol": protocol, "options": options, "correct_option_ids": correct}
            if question_type == "ranking":
                record.update(ranking_axis="retention days", direction="ascending")
            self.tools["submit_qa"](record)
            return self.evidence()

    records = create_test_dataset(source, skill=skill, output=output, question_type=question_type,
                                  skill_agent_factory=TypedAgent, verbose=False)
    assert records[0]["expected_answer"] == expected
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert sidecar["dataset"]["lines"][0]["answer_protocol"] == protocol
    assert not Path(str(output) + ".graph.json").exists()


def test_valid_python_delivery_can_replace_earlier_rejected_tool_candidates(python_inputs):
    source, skill = python_inputs

    class RepairedAgent(DirectAgent):
        def __call__(self, request):
            page = self.tools["read_source"]("source_001")
            self.tools["submit_knowledge_points"]([{
                "statement": "Incorrect draft", "topic": "Retention",
                "evidence": [{"read_id": page["read_id"], "quote": "Not in the source"}],
            }])
            candidate = {
                "reads": [{"read_id": "file_read", "source_id": "source_001", "start_char": 0, "end_char": len(page["text"])}],
                "knowledge_points": [{"id": "point", "statement": page["text"], "topic": "Retention",
                                      "evidence": [{"read_id": "file_read", "quote": page["text"]}]}],
                "qa": [{"knowledge_point_id": "point", "question": "How long does A keep data?", "expected_answer": "30 days"}],
            }
            Path(request["candidate_path"]).write_text(json.dumps(candidate), encoding="utf-8")
            return self.evidence()

    records = create_test_dataset(source, skill=skill, skill_agent_factory=RepairedAgent, verbose=False)
    assert records[0]["expected_answer"] == "30 days"


def test_free_qa_schema_feedback_allows_the_agent_to_repair_its_submission(inputs):
    source, skill = inputs

    class RepairingAgent(DirectAgent):
        def __call__(self, request):
            super().__call__(request)
            with pytest.raises(ValueError, match="Free QA requires exactly knowledge_point_id, question, expected_answer"):
                self.tools["submit_qa"]({
                    "knowledge_point_id": "kp_000001", "question": "How long does Service A keep data?",
                    "expected_answer": "30 days", "question_type": "free", "actual_response": None,
                })
            self.tools["submit_qa"]({
                "knowledge_point_id": "kp_000001", "question": "How long does Service A keep data?",
                "expected_answer": "30 days",
            })
            return self.evidence()

    records = create_test_dataset(source, skill=skill, skill_agent_factory=RepairingAgent, verbose=False)
    assert len(records) == 1


def test_graph_fact_feedback_explains_the_entity_label_contract(inputs):
    source, skill = inputs

    class RepairingGraphAgent(DirectAgent):
        def __call__(self, request):
            super().__call__(request)
            graph = {
                "nodes": [{"id": "a", "type": "entity", "label": "Service A", "origin": "source", "evidence_refs": ["kp_000001"]}],
                "edges": [],
                "facts": [{"entity_id": "a", "label": "Retention", "value": "30", "unit": "days", "evidence_ref": "kp_000001"}],
            }
            with pytest.raises(ValueError, match="label must exactly match the entity node label"):
                self.tools["submit_semantic_graph"](graph)
            graph["facts"][0]["label"] = "Service A"
            self.tools["submit_semantic_graph"](graph)
            self.tools["read_semantic_graph"]()
            self.tools["submit_test_plans"]([{"type": "direct_fact", "entity_id": "a", "question": "What is Service A's retention period?", "expected_answer": "30 days"}])
            return self.evidence()

    rows = create_test_dataset(source, skill=skill, skill_agent_factory=RepairingGraphAgent, verbose=False)
    assert len(rows) == 2


def test_candidate_file_delivery_is_not_offered_without_python_opt_in(inputs):
    source, skill = inputs
    requests = []

    class RecordingAgent(DirectAgent):
        def __call__(self, request):
            requests.append(request)
            return super().__call__(request)

    records = create_test_dataset(source, skill=skill, skill_agent_factory=RecordingAgent, verbose=False)
    assert records[0]["expected_answer"] == "30 days"
    assert requests and all("candidate_path" not in request for request in requests)


def test_invalid_candidate_file_falls_back_to_accepted_tool_submissions(python_inputs):
    source, skill = python_inputs
    attempts = []

    class BrokenFileAgent(DirectAgent):
        def __call__(self, request):
            attempts.append(request["attempt"])
            result = super().__call__(request)
            Path(request["candidate_path"]).write_text(json.dumps({"qa": [], "unexpected": True}), encoding="utf-8")
            return result

    records = create_test_dataset(source, skill=skill, skill_agent_factory=BrokenFileAgent, verbose=False)
    assert records[0]["expected_answer"] == "30 days"
    assert attempts == [1]


def _failing(error, agent):
    error.skill_evidence = {**agent.evidence(), "input_tokens": 400}
    return error


def test_budget_exhaustion_keeps_accepted_submissions_without_retrying(inputs):
    from lladar.exceptions import SkillBudgetExceeded

    source, skill = inputs
    attempts = []

    class SpendingAgent(DirectAgent):
        def __call__(self, request):
            attempts.append(request["attempt"])
            super().__call__(request)
            raise _failing(SkillBudgetExceeded("budget"), self)

    records = create_test_dataset(source, skill=skill, skill_agent_factory=SpendingAgent, verbose=False)
    assert records[0]["expected_answer"] == "30 days"
    assert attempts == [1]


def test_budget_exhaustion_without_results_stops_instead_of_retrying(inputs):
    from lladar.exceptions import SkillBudgetExceeded

    source, skill = inputs
    attempts = []

    class SpendingAgent(DirectAgent):
        def __call__(self, request):
            attempts.append(request["attempt"])
            raise _failing(SkillBudgetExceeded("budget"), self)

    with pytest.raises(SkillBudgetExceeded):
        create_test_dataset(source, skill=skill, skill_agent_factory=SpendingAgent, verbose=False)
    assert attempts == [1]


def test_round_limit_is_retried_once_with_the_remaining_token_budget(inputs, monkeypatch):
    from langgraph.errors import GraphRecursionError

    monkeypatch.setenv("LLADAR_SKILL_ITEM_INPUT_TOKENS", "1000")
    source, skill = inputs
    budgets = []

    class LoopingAgent(DirectAgent):
        def __init__(self, **options):
            budgets.append(options.get("input_token_budget"))
            super().__init__(**options)

        def __call__(self, request):
            raise _failing(GraphRecursionError("rounds"), self)

    with pytest.raises(DatasetValidationError):
        create_test_dataset(source, skill=skill, skill_agent_factory=LoopingAgent, verbose=False)
    assert budgets == [1000, 600]


def test_retry_request_lists_only_the_remaining_work(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("Service A keeps data for 30 days. Service B keeps data for 7 days.", encoding="utf-8")
    skill = tmp_path / "direct-qa"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: direct-qa\ndescription: Direct questions.\n---\nAsk.\n",
                                    encoding="utf-8")
    requests = []

    class PartialAgent(DirectAgent):
        def __call__(self, request):
            requests.append(request)
            if request["attempt"] == 1:
                page = self.tools["read_source"]("source_001", 0, 33)
                accepted = self.tools["submit_knowledge_points"]([{
                    "statement": page["text"], "topic": "A", "evidence": [{"read_id": page["read_id"], "quote": page["text"]}]}])
                self.tools["submit_knowledge_points"]([{
                    "statement": "Service A retention", "topic": "A",
                    "evidence": [{"read_id": page["read_id"], "quote": "30 days"}]}])
                self.tools["submit_qa"]({"knowledge_point_id": accepted["accepted"][0],
                                         "question": "How long does A keep data?", "expected_answer": "30 days"})
                return self.evidence()
            return super().__call__(request)

    create_test_dataset(source, skill=skill, skill_agent_factory=PartialAgent, verbose=False)
    assert "remaining_work" not in requests[0]
    remaining = requests[1]["remaining_work"]
    assert remaining["unread_ranges"] == {"source_001": [[33, len(source.read_text(encoding="utf-8"))]]}
    assert remaining["accepted_qa_count"] == 1
    assert remaining["knowledge_points_without_qa"] == ["kp_000002"]
    assert "unread ranges" in remaining["problems"][0]


def test_existing_results_are_bounded_outside_inventory_mode(inputs):
    source, skill = inputs
    sizes = []

    class BusyAgent(DirectAgent):
        def __call__(self, request):
            sizes.append(len(json.dumps(request["existing_results"], ensure_ascii=False).encode("utf-8")))
            if request["attempt"] == 1:
                super().__call__(request)
                page = self.tools["read_source"]("source_001")
                point = self.tools["submit_knowledge_points"]([{
                    "statement": page["text"], "topic": "Retention",
                    "evidence": [{"read_id": page["read_id"], "quote": page["text"]}]}])["accepted"][0]
                for index in range(200):
                    self.tools["submit_qa"]({"knowledge_point_id": point, "question": f"Variant {index} " + "x" * 80 + "?",
                                             "expected_answer": "30 days"})
                raise _failing(RuntimeError("stop"), self)
            return super().__call__(request)

    create_test_dataset(source, skill=skill, skill_agent_factory=BusyAgent, verbose=False)
    assert sizes[-1] <= 6000
