"""Exercise Akasha's real skill loader with an offline chat model."""

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


def test_native_skill_loads_instructions_and_only_controlled_tools(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "native-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: native-example\ndescription: Example for a native load.\n---\n"
        "Use the phrase NEBULA-METHOD when recording a fact.\n", encoding="utf-8")
    received = []

    def record_fact(value: str) -> str:
        received.append(value)
        return "accepted"

    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "native-example"}, "id": "load-1"}]),
        AIMessage(content="", tool_calls=[{"name": "record_fact", "args": {"value": "NEBULA-METHOD"}, "id": "submit-1"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"record_fact": record_fact},
                            model=model, env_file="", temperature=0,
                            max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({"stage": "qa", "knowledge_point_id": "point-1"})
    assert evidence["loaded_skills"] == ["native-example"]
    assert received == ["NEBULA-METHOD"]
    assert "NEBULA-METHOD" in str(model.prompts[1])
    assert all("python_execute" not in names for names in model.offered)
    assert evidence["skill_files"]["SKILL.md"]


def test_native_agent_rejects_execution_and_work_before_skill_load(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "restricted"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: restricted\ndescription: Restricted test.\n---\nRead before submitting.\n")
    received = []

    def record_fact(value: str) -> str:
        received.append(value)
        return "accepted"

    marker = tmp_path / "must-not-exist"
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "record_fact", "args": {"value": "premature"}, "id": "early"}]),
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "restricted"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "python_execute", "args": {
            "skill": "restricted", "source": f"open({str(marker)!r}, 'w').write('escaped')"}, "id": "escape"}]),
        AIMessage(content="", tool_calls=[{"name": "record_fact", "args": {"value": "allowed"}, "id": "late"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"record_fact": record_fact},
                            model=model, env_file="", temperature=0,
                            max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    agent({"stage": "qa", "knowledge_point_id": "point-1"})
    assert not marker.exists()
    assert received == ["allowed"]


def test_skill_resource_is_scoped_and_effective_tools_are_auditable(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "references"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: references\ndescription: Read a reference.\n---\nRead note.md.")
    (skill / "note.md").write_text("Scoped reference content")
    (tmp_path / "outside.txt").write_text("OUTSIDE-SECRET")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "references"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "read_skill_resource", "args": {"skill": "references", "path": "../outside.txt"}, "id": "escape"}]),
        AIMessage(content="", tool_calls=[{"name": "read_skill_resource", "args": {"skill": "references", "path": "note.md"}, "id": "read"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                            max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    evidence = agent({"stage": "knowledge_points", "source_id": "source_001"})
    assert "OUTSIDE-SECRET" not in str(model.prompts)
    assert "Scoped reference content" in str(model.prompts[-1])
    assert evidence["skill_files"]["note.md"]
    assert set(evidence["tool_names"]) == {"load_skill", "read_skill_resource"}
    assert any(event["tool"] == "load_skill" for event in evidence["tool_events"])


def test_agent_enforces_model_context_budget_before_model_call(tmp_path):
    import pytest
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "budget"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: budget\ndescription: Budget test.\n---\nRead sources.")
    model = ToolScriptModel(replies=[AIMessage(content="Done")])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                            max_input_tokens=100, max_output_tokens=100, verbose=False)
    with pytest.raises(RuntimeError, match="input.*budget"):
        agent({"stage": "knowledge_points", "source_id": "source_001" + "long " * 100})
    assert model.prompts == []


def test_all_tool_calls_including_unknown_tools_share_a_finite_budget(tmp_path):
    import pytest
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "bounded"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: bounded\ndescription: Tool budget.\n---\nRead sources.")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "unknown_tool", "args": {}, "id": f"call-{i}"} for i in range(41)]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                            max_input_tokens=100000, max_output_tokens=2000, verbose=False)
    with pytest.raises(RuntimeError, match="tool call budget"):
        agent({"stage": "knowledge_points", "source_id": "source_001"})


def test_native_skill_load_failure_is_fatal_not_a_retryable_model_failure(tmp_path):
    import pytest
    from lladar.exceptions import LladarError
    from lladar.skill_agent import AkashaSkillAgent

    skill = tmp_path / "broken"
    skill.mkdir()
    document = skill / "SKILL.md"
    document.write_text("---\nname: broken\ndescription: Loader test.\n---\nRead sources.")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "broken"}, "id": "load"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                            max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    document.write_text("Invalid skill entry")
    with pytest.raises(LladarError, match="skill.*load"):
        agent({"stage": "knowledge_points", "source_id": "source_001"})


def test_native_provider_failure_is_retryable_without_exposing_exception_details(tmp_path):
    import pytest
    from lladar.exceptions import ProviderError
    from lladar.skill_agent import AkashaSkillAgent

    class DisconnectedModel(ToolScriptModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ConnectionError("PRIVATE-REQUEST-DATA")

    skill = tmp_path / "network"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: network\ndescription: Network test.\n---\nRead sources.")
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=DisconnectedModel(), env_file="", temperature=0,
                            max_input_tokens=16000, max_output_tokens=2000, verbose=False)
    with pytest.raises(ProviderError) as error:
        agent({"stage": "knowledge_points", "source_id": "source_001"})
    assert "PRIVATE-REQUEST-DATA" not in str(error.value)
    assert error.value.skill_evidence["tool_call_limit"] == 40
    assert error.value.skill_evidence["max_round"] == 30
    assert error.value.skill_evidence["loaded_skills"] == []
