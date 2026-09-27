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

    assert "NEBULA-METHOD" not in str(model.prompts[0])
    assert "NEBULA-METHOD" in str(model.prompts[1])
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


def test_adapter_has_no_lladar_specific_skill_middleware_or_agent_subclass():
    import lladar.skill_agent as skill_agent

    assert not hasattr(skill_agent, "DatasetSkillMiddleware")
    assert not hasattr(skill_agent, "DatasetAkashaAgent")


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
                             max_output_tokens=2000, verbose=False)
    with pytest.raises(RuntimeError, match="tool call budget"):
        agent({"stage": "knowledge_points", "source_id": "source_001"})
