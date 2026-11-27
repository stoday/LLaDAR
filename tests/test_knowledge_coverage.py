"""Coverage behavior through the agreed public dataset and CLI interfaces."""

import hashlib
import json
from functools import partial
from pathlib import Path

import pytest

from lladar.api import create_test_dataset as generate_dataset
from lladar.cli import main
from lladar.exceptions import DatasetValidationError

create_test_dataset = partial(generate_dataset, workflow_level=3)


class CoverageAgent:
    """An external model substitute for a worked twenty-fact example."""

    def __init__(self, *, skills, tools, **options):
        self.skill = Path(skills[0])
        self.tools = tools

    def evidence(self):
        return {
            "loaded_skills": [self.skill.name],
            "skill_files": {"SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()},
        }

    def __call__(self, request):
        if request["stage"] == "inventory_reconciliation":
            self.tools["finish_inventory_reconciliation"](reason="Fixture fact boundaries and duplicates checked.", issues=[])
            return self.evidence()
        if request["stage"] in {"inventory", "inventory_audit"}:
            page = request["source"]
            points = []
            for line in page["text"].splitlines():
                if not line.strip():
                    continue
                points.append({"statement": line, "topic": "Retention",
                    "evidence": [{"source_id": page["source_id"],
                        "start_char": page["start_char"] + page["text"].index(line), "quote": line}]})
            self.tools["submit_inventory_batch"](points=points, pending=[], reason="Every complete fact in this page was inventoried.", complete=True)
        elif request["stage"] == "coverage_review":
            questions = request["questions"]
            if "list_coverage_questions" in self.tools:
                questions = []
                cursor = 0
                while True:
                    page = self.tools["list_coverage_questions"](start=cursor, limit=4)
                    questions.extend(page["questions"])
                    if page["next_start"] is None:
                        break
                    cursor = page["next_start"]
            judgments = []
            for point in request["points"]:
                number = int(point["statement"].split()[1])
                matches = [q["id"] for q in questions if q["question"] == f"How long does Service {number} keep data?"]
                judgments.append({"knowledge_point_id": point["id"],
                    "answerability": "excluded" if number > 18 else "eligible",
                    "question_ids": matches,
                    "reason": "The scenario cannot identify this service." if number > 18 else "The question asks this retention fact and its answer states the duration."})
            self.tools["submit_coverage_review"](judgments)
        else:
            for source in self.tools["list_sources"]():
                page = self.tools["read_source"](source["source_id"])
                for number in range(1, 17):
                    if f"Service {number} retains data for {number} days." not in page["text"]:
                        continue
                    receipt = self.tools["submit_question"](
                        request_id=f"service-{number}",
                        question=f"How long does Service {number} keep data?",
                        expected_answer=f"{number} days", source_refs=[page["source_refs"][0]["ref_id"]],
                        answer_basis="derived_criterion", rationale=f"Service {number} retains data for {number} days.",
                    )
                    assert receipt["status"] == "accepted"
        return self.evidence()


def test_independent_inventory_is_the_denominator_and_exclusions_are_reviewed(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    records = create_test_dataset(source, output=output, skill_agent_factory=CoverageAgent, verbose=False)

    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert len(records) == 16
    assert len(saved["inventory"]["points"]) == 20
    assert saved["coverage"]["summary"] == {
        "total_points": 20, "eligible_points": 18, "covered_points": 16,
        "excluded_points": 2, "pending_points": 2, "pending_review_points": 0,
        "output_records": 16, "all_coverage": 0.8, "eligible_coverage": 16 / 18,
    }
    assert {p.name for p in tmp_path.iterdir() if p.is_file()} == {
        "knowledge.md", "dataset.jsonl", "dataset.jsonl.generation.json",
    }


class AnnotatedCoverageAgent(CoverageAgent):
    def question_payload(self, request, number):
        point = next(p for p in request["inventory_points"]["points"] if p["statement"].split()[1] == str(number))
        evidence = self.tools["read_source"](**point["evidence_ranges"][0])
        return {"question": f"How long does Service {number} keep data?", "expected_answer": f"{number} days",
                "source_refs": [evidence["source_refs"][0]["ref_id"]], "answer_basis": "derived_criterion",
                "rationale": point["statement"], "coverage_claims": [{"knowledge_point_id": point["id"], "covers_entire_point": True,
                    "reason": "The question requires the service's retention duration to be correct."}]}


@pytest.mark.parametrize("certifies_full_point", [True, False])
def test_annotations_follow_accepted_question_revisions_and_final_selection(tmp_path, certifies_full_point):
    source = tmp_path / "knowledge.md"
    source.write_text("\n".join(f"Service {n} retains data for {n} days." for n in range(1, 6)), encoding="utf-8")

    class Author(AnnotatedCoverageAgent):
        def __call__(self, request):
            if request["stage"] != "generation":
                return super().__call__(request)
            self.tools["read_source"]("source_001")
            first, second, third, fourth = [self.question_payload(request, number) for number in range(1, 5)]
            second["coverage_claims"][0]["covers_entire_point"] = certifies_full_point
            submit = self.tools["submit_question"]
            unknown = [{"knowledge_point_id": "kp_000001", "reason": "A local evidence ID is invalid."}]
            assert submit(request_id="unknown", **{**first, "coverage_claims": unknown})["issues"][0]["field"] == "coverage_claims"
            wrong_range = {**second, "source_refs": first["source_refs"]}
            assert submit(request_id="other-range", **wrong_range)["issues"][0]["field"] == "coverage_claims"
            assert submit(request_id="first", **first)["status"] == "accepted"
            duplicate = {**third, "question": first["question"], "expected_answer": first["expected_answer"]}
            assert submit(request_id="duplicate", **duplicate)["status"] == "accepted"
            draft = submit(request_id="draft", **third)
            revise = self.tools["revise_question"]
            revision = {"item_id": draft["item_id"], "revision": draft["revision"], "request_id": "revision", **second}
            saved = revise(**revision)
            assert saved["status"] == "accepted" and revise(**revision) == saved
            assert submit(request_id="past-cap", **fourth)["status"] == "accepted"
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    rows = create_test_dataset(source, output=output, count=2, skill_agent_factory=Author, verbose=False)
    sidecar = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert [row["expected_answer"] for row in rows] == ["1 days", "2 days"]
    lines = sidecar["dataset"]["lines"]
    assert [line["inventory_coverage"]["claims"][0]["knowledge_point_id"] for line in lines] == ["ikp_000001", "ikp_000002"]
    reviews = sidecar["coverage"]["reviews"]
    assert [(row["review_method"], bool(row["question_ids"])) for row in reviews] == [
        ("review", True), ("generation" if certifies_full_point else "review", True),
        ("review", False), ("review", False), ("review", False),
    ]
    assert sidecar["coverage"]["summary"]["all_coverage"] == 0.4
    report = tmp_path / "coverage.md"
    assert main(["report", str(output) + ".generation.json", "--output", str(report), "--lang", "zh-TW", "--no-verbose"]) == 0
    if certifies_full_point:
        assert "1 點採用出題註記與程式引文驗證，其餘 1 點經另外覆核" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("publish", [False, True])
def test_coverage_repair_only_reviews_points_with_changed_candidate_questions(tmp_path, publish):
    source = tmp_path / "knowledge.md"
    source.write_text("\n".join(f"Service {n} retains data for {n} days." for n in range(1, 6)), encoding="utf-8")
    reviewed = []

    class Author(AnnotatedCoverageAgent):
        def __call__(self, request):
            if request["stage"] == "coverage_review":
                reviewed.append([point["id"] for point in request["points"]])
            if request["stage"] != "generation":
                return super().__call__(request)
            self.tools["read_source"]("source_001")
            round_number = request.get("coverage_round", 0)
            numbers = {0: [2, 3], 1: [1, 4], 2: []}[round_number]
            for number in numbers:
                assert self.tools["submit_question"](request_id=f"service-{number}",
                    **self.question_payload(request, number))["status"] == "accepted"
            return self.evidence()

    rows = create_test_dataset(source, output=tmp_path / "dataset.jsonl" if publish else None,
                               skill_agent_factory=Author, verbose=False)
    assert [row["expected_answer"] for row in rows] == ["2 days", "3 days", "1 days", "4 days"]
    assert reviewed == [["ikp_000001", "ikp_000004", "ikp_000005"], ["ikp_000001"]]


def test_a_question_can_cover_multiple_independent_points_after_review(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n".join(f"Service {n} retains data for {n} days." for n in range(1, 4)), encoding="utf-8")

    class Author(AnnotatedCoverageAgent):
        def __call__(self, request):
            if request["stage"] == "coverage_review":
                question_id = request["questions"][0]["id"]
                reviews = []
                for point in request["points"]:
                    question_ids = [q["id"] for q in self.tools["list_coverage_questions"](
                        knowledge_point_id=point["id"])["questions"]]
                    expected = [] if "Service 1" in point["statement"] else [question_id]
                    assert question_ids == expected
                    reviews.append({"knowledge_point_id": point["id"], "answerability": "eligible",
                                    "question_ids": question_ids, "reason": "Only Services 2 and 3 are requested."})
                self.tools["submit_coverage_review"](reviews)
                return self.evidence()
            if request["stage"] != "generation":
                return super().__call__(request)
            self.tools["read_source"]("source_001")
            second, third = [self.question_payload(request, number) for number in (2, 3)]
            receipt = self.tools["submit_question"](request_id="compare-retention",
                question="How long do Services 2 and 3 each keep data?", expected_answer="2 and 3 days, respectively.",
                source_refs=second["source_refs"] + third["source_refs"], answer_basis="derived_criterion",
                rationale="Both retention periods are required.", coverage_claims=second["coverage_claims"] + third["coverage_claims"])
            assert receipt["status"] == "accepted"
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, count=1, skill_agent_factory=Author, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["covered_points"] == 2
    assert all(review["review_method"] == "review" for review in saved["coverage"]["reviews"])


def test_cli_report_reads_saved_inventory_and_highlights_gaps(tmp_path, capsys):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=dataset, skill_agent_factory=CoverageAgent, verbose=False)
    report = tmp_path / "report.md"

    assert main(["report", str(dataset) + ".generation.json", "--output", str(report),
                 "--lang", "zh-TW", "--no-verbose"]) == 0
    assert "題目覆蓋報告" in capsys.readouterr().out

    text = report.read_text(encoding="utf-8")
    assert "全部已盤點知識點" in text
    assert "16 / 20" in text and "80%" in text
    assert "16 / 18" in text and "88.9%" in text
    assert "Service 17 retains data" in text
    assert "Service 19 retains data" in text
    assert "Service 1 retains data" not in text


def test_large_source_preserves_a_fact_split_across_inventory_batches(tmp_path):
    fact = "1998 年，該建築被指定為市定古蹟。"
    text = "序" * 245 + fact + "\n\n" + "尾" * 300
    source = tmp_path / "large.md"
    source.write_text(text, encoding="utf-8")
    skill = tmp_path / "boundary-facts"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: boundary-facts\ndescription: Source facts.\n---\nInventory complete source facts, then ask natural questions.", encoding="utf-8")

    class BoundaryAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] in {"inventory", "inventory_audit"}:
                page = request["source"]
                assert len(page["text"]) <= 512
                points, pending = [], []
                if fact in page["text"]:
                    points = [{"statement": fact, "topic": "Building",
                        "evidence": [{"source_id": page["source_id"], "start_char": 245, "quote": fact}]}]
                elif "1998" in page["text"]:
                    pending = [{"source_id": page["source_id"], "start_char": 245,
                                "end_char": page["end_char"], "reason": "The designation event continues on the next page."}]
                self.tools["submit_inventory_batch"](points=points, pending=pending, reason="Processed the supplied page.", complete=True)
            elif request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{
                    "knowledge_point_id": request["points"][0]["id"], "answerability": "eligible",
                    "question_ids": [request["questions"][0]["id"]], "reason": "Question asks the full designation event."}])
            else:
                assigned = self.tools["list_sources"]()[0]
                assert assigned["assigned_end"] - assigned["assigned_start"] <= 512
                sid = assigned["source_id"]
                start = 0
                while True:
                    page = self.tools["read_source"](sid, start_char=start)
                    if page["next_start"] is None:
                        break
                    start = page["next_start"]
                if assigned["assigned_start"] <= 245 < assigned["assigned_end"]:
                    evidence = self.tools["read_source"](sid, start_char=245, end_char=245 + len(fact))
                    receipt = self.tools["submit_question"](request_id="designation", question="這棟是什麼時候被指定為哪一級古蹟的？",
                        expected_answer=fact, source_refs=[evidence["source_refs"][0]["ref_id"]],
                        answer_basis="derived_criterion", rationale=fact)
                    assert receipt["status"] == "accepted"
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill=skill, skill_agent_factory=BoundaryAgent,
                        max_input_tokens=2048, max_output_tokens=1024, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert len(saved["inventory"]["batches"]) >= 2
    assert saved["inventory"]["points"][0]["statement"] == fact
    assert saved["inventory"]["status"] == "complete"
    assert saved["coverage"]["summary"]["total_points"] == 1


