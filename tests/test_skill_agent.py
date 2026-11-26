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


def _python_skill(tmp_path, allowed_tools=""):
    skill = tmp_path / "python-guard"
    skill.mkdir()
    frontmatter = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    (skill / "SKILL.md").write_text(
        f"---\nname: python-guard\ndescription: Guard.\n{frontmatter}---\nSubmit facts.\n", encoding="utf-8")
    (skill / "note.md").write_text("Scoped reference content", encoding="utf-8")
    return skill


def test_python_execute_is_hidden_and_rejected_unless_the_skill_opts_in(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path)
    marker = tmp_path / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('ran')"
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "python-guard"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "python_execute", "args": {"skill": "python-guard", "source": code}, "id": "py"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False)

    evidence = agent({"stage": "generation"})

    assert not marker.exists()
    assert "python_execute" not in model.offered[1]
    assert "read_skill_resource" in model.offered[1]
    assert "Use python_execute(" not in str(model.prompts[1])
    assert "python_execute is not enabled" in str(model.prompts[2])
    assert evidence["blocked_runtime_tools"] == ["python_execute"]
    assert evidence["tool_calls"] == 1


def test_python_execute_is_offered_when_allowed_tools_declares_it(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path, "python_execute")
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "python-guard"}, "id": "load"}]),
        AIMessage(content="", tool_calls=[{"name": "python_execute", "args": {"skill": "python-guard", "source": "print(6 * 7)"}, "id": "py"}]),
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False)

    evidence = agent({"stage": "generation"})

    assert "python_execute" in model.offered[1]
    assert "42" in str(model.prompts[2])
    assert evidence["blocked_runtime_tools"] == []


def test_akasha_runtime_tools_count_against_the_skill_tool_budget(tmp_path):
    import pytest
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path)
    model = ToolScriptModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "python-guard"}, "id": "load"}]),
        *[AIMessage(content="", tool_calls=[{"name": "read_skill_resource",
                                             "args": {"skill": "python-guard", "path": "note.md"}, "id": f"read-{i}"}])
          for i in range(3)],
        AIMessage(content="Done"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False, tool_call_limit=2)
    with pytest.raises(RuntimeError, match="tool call budget"):
        agent({"stage": "generation"})


def test_input_token_budget_stops_a_work_item_with_its_evidence(tmp_path):
    import pytest
    from lladar.exceptions import SkillBudgetExceeded
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path)
    usage = {"input_tokens": 600, "output_tokens": 10, "total_tokens": 610}
    model = ToolScriptModel(replies=[
        AIMessage(content="", usage_metadata=usage,
                  tool_calls=[{"name": "load_skill", "args": {"reference": "python-guard"}, "id": "load"}]),
        AIMessage(content="", usage_metadata=usage,
                  tool_calls=[{"name": "read_skill_resource", "args": {"skill": "python-guard", "path": "note.md"}, "id": "read"}]),
        AIMessage(content="Never reached"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={}, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False, input_token_budget=1000)
    with pytest.raises(SkillBudgetExceeded) as raised:
        agent({"stage": "generation"})
    assert raised.value.skill_evidence["input_tokens"] == 1200
    assert len(model.prompts) == 2


def test_input_token_budget_also_bounds_host_scoped_inventory_work(tmp_path):
    import pytest
    from lladar.exceptions import SkillBudgetExceeded
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path)
    usage = {"input_tokens": 700, "output_tokens": 10, "total_tokens": 710}
    model = ToolScriptModel(replies=[
        AIMessage(content="", usage_metadata=usage, tool_calls=[{"name": "note", "args": {"value": "a"}, "id": "n1"}]),
        AIMessage(content="", usage_metadata=usage, tool_calls=[{"name": "note", "args": {"value": "b"}, "id": "n2"}]),
        AIMessage(content="Never reached"),
    ])
    agent = AkashaSkillAgent(skills=[str(skill)], tools={"note": lambda value: value}, model=model, env_file="",
                             temperature=0, max_input_tokens=16000, max_output_tokens=2000, verbose=False,
                             host_scoped=True, batch_complete=lambda: False, input_token_budget=1000)
    with pytest.raises(SkillBudgetExceeded):
        agent({"stage": "inventory"})


def test_estimate_tokens_counts_cjk_text_per_character():
    from langchain_core.messages import HumanMessage
    from lladar.skill_agent import estimate_tokens

    assert estimate_tokens([HumanMessage(content="警" * 400)]) >= 400
    assert estimate_tokens([HumanMessage(content="a" * 400)]) < 120


def test_old_tool_results_are_cleared_but_source_pages_and_recent_results_remain(tmp_path):
    from lladar.skill_agent import AkashaSkillAgent

    skill = _python_skill(tmp_path)
    tools = {
        "read_source": lambda source_id: "SOURCE-PAGE " + "原" * 300,
        "inspect": lambda index: f"RESULT-{index} " + "查" * 300,
    }
    calls = [AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"reference": "python-guard"}, "id": "load"}]),
             AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"source_id": "s"}, "id": "src"}])]
    calls += [AIMessage(content="", tool_calls=[{"name": "inspect", "args": {"index": str(i)}, "id": f"i{i}"}])
              for i in range(8)]
    model = ToolScriptModel(replies=[*calls, AIMessage(content="Done")])
    agent = AkashaSkillAgent(skills=[str(skill)], tools=tools, model=model, env_file="", temperature=0,
                             max_input_tokens=16000, max_output_tokens=2000, verbose=False, context_trigger_tokens=1500)

    agent({"stage": "generation"})

    final = str(model.prompts[-1])
    assert "SOURCE-PAGE" in final
    assert all(f"RESULT-{i}" not in final for i in range(4))
    assert all(f"RESULT-{i}" in final for i in range(4, 8))
    assert "Earlier tool result cleared" in final
    # Only the request is edited; the first prompts saw full results before the trigger.
    assert "RESULT-0" in str(model.prompts[3])
