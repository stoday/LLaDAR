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
        AIMessage(content='', tool_calls=[{'name': 'record', 'args': {'value': str(i)}, 'id': f'call-{i}'} for i in range(41)]),
        AIMessage(content='Done'),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={'record': lambda value: value}, model=model,
                            env_file='', temperature=0, max_input_tokens=100000,
                            max_output_tokens=2000, verbose=False)
    agent({'stage': 'knowledge_points', 'source_id': 'source_001'})
    assert agent.calls == 41