def test_independent_source_audit_adds_a_missing_fact_and_replenishes_its_question(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")

    class AuditAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] == "inventory":
                # The initial source extraction misses a fact. The independent
                # source pass must discover it without using questions as input.
                page = request["source"]
                points = []
                for line in page["text"].splitlines():
                    if line.strip() and not line.startswith("Service 17 "):
                        points.append({"statement": line, "topic": "Retention",
                            "evidence": [{"source_id": page["source_id"],
                                "start_char": page["text"].index(line), "quote": line}]})
                self.tools["submit_inventory_batch"](points=points, pending=[], reason="Inventoried the facts found in this pass.", complete=True)
            elif request.get("coverage_repair"):
                assert "read_inventory_point" in self.tools
                sid = self.tools["list_sources"]()[0]["source_id"]
                page = self.tools["read_source"](sid)
                self.tools["submit_question"](request_id="service-17", question="How long does Service 17 keep data?",
                    expected_answer="17 days", source_refs=[page["source_refs"][0]["ref_id"]],
                    answer_basis="derived_criterion", rationale="Service 17 retains data for 17 days.")
            else:
                return super().__call__(request)
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    records = create_test_dataset(source, output=output, skill_agent_factory=AuditAgent, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert len(records) == 17
    assert saved["inventory"]["revision"] == 2
    assert saved["coverage"]["audit_status"] == "complete"
    assert saved["coverage"]["summary"]["total_points"] == 20
    assert saved["coverage"]["summary"]["all_coverage"] == 0.85


def test_generation_author_can_use_a_separate_inventory_and_review_provider(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")

    class Author(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            assert request["stage"] == "generation"
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    records = create_test_dataset(source, output=output, skill_agent_factory=Author,
        coverage_agent_factory=CoverageAgent, verbose=False)
    assert len(records) == 16


def test_large_review_uses_bounded_requests_and_paged_question_access(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")

    class BudgetAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] == "coverage_review":
                assert len(json.dumps(request, ensure_ascii=False).encode("utf-8")) <= 4096
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=BudgetAgent,
                        max_input_tokens=4096, max_output_tokens=2048, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["all_coverage"] == 0.8


def test_semantic_duplicate_facts_merge_without_losing_source_evidence(tmp_path):
    statements = ["The building was a police station.", "Originally the building served as the police station."]
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(statements), encoding="utf-8")

    class DuplicateAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                points = request["points"]
                assert request["all_points_assigned"]
                assert all("evidence" in point for point in points)
                if len(points) > 1:
                    self.tools["revise_inventory_point"](knowledge_point_id=points[0]["id"],
                        statement=statements[0], topic="Building", evidence=points[0]["evidence"],
                        merge_ids=[point["id"] for point in points[1:]], reason="Both passages state the same previous use.")
                return super().__call__(request)
            if request["stage"] in {"inventory", "inventory_audit"}:
                assert set(self.tools) == {"submit_inventory_batch", "finish_inventory_batch"}
                page = request["source"]
                points = [{"statement": fact, "topic": "Building", "evidence": [{
                    "source_id": page["source_id"], "quote": fact,
                }]} for fact in statements]
                self.tools["submit_inventory_batch"](points=points, pending=[], reason="Read summary and body.", complete=True)
            elif request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{
                    "knowledge_point_id": p["id"], "answerability": "eligible",
                    "question_ids": [request["questions"][0]["id"]], "reason": "Question tests previous use.",
                } for p in request["points"]])
            else:
                page = self.tools["read_source"](self.tools["list_sources"]()[0]["source_id"])
                self.tools["submit_question"](request_id="previous-use", question="What was this building used for?",
                    expected_answer="A police station.", source_refs=[page["source_refs"][0]["ref_id"]],
                    answer_basis="derived_criterion", rationale=statements[0])
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=DuplicateAgent, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["total_points"] == 1
    assert "reused" not in saved["inventory"]["reconciliation"]["audit"]
    assert len(saved["inventory"]["points"][0]["evidence"]) == 2
    assert saved["inventory"]["changes"]


