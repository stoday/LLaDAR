"""Separate external inventory provider for existing generation-author tests.

These tests model one source passage as one fact. Detailed extraction, mapping,
exclusion and audit behavior is exercised through the public API in
test_knowledge_coverage.py.
"""

import hashlib
from pathlib import Path

from lladar.api import create_test_dataset as generate_dataset
from lladar.cli import main as run_cli


class SourceCoverageAgent:
    def __init__(self, *, skills, tools, **options):
        self.skill = Path(skills[0])
        self.tools = tools

    def __call__(self, request):
        if request["stage"] == "inventory_reconciliation":
            self.tools["finish_inventory_reconciliation"](reason="Fixture inventory checked.", issues=[])
            return {"loaded_skills": [self.skill.name], "skill_files": {
                "SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}
        if request["stage"] in {"inventory", "inventory_audit"}:
            page = request["source"]
            text = page["text"]
            points = [{"statement": text.strip(), "topic": "Source fact", "evidence": [{
                "source_id": page["source_id"], "start_char": page["start_char"], "quote": text,
            }]}] if text.strip() else []
            self.tools["submit_inventory_batch"](points=points, pending=[], reason="Fixture source passage processed.", complete=True)
        elif request["stage"] == "coverage_review":
            self.tools["submit_coverage_review"]([{
                "knowledge_point_id": point["id"], "answerability": "eligible",
                "question_ids": [request["questions"][0]["id"]] if request["questions"] else [],
                "reason": "The fixture question tests the modeled source fact.",
            } for point in request["points"]])
        return {"loaded_skills": [self.skill.name], "skill_files": {
            "SKILL.md": hashlib.sha256((self.skill / "SKILL.md").read_bytes()).hexdigest()}}


def create_test_dataset(*args, **kwargs):
    kwargs.setdefault("coverage_agent_factory", SourceCoverageAgent)
    if kwargs.get("from_github") is None:
        kwargs.setdefault("workflow_level", 3)
    return generate_dataset(*args, **kwargs)


def main(argv, **kwargs):
    kwargs.setdefault("coverage_agent_factory", SourceCoverageAgent)
    if "test-dataset" in argv and "--knowledge" in argv and "--workflow-level" not in argv:
        argv = [*argv, "--workflow-level", "3"]
    return run_cli(argv, **kwargs)
