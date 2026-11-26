"""Public behavior of the thin adapter over Akasha's native skill runtime."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ToolScriptModel(BaseChatModel):
    replies: list = Field(default_factory=list)
    offered: list = Field(default_factory=list)
    prompts: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-skill-model"

    def bind_tools(self, tools, **kwargs):
        self.offered.append([getattr(tool, "name", "") for tool in tools])
        return self

    def get_num_tokens(self, text):
        return len(text)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def test_akasha_native_skill_is_loaded_only_after_the_model_requests_it(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "native-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: native-example\ndescription: Example.\n---\n"
        "Use the phrase NEBULA-METHOD when recording a fact.\n", encoding="utf-8")
    (skill / "AUTHORING.md").write_text("AUTHOR-GUIDE-ALTERNATIVE", encoding="utf-8")
    (skill / "guide.md").write_text("OTHER-GUIDE-ALTERNATIVE", encoding="utf-8")
    received = []

    def record_fact(value: str) -> None:
        received.append(value)

    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "native-example"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "record_fact", "args": {"value": "NEBULA-METHOD"}, "id": "record"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"record_fact": record_fact}, model=model,
                             env_file="", temperature=0, max_input_tokens=16000,
                             max_output_tokens=2000, verbose=False)

    evidence = agent({"stage": "qa", "knowledge_point_id": "point-1"})

    assert "reference='native-example'" in str(model.prompts[0])
    assert "NEBULA-METHOD" not in str(model.prompts[0])
    assert "NEBULA-METHOD" in str(model.prompts[1])
    assert "AUTHOR-GUIDE-ALTERNATIVE" not in str(model.prompts)
    assert "OTHER-GUIDE-ALTERNATIVE" not in str(model.prompts)
    assert received == ["NEBULA-METHOD"]
    assert evidence["loaded_skills"] == ["native-example"]
    assert evidence["skill_files"]["SKILL.md"]
    assert "load_skill" in model.offered[0]


def test_akasha_native_skill_reads_a_declared_resource_on_demand(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "references"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: references\ndescription: Resource.\n---\nRead note.md.\n", encoding="utf-8")
    (skill / "note.md").write_text("Scoped reference content", encoding="utf-8")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "references"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "read_skill_resource", "args": {"skill": "references", "path": "note.md"}, "id": "read"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False)

    evidence = agent({"stage": "knowledge_points", "source_id": "source_001"})

    assert "Scoped reference content" not in str(model.prompts[1])
    assert "Scoped reference content" in str(model.prompts[2])
    assert evidence["loaded_skills"] == ["references"]
    assert "read_skill_resource" in model.offered[1]


def test_builtin_situation_skill_can_read_authoring_guide_and_submit():
    from pathlib import Path
    from lladar.skill_agent import AkashaSkillAgent

    skill = Path(__file__).parents[1] / "src" / "lladar" / "skill_assets" / "create-situation"
    received = []
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "create-situation"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "read_skill_resource", "args": {"skill": "create-situation", "path": "AUTHORING.md"}, "id": "read"}]),
        AIMessage(content="", tool_calls=[{"name": "submit_situation", "args": {"value": "fixture"}, "id": "submit"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"submit_situation": lambda value: received.append(value)},
                             model=model, env_file="", max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({"stage": "create_situation"})
    assert received == ["fixture"]
    assert evidence["loaded_skills"] == ["create-situation"]


def test_builtin_report_skill_reads_authoring_and_submits_valid_section():
    import json
    from lladar.reporting import BUILTIN_REPORT_SKILL_DIR, ReportWorkspace
    from lladar.skill_agent import AkashaSkillAgent

    workspace = ReportWorkspace({'summary': {}})
    section = {'section': 'overview', 'text': 'This report summarizes the saved evaluation evidence.',
               'source_refs': ['summary'], 'request_id': 'overview'}
    model = ToolScriptModel(replies=[
        AIMessage(content='', tool_calls=[{'name': 'load_skill', 'args': {'reference': 'report-evidence-summary'}, 'id': 'load'}]),
        AIMessage(content='', tool_calls=[{'name': 'read_skill_resource', 'args': {'skill': 'report-evidence-summary', 'path': 'AUTHORING.md'}, 'id': 'read'}]),
        AIMessage(content='', tool_calls=[{'name': 'submit_section', 'args': section, 'id': 'submit'}]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(BUILTIN_REPORT_SKILL_DIR)],
        tools={'submit_section': workspace.submit_section}, model=model, env_file='',
        max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({'stage': 'report', 'facts': {'summary': {}}})
    assert evidence['loaded_skills'] == ['report-evidence-summary']
    assert workspace.get_section('overview')['status'] == 'accepted'
    assert '# Report authoring guide' in str(model.prompts[2])
    guide = (BUILTIN_REPORT_SKILL_DIR / 'AUTHORING.md').read_text(encoding='utf-8')
    examples = [json.loads(block.split('```', 1)[0]) for block in guide.split('```json\n')[1:]]
    assert ReportWorkspace({'summary': {}}).submit_section(**examples[0])['status'] == 'accepted'
    assert ReportWorkspace().submit_report(examples[1]) == {'accepted': True}


def test_host_tool_requests_skill_loading_before_accepting_a_submission(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent
    skill = tmp_path / 'fixture-method'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: fixture-method\ndescription: fixture\n---\nSubmit the result.', encoding='utf-8')
    received = []
    model = ToolScriptModel(replies=[
        AIMessage(content='', tool_calls=[{'name': 'submit_judgment', 'args': {'value': 'early'}, 'id': 'early'}]),
        AIMessage(content='', tool_calls=[{'name': 'load_skill', 'args': {'reference': 'fixture-method'}, 'id': 'load'}]),
        AIMessage(content='', tool_calls=[{'name': 'submit_judgment', 'args': {'value': 'loaded'}, 'id': 'accepted'}]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={'submit_judgment': lambda value: received.append(value)},
        model=model, env_file='', max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({'stage': 'judgment'})
    assert received == ['loaded']
    assert 'load_skill' in evidence['tool_events'][0]['result']['error']
    assert evidence['loaded_skills'] == ['fixture-method']


def test_model_that_finishes_without_loading_skill_gets_a_bounded_retry(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent
    skill = tmp_path / 'retry-method'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: retry-method\ndescription: fixture\n---\nSubmit the result.', encoding='utf-8')
    received = []
    model = ToolScriptModel(replies=[
        AIMessage(content='Done'),
        AIMessage(content='', tool_calls=[{'name': 'load_skill', 'args': {'reference': 'retry-method'}, 'id': 'load'}]),
        AIMessage(content='', tool_calls=[{'name': 'submit_judgment', 'args': {'value': 'loaded'}, 'id': 'submit'}]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={'submit_judgment': lambda value: received.append(value)},
        model=model, env_file='', max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({'stage': 'judgment'})
    assert received == ['loaded']
    assert evidence['loaded_skills'] == ['retry-method']


def test_builtin_evaluation_skill_reads_authoring_and_submits_valid_plan():
    import json
    from pathlib import Path
    from lladar.evaluation import EvaluationWorkspace
    from lladar.skill_agent import AkashaSkillAgent

    skill = Path(__file__).parents[1] / 'src' / 'lladar' / 'skill_assets' / 'eval-answer-verdict'
    workspace = EvaluationWorkspace()
    plan = {'title': 'Answer correctness', 'approach': 'Compare the supplied answers.',
        'dimensions': [{'name': 'correct', 'description': 'Matches the expected answer.', 'kind': 'boolean'}],
        'limitations': ['Only the supplied evidence is judged.']}
    model = ToolScriptModel(replies=[
        AIMessage(content='', tool_calls=[{'name': 'load_skill', 'args': {'reference': 'eval-answer-verdict'}, 'id': 'load'}]),
        AIMessage(content='', tool_calls=[{'name': 'read_skill_resource', 'args': {'skill': 'eval-answer-verdict', 'path': 'AUTHORING.md'}, 'id': 'read'}]),
        AIMessage(content='', tool_calls=[{'name': 'submit_plan', 'args': {'plan': plan}, 'id': 'submit'}]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={'submit_plan': workspace.submit_plan},
        model=model, env_file='', max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({'stage': 'plan'})
    assert evidence['loaded_skills'] == ['eval-answer-verdict']
    assert workspace.plan == plan
    assert '"kind": "boolean"' in str(model.prompts[2])
    # Keep the documented plan and judgment examples valid against the actual host validators.
    from lladar.evaluation import _validate_plan, _validate_judgment
    text = (skill / 'AUTHORING.md').read_text(encoding='utf-8')
    examples = [json.loads(block.split('```', 1)[0]) for block in text.split('```json\n')[1:]]
    documented_plan = _validate_plan(examples[0])
    _validate_judgment(examples[1], documented_plan)


def test_missing_skill_loading_retry_stops_after_three_attempts(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent
    skill = tmp_path / 'unloaded-method'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: unloaded-method\ndescription: fixture\n---\nRead these rules.', encoding='utf-8')
    model = ToolScriptModel(replies=[AIMessage(content='Done') for _ in range(3)])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file='',
        max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({'stage': 'fixture'})
    assert evidence['loaded_skills'] == []
    assert len(model.prompts) == 3


def test_missing_skill_resource_is_not_reported_as_provider_failure(tmp_path):
    import pytest
    from lladar.skill_agent import AkashaSkillAgent, SkillRuntimeError

    skill = tmp_path / "missing-resource"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: missing-resource\ndescription: Test.\n---\nRead missing.md.", encoding="utf-8")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "missing-resource"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "read_skill_resource", "args": {"skill": "missing-resource", "path": "missing.md"}, "id": "read"}]),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="",
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    with pytest.raises(SkillRuntimeError, match="file not found during skill execution"):
        agent({"stage": "fixture"})


def test_adapter_has_no_lladar_specific_skill_middleware_or_agent_subclass():
    import lladar.skill_agent as skill_agent

    assert not hasattr(skill_agent, "DatasetSkillMiddleware")
    assert not hasattr(skill_agent, "DatasetAkashaAgent")


def test_verbose_skill_agent_displays_akasha_trace(tmp_path, capsys):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "trace-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: trace-example\ndescription: Trace test.\n---\nRecord a fact.", encoding="utf-8")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "trace-example"}, "id": "load"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="",
                             max_input_tokens=16000, max_output_tokens=2000, verbose=True)
    agent({"stage": "fixture"})
    captured = capsys.readouterr()
    assert "agent.trace" in captured.err or "load_skill" in captured.err


def test_direct_adapter_limits_bound_host_tool_calls(tmp_path):
    import pytest
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "bounded"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: bounded\ndescription: Budget.\n---\nSubmit facts.")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "bounded"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "record", "args": {"value": str(i)}, "id": f"call-{i}"}
                                          for i in range(41)]),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"record": lambda value: value}, model=model,
                             env_file="", temperature=0, max_input_tokens=100000,
                             max_output_tokens=2000, verbose=False, tool_call_limit=40)
    with pytest.raises(RuntimeError, match="tool call budget"):
        agent({"stage": "knowledge_points", "source_id": "source_001"})


def test_default_skill_budget_allows_work_past_the_old_forty_call_limit(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent
    skill = tmp_path / 'larger-budget'
    skill.mkdir()
    (skill / 'SKILL.md').write_text('---\nname: larger-budget\ndescription: Budget.\n---\nSubmit facts.', encoding='utf-8')
    model = ToolScriptModel(replies=[
        AIMessage(content='', tool_calls=[{'name': 'load_skill', 'args': {'reference': 'larger-budget'}, 'id': 'load'}]),
        AIMessage(content='', tool_calls=[{'name': 'record', 'args': {'value': str(i)}, 'id': f'call-{i}'} for i in range(41)]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={'record': lambda value: value}, model=model,
                            env_file='', temperature=0, max_input_tokens=100000,
                            max_output_tokens=2000, verbose=False)
    agent({'stage': 'knowledge_points', 'source_id': 'source_001'})
    assert agent.calls == 41