def test_complete_previous_paragraph_can_supply_the_subject_of_a_later_fact(tmp_path):
    subject = "Architect Umezawa designed the building."
    later = "He worked for Tainan Prefecture."
    source = tmp_path / "knowledge.md"
    text = subject + " " + "x" * 200 + "\n\n" + later
    source.write_text(text, encoding="utf-8")

    class ContextAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] in {"inventory", "inventory_audit"}:
                page = request["source"]
                original = {"source_id": page["source_id"], "start_char": 0, "quote": subject}
                if subject in page["text"]:
                    self.tools["submit_inventory_batch"](points=[], pending=[], context=[original], reason="Retain the named subject for the next paragraph.", complete=True)
                else:
                    assert request["context"][0]["quote"] == subject
                    self.tools["submit_inventory_batch"](points=[{
                        "statement": "Umezawa worked for Tainan Prefecture.", "topic": "Architect",
                        "evidence": [original, {"source_id": page["source_id"], "start_char": text.index(later), "quote": later}],
                    }], pending=[], reason="Resolved the source pronoun from prior context.", complete=True)
            elif request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{"knowledge_point_id": p["id"], "answerability": "eligible",
                    "question_ids": [request["questions"][0]["id"]], "reason": "Question asks the employer."} for p in request["points"]])
            else:
                page = self.tools["read_source"](self.tools["list_sources"]()[0]["source_id"])
                self.tools["submit_question"](request_id="employer", question="Who employed Umezawa?", expected_answer="Tainan Prefecture",
                    source_refs=[page["source_refs"][0]["ref_id"]], answer_basis="derived_criterion", rationale="Umezawa worked for Tainan Prefecture.")
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=ContextAgent,
                        max_input_tokens=2048, max_output_tokens=1024, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["inventory"]["points"][0]["statement"] == "Umezawa worked for Tainan Prefecture."
    assert len(saved["inventory"]["points"][0]["evidence"]) == 2


