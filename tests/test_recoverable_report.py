from pathlib import Path
import pytest
from lladar.evaluation import evaluate
from lladar.reporting import create_report
from test_simple_pipeline import EvaluationSkillAgent, ReportSkillAgent
from test_recoverable_evaluation import responses


def test_report_resumes_accepted_section_without_rewriting_it(tmp_path):
    evaluation = tmp_path / 'eval.json'
    evaluate(responses(tmp_path), output=evaluation, skill_agent_factory=EvaluationSkillAgent, verbose=False)
    output = tmp_path / 'report.md'
    class Interrupted(ReportSkillAgent):
        def __call__(self, request):
            saved = self.tools['submit_section']('overview', 'Saved overview.', ['summary'], 'overview')
            assert saved['status'] == 'accepted'
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        create_report(evaluation, output, skill_agent_factory=Interrupted, verbose=False)
    assert not output.exists()
    class Resumed(ReportSkillAgent):
        def __call__(self, request):
            assert request['completed_sections'] == ['overview']
            assert self.tools['get_section']('overview')['value']['text'] == 'Saved overview.'
            bad = self.tools['submit_section']('findings', 'Unsupported finding.', ['unknown'], 'bad')
            assert bad['status'] == 'needs_revision'
            fixed = self.tools['revise_section']('findings', 'repair', bad['revision'],
                'Measured results are tabulated.', ['aggregates'])
            assert fixed['status'] == 'accepted'
            self.tools['submit_section']('limitations', 'Synthetic fixture; human review pending.', ['summary'], 'limit')
            return {'loaded_skills': [self.name], 'skill_files': {'SKILL.md': 'fixture'}}
    create_report(evaluation, output, skill_agent_factory=Resumed, verbose=False, resume=Path(str(output)+'.work'))
    assert 'Saved overview.' in output.read_text(encoding='utf-8')
    assert 'Unsupported finding.' not in output.read_text(encoding='utf-8')


def test_native_report_review_refuses_unsupported_sections(tmp_path, monkeypatch):
    import json
    import akasha
    from types import SimpleNamespace
    from lladar.exceptions import EvaluationError
    evaluation = tmp_path/'eval.json'
    evaluate(responses(tmp_path), output=evaluation, skill_agent_factory=EvaluationSkillAgent, verbose=False)
    class SDK:
        def __init__(self, *, skills, tools, **kwargs):
            self.tools = {tool.name: tool.func for tool in tools}
            name = Path(skills[0]).name
            self.skill_context = SimpleNamespace(names=[name])
            self.skill_middleware = SimpleNamespace(loaded_skill_names=[name])
        def __call__(self, payload):
            request = json.loads(payload)
            if request['stage'] == 'report':
                self.tools['submit_report']({'overview':'Fixture overview.', 'findings':'Unsupported finding.',
                                            'limitations':'Fixture limitations.'})
            else:
                assert request['stage'] == 'report_review'
                self.tools['submit_section_review']([
                    {'section':section, 'approved':section != 'findings', 'reason':'Finding lacks support.'}
                    for section in ('overview','findings','limitations')])
    monkeypatch.setattr(akasha, 'agents', SDK)
    output=tmp_path/'report.md'
    with pytest.raises(EvaluationError, match='support|review'):
        create_report(evaluation, output, verbose=False)
    assert not output.exists() and evaluation.is_file()


def test_report_uses_explicit_model_budgets_for_author_and_reviewer(tmp_path):
    evaluation = tmp_path / 'evaluation.json'
    evaluate(responses(tmp_path),output=evaluation,skill_agent_factory=EvaluationSkillAgent,verbose=False)
    seen=[]
    class Author(ReportSkillAgent):
        def __init__(self, **options):
            seen.append((options.get('max_input_tokens'),options.get('max_output_tokens')))
            super().__init__(**options)
    class Reviewer(Author):
        def __call__(self, request):
            self.tools['submit_section_review']([{'section':s,'approved':True,'reason':'Supported by frozen facts.'} for s in ['overview','findings','limitations']])
            return {'loaded_skills':[self.name], 'skill_files':{'SKILL.md':'fixture'}}
    create_report(evaluation,tmp_path/'report.md',skill_agent_factory=Author,review_agent_factory=Reviewer,
                  max_input_tokens=16000,max_output_tokens=2000,verbose=False)
    assert seen==[(16000,2000),(16000,2000)]
