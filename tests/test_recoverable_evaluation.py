"""Four-stage recovery via public APIs, tools and saved artifacts."""
import json
from pathlib import Path
import pytest
from lladar.evaluation import evaluate
from test_simple_pipeline import EvaluationSkillAgent, write_jsonl


def responses(tmp_path):
    source = tmp_path / 'responses.jsonl'
    write_jsonl(source, [{'question': 'Retention?', 'expected_answer': '30 days',
                         'actual_response': '30 days'},
                        {'question': 'Quota?', 'expected_answer': '10', 'actual_response': '10'}])
    return source


def test_evaluation_resumes_frozen_plan_and_accepted_judgment(tmp_path):
    source, output = responses(tmp_path), tmp_path / 'evaluation.json'
    class Interrupted(EvaluationSkillAgent):
        def __call__(self, request):
            evidence = super().__call__(request)
            if request['stage'] == 'judgment':
                raise KeyboardInterrupt()
            return evidence
    with pytest.raises(KeyboardInterrupt):
        evaluate(source, output=output, skill_agent_factory=Interrupted, verbose=False)
    assert not output.exists()
    class Resumed(EvaluationSkillAgent):
        def __call__(self, request):
            assert request['stage'] == 'judgment'
            assert request['question'] == 'Quota?'
            return super().__call__(request)
    result = evaluate(source, output=output, skill_agent_factory=Resumed, verbose=False,
                      resume=Path(str(output) + '.work'))
    assert result['summary']['evaluated'] == 2
    assert result['aggregates']['correct']['true_rate'] == 1.0


def test_invalid_judgment_can_be_repaired_with_stable_id(tmp_path):
    class Repair(EvaluationSkillAgent):
        def __call__(self, request):
            if request['stage'] == 'plan':
                return super().__call__(request)
            invalid = self.tools['submit_judgment']({'values': {'correct': 'yes'}, 'reason': 'Compare.'},
                                                    request_id='initial')
            assert invalid['status'] == 'needs_revision'
            assert invalid['issues'][0]['field'] == 'values'
            fixed = self.tools['revise_judgment'](invalid['item_id'], 'repair', invalid['revision'],
                                               {'values': {'correct': True}, 'reason': 'Compare.'})
            assert fixed['status'] == 'accepted' and fixed['item_id'] == invalid['item_id']
            replay = self.tools['revise_judgment'](invalid['item_id'], 'repair', invalid['revision'],
                                                {'values': {'correct': True}, 'reason': 'Compare.'})
            assert replay == fixed
            return {'loaded_skills': [self.name], 'skill_files': {'SKILL.md': 'fixture'}}
    result = evaluate(responses(tmp_path), output=tmp_path / 'eval.json', skill_agent_factory=Repair, verbose=False)
    assert result['summary']['judge_error'] == 0


def test_long_response_requires_complete_observed_read_before_judgment(tmp_path):
    text = 'Long captured answer. ' * 500 + 'FINAL: 30 days'
    source = tmp_path / 'long.jsonl'
    write_jsonl(source, [{'question': 'Retention?', 'expected_answer': '30 days', 'actual_response': text}])
    class Reader(EvaluationSkillAgent):
        def __call__(self, request):
            if request['stage'] == 'plan':
                return super().__call__(request)
            assert request['actual_response'] is None
            assert request['response_read']['total_chars'] == len(text)
            premature = self.tools['submit_judgment']({'values': {'correct': True}, 'reason': 'Compare.'})
            assert premature['status'] == 'needs_revision'
            pieces, cursor = [], 0
            while cursor is not None:
                page = self.tools['read_trial_response'](request['trial_id'], cursor)
                pieces.append(page['text'])
                cursor = page['next_start']
            assert ''.join(pieces) == text
            self.tools['revise_judgment'](premature['item_id'], 'full-read', premature['revision'],
                                        {'values': {'correct': True}, 'reason': 'Read the complete captured answer.'})
            return {'loaded_skills': [self.name], 'skill_files': {'SKILL.md': 'fixture'}}
    result = evaluate(source, output=tmp_path/'eval.json', max_input_tokens=1024,
                      skill_agent_factory=Reader, verbose=False)
    assert result['items'][0]['response_read']['complete'] is True
    assert result['items'][0]['response_read']['read_ranges'] == [[0, len(text)]]