def test_inventory_resume_keeps_accepted_points_after_provider_failure(tmp_path):
    from lladar.exceptions import ProviderError

    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")

    class Interrupted(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            evidence = super().__call__(request)
            if request["stage"] == "inventory" and request["source"]["start_char"] > 0:
                raise ProviderError("Synthetic model interruption after accepted facts.")
            return evidence

    output = tmp_path / "dataset.jsonl"
    with pytest.raises(ProviderError):
        create_test_dataset(source, output=output, skill_agent_factory=Interrupted,
                            max_input_tokens=2048, max_output_tokens=1024, verbose=False)
    assert not output.exists()
    records = create_test_dataset(source, output=output, skill_agent_factory=CoverageAgent,
        max_input_tokens=2048, max_output_tokens=1024, verbose=False, resume=Path(str(output) + ".work"))
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert len(records) == 16
    assert len({p["id"] for p in saved["inventory"]["points"]}) == 20
    assert saved["coverage"]["summary"]["all_coverage"] == 0.8


def test_no_verbose_reaches_the_inventory_model_provider(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")

    class QuietProvider(CoverageAgent):
        def __init__(self, *, verbose, **options):
            assert verbose is False
            super().__init__(**options)

    create_test_dataset(source, output=tmp_path / "dataset.jsonl", skill_agent_factory=CoverageAgent,
                        coverage_agent_factory=QuietProvider, count=1, verbose=False)


def test_native_inventory_limits_tools_and_loads_only_scoped_policy(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    from types import SimpleNamespace

    from lladar.knowledge_coverage import KnowledgeCoverage
    from lladar.model_profiles import resolve_model_profile
    from lladar.skill_agent import AkashaSkillAgent
    from lladar.token_usage import token_usage_stage, usage_snapshot
    from test_skill_agent import ToolScriptModel

    skill = tmp_path / "scoped-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: scoped-example\ndescription: Example method.\nmetadata:\n"
        "  lladar-inventory: INVENTORY-POLICY\n  lladar-inventory-audit: AUDIT-POLICY\n"
        "  lladar-coverage-review: REVIEW-POLICY\n---\nGENERATION-POLICY\n", encoding="utf-8")
    fact = "The archive is open."

    def submit(call_id, *, complete=True, **offset):
        return AIMessage(content="", tool_calls=[{"name": "submit_inventory_batch", "id": call_id,
            "args": {"points": [{"statement": fact, "topic": "Archive", "evidence": [
                {"source_id": "source_001", "quote": fact, **offset}]}],
                "pending": [], "reason": "One source fact.", "complete": complete}}])

    finish_call = {"name": "finish_inventory_reconciliation", "id": "finish",
                   "args": {"reason": "The fact is already atomic.", "issues": []}}
    model = ToolScriptModel(replies=[
        submit("ambiguous"), submit("partial", start_char=0, complete=False), submit("explicit", start_char=0),
        AIMessage(content="", tool_calls=[finish_call,
            {"name": "list_inventory_points", "id": "list", "args": {}}]),
        AIMessage(content="", tool_calls=[{**finish_call, "id": "finish-alone"}]),
        submit("audit", start_char=0),
        AIMessage(content="", tool_calls=[{"name": "submit_coverage_review", "id": "review", "args": {
            "judgments": [{"knowledge_point_id": "ikp_000001", "answerability": "eligible",
                           "question_ids": ["q1"], "reason": "It asks whether the archive is open."}]}}]),
    ])
    for reply in model.replies:
        reply.usage_metadata = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}

    def with_scripted_model(**options):
        return AkashaSkillAgent(**{**options, "model": model})

    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent", with_scripted_model)
    coverage = KnowledgeCoverage([(tmp_path / "source.md", f"{fact}\n{fact}")], skill=skill,
        agent_factory=None, model="offline", env_file="", profile=resolve_model_profile("offline"), verbose=False)
    result = SimpleNamespace(records=[{"question": "Is the archive open?", "expected_answer": "Yes.",
        "actual_response": None}], provenance={"sources": coverage.inventory["sources"],
        "dataset": {"lines": [{"qa_id": "q1", "record_fingerprint": "saved-question"}]}})

    @token_usage_stage
    def run_coverage(*, verbose):
        coverage.inventory_sources()
        coverage.reconcile("initial")
        coverage.inventory_sources(audit=True)
        coverage.reconcile("audit")
        coverage.review(result)
        return usage_snapshot()

    usage = run_coverage(verbose=False)
    assert usage["reported_calls"] == 7 and usage["unreported_calls"] == 0
    assert len(model.prompts) == 7 and not model.replies
    assert model.offered[:3] == [["submit_inventory_batch", "finish_inventory_batch"]] * 3
    assert "INVENTORY-POLICY" in str(model.prompts[0]) and "AUDIT-POLICY" not in str(model.prompts[0])
    assert "quote must be unique" in str(model.prompts[1])
    assert "Submit the completed batch in a separate tool call" in str(model.prompts[4])
    assert "INVENTORY-POLICY" in str(model.prompts[5]) and "AUDIT-POLICY" in str(model.prompts[5])
    assert "REVIEW-POLICY" in str(model.prompts[6]) and "AUDIT-POLICY" not in str(model.prompts[6])
    assert "GENERATION-POLICY" not in str(model.prompts)
    assert coverage.inventory["status"] == coverage.inventory["audit_status"] == "complete"
    assert coverage.inventory["points"][0]["evidence"][0]["start_char"] == 0
    assert result.provenance["coverage"]["summary"]["covered_points"] == 1


@pytest.mark.parametrize("completion", [{}, {"complete": False}, None], ids=["omitted", "false", "no-valid-submission"])
def test_inventory_recovers_when_model_stops_after_partial_submission(tmp_path, monkeypatch, completion):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from lladar.knowledge_coverage import KnowledgeCoverage
    from lladar.model_profiles import resolve_model_profile
    from lladar.skill_agent import AkashaSkillAgent
    from test_skill_agent import ToolScriptModel

    skill = tmp_path / "scoped-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: scoped-example\ndescription: Example.\n---\nSource facts.\n", encoding="utf-8")
    fact = "The archive is open."
    finish = AIMessage(content="", tool_calls=[{"name": "finish_inventory_batch", "id": "finish", "args": {
        "reason": "The whole assigned page was reviewed."}}])
    replies = [finish, *[AIMessage(content="Done") for _ in range(3)]] if completion is None else [
        AIMessage(content="", tool_calls=[{"name": "submit_inventory_batch", "id": "partial", "args": {
            "points": [{"statement": fact, "topic": "Archive", "evidence": [
                {"source_id": "source_001", "quote": fact}]}],
            "pending": [], "reason": "One saved fact.", **completion}}]),
        AIMessage(content="Done"),
        finish,
    ]
    model = ToolScriptModel(replies=replies)

    def with_scripted_model(**options):
        return AkashaSkillAgent(**{**options, "model": model, "language": "en"})

    monkeypatch.setattr("lladar.skill_agent.AkashaSkillAgent", with_scripted_model)
    coverage = KnowledgeCoverage([(tmp_path / "source.md", fact)], skill=skill,
        agent_factory=None, model="offline", env_file="", profile=resolve_model_profile("offline"), verbose=False)
    if completion is None:
        with pytest.raises(DatasetValidationError, match="inventory batch is incomplete after completion reminders"):
            coverage.inventory_sources()
        assert len(model.prompts) == 4 and not model.replies
        assert "submit a valid inventory result" in str(model.prompts[1])
        assert coverage.inventory["progress"]["source_001"]["cursor"] == 0
        assert not coverage.inventory["batches"]
        return

    coverage.inventory_sources()
    assert len(model.prompts) == 3 and not model.replies
    assert any(isinstance(message, ToolMessage) and message.tool_call_id == "partial"
               and json.loads(message.content)["complete"] is False for message in model.prompts[2])
    assert isinstance(model.prompts[2][-1], HumanMessage)
    assert "finish_inventory_batch" in model.prompts[2][-1].content
    assert coverage.inventory["progress"]["source_001"]["cursor"] == len(fact)
    assert len(coverage.inventory["points"]) == len(coverage.inventory["batches"]) == 1


def test_legacy_generation_report_does_not_invent_a_coverage_denominator(tmp_path):
    source = tmp_path / "legacy.generation.json"
    source.write_text(json.dumps({"schema_version": 5, "sources": [], "knowledge_points": [],
        "qa": [], "dataset": {"lines": []}, "stats": {"output_records": 10}}), encoding="utf-8")
    report = tmp_path / "report.md"
    assert main(["report", str(source), "--output", str(report), "--lang", "zh-TW", "--no-verbose"]) == 0
    text = report.read_text(encoding="utf-8")
    assert "尚無獨立盤點" in text
    assert "100%" not in text


@pytest.mark.parametrize("mismatch", ["unknown_question", "changed_question_fingerprint"])
def test_rejected_mapping_cannot_replace_an_existing_report(tmp_path, mismatch):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=dataset, skill_agent_factory=CoverageAgent, count=1, verbose=False)
    provenance = Path(str(dataset) + ".generation.json")
    saved = json.loads(provenance.read_text(encoding="utf-8"))
    if mismatch == "unknown_question":
        saved["coverage"]["reviews"][0]["question_ids"] = ["unknown-question"]
    else:
        saved["dataset"]["lines"][0]["record_fingerprint"] = "0" * 64
    provenance.write_text(json.dumps(saved), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("Previous report", encoding="utf-8")
    assert main(["report", str(provenance), "--output", str(report), "--force", "--no-verbose"]) == 2
    assert report.read_text(encoding="utf-8") == "Previous report"


def test_reviewed_exclusion_of_every_fact_has_no_eligible_percentage(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 19 retains data for 19 days.", encoding="utf-8")

    class ExcludedAgent(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] != "generation":
                return super().__call__(request)
            page = self.tools["read_source"](self.tools["list_sources"]()[0]["source_id"])
            self.tools["submit_question"](request_id="excluded-candidate", question="Does Service 19 retain data?",
                expected_answer="Yes", source_refs=[page["source_refs"][0]["ref_id"]], answer_basis="derived_criterion", rationale=page["text"])
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=ExcludedAgent, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["all_coverage"] == 0.0
    assert saved["coverage"]["summary"]["eligible_coverage"] is None


def test_generation_does_not_resend_the_full_accumulated_question_history(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")

    class HistoryAgent(CoverageAgent):
        def __call__(self, request):
            if request["stage"] in {"inventory_reconciliation", "coverage_review"}:
                assert len(json.dumps(request["points"], ensure_ascii=False).encode("utf-8")) <= 512
            if request["stage"] == "generation":
                assert len(json.dumps(request["existing_results"]).encode("utf-8")) <= 1024
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=HistoryAgent,
                        max_input_tokens=2048, max_output_tokens=1024, verbose=False)
    assert len(json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))["inventory"]["points"]) == 20