@pytest.mark.parametrize('change', ['responses', 'sidecar', 'criteria', 'corrupt_checkpoint'])
def test_resume_refuses_changed_inputs_or_damaged_checkpoint(tmp_path, change):
    source, output = responses(tmp_path), tmp_path/'eval.json'
    evaluate(source, output=output, skill_agent_factory=EvaluationSkillAgent, verbose=False)
    original = output.read_bytes()
    session = Path(str(output)+'.work')
    kwargs = {}
    if change == 'responses':
        source.write_text(source.read_text().replace('30 days', '60 days'), encoding='utf-8')
    elif change == 'sidecar':
        Path(str(source)+'.trials.jsonl').write_text('{}\n', encoding='utf-8')
    elif change == 'criteria':
        kwargs['criteria'] = 'A different standard.'
    else:
        envelope = json.loads((session/'session.json').read_text(encoding='utf-8'))
        envelope['data']['session_id'] = 'tampered'
        (session/'session.json').write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(ValueError, match='differs|integrity'):
        evaluate(source, output=output, resume=session, skill_agent_factory=EvaluationSkillAgent,
                 verbose=False, **kwargs)
    assert output.read_bytes() == original


def test_temporary_provider_failure_retries_only_the_unfinished_judgment(tmp_path):
    from lladar.exceptions import ProviderError
    failed = []
    class Temporary(EvaluationSkillAgent):
        def __call__(self, request):
            if request['stage'] == 'judgment' and request['question'] == 'Quota?' and len(failed) < 2:
                failed.append(request['question'])
                raise ProviderError('fixture temporarily unavailable')
            return super().__call__(request)
    result = evaluate(responses(tmp_path), output=tmp_path/'eval.json', skill_agent_factory=Temporary, verbose=False)
    assert result['summary']['evaluated'] == 2
    assert len(failed) == 2


def test_process_crash_releases_writer_and_preserves_frozen_plan(tmp_path):
    import subprocess
    import sys
    source, output = responses(tmp_path), tmp_path/'eval.json'
    program = """
from pathlib import Path
import os
from lladar.evaluation import evaluate
class Agent:
    def __init__(self, *, tools, **kwargs):
        self.tools = tools
    def __call__(self, request):
        self.tools['submit_plan']({'title':'Frozen', 'approach':'Compare answers.',
            'dimensions':[{'name':'correct','description':'Correct','kind':'boolean'}],
            'limitations':['Synthetic evidence.']})
        os._exit(9)
evaluate(SOURCE, output=OUTPUT, skill_agent_factory=Agent, verbose=False)
""".replace('SOURCE', repr(str(source))).replace('OUTPUT', repr(str(output)))
    child = subprocess.run([sys.executable, '-c', program], capture_output=True, timeout=30)
    assert child.returncode == 9, child.stderr.decode(errors='replace')
    class Resumed(EvaluationSkillAgent):
        def __call__(self, request):
            assert request['stage'] == 'judgment'
            assert request['plan']['title'] == 'Frozen'
            return super().__call__(request)
    result=evaluate(source, output=output, resume=Path(str(output)+'.work'),
                    skill_agent_factory=Resumed, verbose=False)
    assert result['summary']['evaluated'] == 2


def test_numeric_statistics_are_calculated_from_finite_judgments(tmp_path):
    source=tmp_path/'responses.jsonl'
    write_jsonl(source,[{'question':name,'expected_answer':'fixture','actual_response':'captured'}
                       for name in ('First','Second','Third')])
    class Numeric(EvaluationSkillAgent):
        def __call__(self,request):
            if request['stage']=='plan':
                self.tools['submit_plan']({'title':'Quality','approach':'Fixture judgment.',
                    'dimensions':[{'name':'quality','description':'Quality score','kind':'numeric'}],
                    'limitations':['Synthetic evidence.']})
            else:
                self.tools['submit_judgment']({'values':{'quality':{'First':1,'Second':3,'Third':5}[request['question']]},
                    'reason':'Fixture judgment.'})
            return {'loaded_skills':[self.name],'skill_files':{'SKILL.md':'fixture'}}
    result=evaluate(source,output=tmp_path/'eval.json',skill_agent_factory=Numeric,verbose=False)
    aggregate=result['aggregates']['quality']
    assert (aggregate['mean'],aggregate['median'],aggregate['min'],aggregate['max'])==(3,3,1,5)


def test_unknown_evaluator_programming_error_aborts_without_success_artifact(tmp_path):
    class Broken(EvaluationSkillAgent):
        def __call__(self, request):
            if request['stage'] == 'judgment':
                raise AttributeError('fixture programming error')
            return super().__call__(request)
    output = tmp_path / 'evaluation.json'
    with pytest.raises(AttributeError, match='programming error'):
        evaluate(responses(tmp_path),output=output,skill_agent_factory=Broken,verbose=False)
    assert not output.exists()
    assert Path(str(output)+'.work/session.json').exists()