def test_source_changes_between_inventory_and_generation_prevent_publication(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")

    class ChangedSource(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            evidence = super().__call__(request)
            if request["stage"] == "inventory":
                source.write_text("Service 1 retains data for 1 days.\n\nService 2 retains data for 2 days.", encoding="utf-8")
            return evidence

    output = tmp_path / "dataset.jsonl"
    with pytest.raises(DatasetValidationError, match="sources changed"):
        create_test_dataset(source, output=output, skill_agent_factory=ChangedSource, verbose=False)
    assert not output.exists()


def test_partial_inventory_submission_is_saved_but_does_not_finish_the_page(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")

    class Partial(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] == "inventory":
                page = request["source"]
                self.tools["submit_inventory_batch"](points=[{
                    "statement": page["text"], "topic": "Retention", "evidence": [{
                        "source_id": page["source_id"], "start_char": 0, "quote": page["text"]}],
                }], pending=[], reason="One submission accepted; model output ended before page completion.", complete=False)
                return self.evidence()
            return super().__call__(request)

    class Resumed(CoverageAgent):
        def __call__(self, request):
            if request["stage"] == "inventory":
                saved = request["saved_partial"]
                assert saved["point_count"] == 1
                assert saved["points"][0]["statement"] == request["source"]["text"]
                assert self.tools["finish_inventory_batch"]("Verified the full page and its saved fact.")["complete"]
                return self.evidence()
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    with pytest.raises(DatasetValidationError, match="incomplete"):
        create_test_dataset(source, output=output, skill_agent_factory=Partial, verbose=False)
    assert not output.exists()
    checkpoint = json.loads((Path(str(output) + ".work") / "session.json").read_text(encoding="utf-8"))
    progress = checkpoint["data"]["state"]["knowledge_coverage"]["inventory"]["progress"]["source_001"]
    assert progress["cursor"] == 0 and len(progress["partial"]["point_ids"]) == 1
    create_test_dataset(source, output=output, skill_agent_factory=Resumed, verbose=False,
                        resume=Path(str(output) + ".work"))
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert len(saved["inventory"]["points"]) == 1


@pytest.mark.parametrize("stage", ["inventory", "inventory_reconciliation", "inventory_audit", "coverage_review"])
def test_saved_coverage_batch_is_reused_after_interruption(tmp_path, stage):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"

    class Interrupted(CoverageAgent):
        def __call__(self, request):
            evidence = super().__call__(request)
            if request["stage"] == stage:
                raise RuntimeError("Interrupted immediately after saving the batch.")
            return evidence

    class Resumed(CoverageAgent):
        def __call__(self, request):
            assert request["stage"] != stage, "A saved batch must be reused"
            return super().__call__(request)

    with pytest.raises(RuntimeError, match="after saving"):
        create_test_dataset(source, output=output, skill_agent_factory=Interrupted, verbose=False)
    assert not output.exists()
    create_test_dataset(source, output=output, skill_agent_factory=Resumed, verbose=False,
                        resume=Path(str(output) + ".work"))
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["covered_points"] == 1
    assert saved["coverage"]["audit_status"] == "complete"
    assert len(saved["inventory"]["batches"]) == len(saved["inventory"]["audit_batches"]) == 1


def test_report_explains_an_unresolved_source_ending_even_at_full_known_coverage(tmp_path):
    fact = "Service 1 retains data for 1 days."
    ending = "The building was designated in"
    text = fact + "\n\n" + ending
    source = tmp_path / "knowledge.md"
    source.write_text(text, encoding="utf-8")

    class Unresolved(CoverageAgent):
        def __call__(self, request):
            if request['stage'] == 'inventory_reconciliation':
                return super().__call__(request)
            if request["stage"] in {"inventory", "inventory_audit"}:
                page = request["source"]
                self.tools["submit_inventory_batch"](points=[{"statement": fact, "topic": "Retention", "evidence": [{
                    "source_id": page["source_id"], "start_char": 0, "quote": fact}]}], pending=[{
                    "source_id": page["source_id"], "start_char": text.index(ending), "end_char": len(text),
                    "reason": "Source ends before the designation is stated."}], reason="A source ending remains unresolved.", complete=True)
                return self.evidence()
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=Unresolved, verbose=False)
    report = tmp_path / "report.md"
    assert main(["report", str(output) + ".generation.json", "--output", str(report), "--lang", "zh-TW", "--no-verbose"]) == 0
    rendered = report.read_text(encoding="utf-8")
    assert "暫定" in rendered and "100%" in rendered
    assert "Source ends before the designation is stated." in rendered


def test_cli_generation_shows_both_denominators_and_the_report_command(tmp_path, capsys):
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(f"Service {n} retains data for {n} days." for n in range(1, 21)), encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    assert main(["create", "test-dataset", "--knowledge", str(source), "--output", str(output), "--workflow-level", "3",
                 "--count", "16", "--no-verbose"], skill_agent_factory=CoverageAgent) == 0
    text = capsys.readouterr().out
    assert "16 / 20" in text and "16 / 18" in text
    assert "lladar report" in text


def test_source_audit_is_followed_by_mandatory_semantic_reconciliation(tmp_path):
    statements = ["The building was a police station.", "Originally the building served as the police station."]
    source = tmp_path / "knowledge.md"
    source.write_text("\n\n".join(statements), encoding="utf-8")

    class Reconciled(CoverageAgent):
        def __call__(self, request):
            if request["stage"] in {"inventory", "inventory_audit"}:
                page = request["source"]
                self.tools["submit_inventory_batch"](points=[{
                    "statement": line, "topic": "Building", "evidence": [{"source_id": page["source_id"],
                        "start_char": page["text"].index(line), "quote": line}],
                } for line in statements], pending=[], reason="Source facts extracted.", complete=True)
            elif request["stage"] == "inventory_reconciliation":
                points = self.tools["list_inventory_points"]()["points"]
                if len(points) > 1:
                    self.tools["revise_inventory_point"](knowledge_point_id=points[0]["id"], statement=statements[0], topic="Building",
                        evidence=[{"source_id": "source_001", "start_char": 0, "quote": statements[0]}],
                        merge_ids=[p["id"] for p in points[1:]], reason="Same previous-use fact in summary and body.")
                self.tools["finish_inventory_reconciliation"](reason="Equivalent facts consolidated.", issues=[])
            elif request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{"knowledge_point_id": p["id"], "answerability": "eligible",
                    "question_ids": [request["questions"][0]["id"]], "reason": "Question tests previous use."} for p in request["points"]])
            else:
                page = self.tools["read_source"]("source_001")
                self.tools["submit_question"](request_id="previous-use", question="What was this building used for?", expected_answer="A police station.",
                    source_refs=[page["source_refs"][0]["ref_id"]], answer_basis="derived_criterion", rationale=statements[0])
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, skill_agent_factory=Reconciled, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["total_points"] == 1
    assert saved["inventory"]["reconciliation"]["audit"]["status"] == "complete"


@pytest.mark.parametrize("split_in_audit", [False, True])
def test_compound_fact_is_split_before_counting_incidental_answer_mentions(tmp_path, split_in_audit):
    fact = "The building was a police station and designated as a monument in 1998."
    source = tmp_path / "knowledge.md"
    source.write_text(fact, encoding="utf-8")
    children = ["The building was a police station.", "The building was designated as a monument in 1998."]

    class SplitFacts(CoverageAgent):
        def __call__(self, request):
            evidence = [{"source_id": "source_001", "start_char": 0, "quote": fact}]
            if request["stage"] in {"inventory", "inventory_audit"}:
                found_evidence = evidence
                if split_in_audit and request["stage"] == "inventory_audit":
                    quote = "designated as a monument in 1998."
                    found_evidence = [{"source_id": "source_001", "start_char": fact.index(quote), "quote": quote}]
                self.tools["submit_inventory_batch"](points=[{"statement": fact, "topic": "Building", "evidence": found_evidence}],
                    pending=[], reason="Extracted the original compound statement.", complete=True)
            elif request["stage"] == "inventory_reconciliation":
                for point in self.tools["list_inventory_points"]()["points"]:
                    if " and designated " in point["statement"] and (not split_in_audit or request["phase"] == "audit"):
                        replaced = self.tools["replace_inventory_point"](knowledge_point_id=point["id"],
                            points=[{"statement": s, "topic": "Building", "evidence": evidence} for s in children],
                            reason="Previous use and monument designation are independently testable facts.")
                        retired = json.loads(self.tools["read_inventory_point"](point["id"])["text"])
                        assert retired["replacement_ids"] == replaced["replacement_ids"]
                self.tools["finish_inventory_reconciliation"](reason="Atomic fact boundaries checked.", issues=[])
            elif request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{"knowledge_point_id": p["id"], "answerability": "eligible",
                    "question_ids": [request["questions"][0]["id"]] if "police" in p["statement"] else [],
                    "reason": "Question requires previous use; the designation year is incidental."} for p in request["points"]])
            else:
                page = self.tools["read_source"]("source_001")
                annotation = {"coverage_claims": [{"knowledge_point_id": request["inventory_points"]["points"][0]["id"],
                    "reason": "The expected answer must include the previous use."}]} if split_in_audit else {}
                result = self.tools["submit_question"](request_id="previous-use", question="What was this building previously used for?",
                    expected_answer="A police station. It was designated as a monument in 1998.",
                    source_refs=[page["source_refs"][0]["ref_id"]], answer_basis="derived_criterion", rationale=fact, **annotation)
                assert result["status"] == "accepted"
            return self.evidence()

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, count=1, skill_agent_factory=SplitFacts, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["total_points"] == 2
    assert saved["coverage"]["summary"]["covered_points"] == 1
    assert saved["coverage"]["summary"]["all_coverage"] == 0.5
    if split_in_audit:
        assert saved["dataset"]["lines"][0]["inventory_coverage"]["claims"][0]["knowledge_point_id"] == "ikp_000001"
        assert all(row["review_method"] == "review" for row in saved["coverage"]["reviews"])


def test_unresolved_answerability_stays_in_the_denominator_and_review_queue(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")

    class Uncertain(CoverageAgent):
        def __call__(self, request):
            if request["stage"] == "coverage_review":
                self.tools["submit_coverage_review"]([{"knowledge_point_id": p["id"], "answerability": "unresolved",
                    "question_ids": [], "reason": "The scene does not identify the service; clarification is needed."} for p in request["points"]])
                return self.evidence()
            return super().__call__(request)

    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, count=1, skill_agent_factory=Uncertain, verbose=False)
    saved = json.loads(Path(str(output) + ".generation.json").read_text(encoding="utf-8"))
    assert saved["coverage"]["summary"]["eligible_points"] == 1
    assert saved["coverage"]["summary"]["pending_review_points"] == 1
    assert saved["coverage"]["summary"]["covered_points"] == 0


def test_coverage_report_honors_an_explicit_report_skill(tmp_path):
    source = tmp_path / "knowledge.md"
    source.write_text("Service 1 retains data for 1 days.", encoding="utf-8")
    output = tmp_path / "dataset.jsonl"
    create_test_dataset(source, output=output, count=1, skill_agent_factory=CoverageAgent, verbose=False)
    skill = tmp_path / "coverage-style"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: coverage-style\ndescription: Write coverage findings.\n---\nUse the frozen coverage facts.", encoding="utf-8")

    class Writer(CoverageAgent):
        def __call__(self, request):
            self.tools["submit_report"]({"overview": "Coverage-focused overview.",
                "findings": "Coverage-focused findings.", "limitations": "Only inventoried source facts are represented."})
            return self.evidence()

    report = tmp_path / "report.md"
    assert main(["report", str(output) + ".generation.json", "--output", str(report), "--skill", str(skill),
                 "--no-verbose"], skill_agent_factory=Writer) == 0
    text = report.read_text(encoding="utf-8")
    assert "Coverage-focused findings." in text
    assert "1 / 1" in text
