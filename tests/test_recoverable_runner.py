import json
from pathlib import Path
import pytest
from lladar.runner import run_agent
from test_simple_pipeline import StrategySkillAgent, write_jsonl


@pytest.mark.parametrize('changed_report', [False, True])
def test_resume_after_browser_prepare_failure_preserves_blocked_report(tmp_path, changed_report):
    source, output = tmp_path/'qa.jsonl', tmp_path/'responses.jsonl'
    write_jsonl(source, [{'question':'Retention?', 'expected_answer':'30 days', 'actual_response':None}])
    class Target:
        evidence = {}
        def __init__(self, **kwargs):
            pass
        def prepare(self, *args, **kwargs):
            raise RuntimeError('fixture discovery failure')
        def close(self):
            pass
    options = dict(page_url='https://fixture.test/chat', browser_target_factory=Target,
                   strategy_agent_factory=StrategySkillAgent, verbose=False)
    with pytest.raises(RuntimeError, match='fixture discovery failure'):
        run_agent(source, output, **options)
    report = Path(str(output)+'.run.json')
    original = report.read_bytes()
    if changed_report:
        report.write_text('{}', encoding='utf-8')
    class Resumed(Target):
        def prepare(self, *args, **kwargs):
            pass
        def answer(self, *args):
            return 'Resumed answer'
    options['browser_target_factory'] = Resumed
    if changed_report:
        with pytest.raises(FileExistsError):
            run_agent(source, output, resume=Path(str(output)+'.work'), **options)
        assert report.read_text() == '{}'
    else:
        assert run_agent(source, output, resume=Path(str(output)+'.work'), **options) == 1
        assert json.loads(output.read_text())['actual_response'] == 'Resumed answer'
        assert any(p.read_bytes() == original for p in Path(str(output)+'.work').glob('blocked-run-*.json'))


def test_resume_does_not_repeat_finished_or_completion_unknown_trials(tmp_path):
    source, output = tmp_path / 'qa.jsonl', tmp_path / 'responses.jsonl'
    write_jsonl(source, [{'question': 'Retention?', 'expected_answer': '30 days', 'actual_response': None},
                        {'question': 'Quota?', 'expected_answer': '10', 'actual_response': None}])
    sent = []
    def interrupted(question):
        sent.append(question)
        if len(sent) == 2:
            raise KeyboardInterrupt()
        return 'Original answer'
    with pytest.raises(KeyboardInterrupt):
        run_agent(source, output, answer=interrupted, strategy_agent_factory=StrategySkillAgent, verbose=False)
    assert not output.exists()
    remaining = []
    def resumed(question):
        remaining.append(question)
        return 'Resumed answer'
    class NoStrategy:
        def __init__(self, **kwargs):
            raise AssertionError('frozen schedule must be reused')
    assert run_agent(source, output, answer=resumed, strategy_agent_factory=NoStrategy, verbose=False,
                     resume=Path(str(output)+'.work')) == 2
    trials = [json.loads(line) for line in Path(str(output)+'.trials.jsonl').read_text().splitlines()]
    assert len(remaining) == 4
    assert len({trial['trial_id'] for trial in trials}) == 6
    assert trials[0]['actual_response'] == 'Original answer'
    assert trials[1]['completion_state'] == 'completion_unknown'
    assert trials[1]['status'] == 'execution_error'


def test_failed_run_publication_preserves_all_previous_artifacts(tmp_path, monkeypatch):
    import os
    source, output = tmp_path / 'qa.jsonl', tmp_path / 'responses.jsonl'
    write_jsonl(source, [{'question': 'Retention?', 'expected_answer': '30 days', 'actual_response': None}])
    run_agent(source, output, answer=lambda _: 'original', verbose=False)
    paths = [output, Path(str(output)+'.trials.jsonl'), Path(str(output)+'.run.json')]
    original = {path: path.read_bytes() for path in paths}
    replace = os.replace
    def fail_marker(src, dest):
        if Path(dest) == paths[-1] and str(src).endswith('.tmp'):
            raise OSError('fixture marker publication failure')
        return replace(src, dest)
    monkeypatch.setattr(os, 'replace', fail_marker)
    with pytest.raises(OSError):
        run_agent(source, output, answer=lambda _: 'replacement', verbose=False, force=True)
    assert {path: path.read_bytes() for path in paths} == original


def test_long_captures_are_saved_per_trial_without_growing_the_session_manifest(tmp_path):
    source, output = tmp_path/'qa.jsonl', tmp_path/'responses.jsonl'
    write_jsonl(source, [{'question':'Retention?', 'expected_answer':'30 days', 'actual_response':None}])
    answer = 'Verbatim capture\n' * 100000 + 'FINAL'
    run_agent(source, output, answer=lambda _: answer, verbose=False)
    session = Path(str(output)+'.work')
    assert (session/'session.json').stat().st_size < 100000
    records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
    assert records[0]['actual_response'] == answer
    trials = [json.loads(line) for line in Path(str(output)+'.trials.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(trials) == 3 and all(trial['actual_response'] == answer for trial in trials)


def test_resume_keeps_the_original_force_authorization(tmp_path):
    source, output=tmp_path/'qa.jsonl',tmp_path/'responses.jsonl'
    write_jsonl(source,[{'question':'Retention?', 'expected_answer':'30 days','actual_response':None}])
    run_agent(source,output,answer=lambda _:'original',verbose=False)
    sent=[]
    def interrupted(question):
        sent.append(question)
        if len(sent)==2:
            raise KeyboardInterrupt()
        return 'replacement'
    with pytest.raises(KeyboardInterrupt):
        run_agent(source,output,answer=interrupted,verbose=False,force=True)
    session=next(tmp_path.glob('responses.jsonl.work-*'))
    run_agent(source,output,answer=lambda _:'continued',verbose=False,resume=session)
    assert json.loads(output.read_text(encoding='utf-8').splitlines()[0])['actual_response']=='replacement'


def test_reordered_schedule_keeps_response_line_and_source_case_distinct(tmp_path):
    from lladar.evaluation import evaluate
    from test_simple_pipeline import EvaluationSkillAgent
    source,output=tmp_path/'qa.jsonl',tmp_path/'responses.jsonl'
    write_jsonl(source,[{'question':'First','expected_answer':'First','actual_response':None},
                       {'question':'Second','expected_answer':'Second','actual_response':None}])
    class Reverse(StrategySkillAgent):
        def __call__(self,request):
            self.tools['read_dataset']()
            self.tools['write_strategy']('def select_cases(cases, schedule):\n    for case in cases[::-1]:\n        schedule(case)\n')
            return {'loaded_skills':[self.name],'skill_files':{'SKILL.md':'fixture'}}
    run_agent(source,output,answer=lambda question:question,strategy_agent_factory=Reverse,verbose=False)
    result=evaluate(output,output=tmp_path/'eval.json',skill_agent_factory=EvaluationSkillAgent,verbose=False)
    assert result['summary']['evaluated']==2
    trials=[json.loads(line) for line in Path(str(output)+'.trials.jsonl').read_text().splitlines()]
    assert [(row['record_index'],row['source_record_index']) for row in trials]==[(1,2),(2,1)]


def test_killed_process_during_publication_can_resume_without_target_replay(tmp_path):
    import subprocess, sys
    source = tmp_path / 'dataset.jsonl'
    output = tmp_path / 'responses.jsonl'
    source.write_text(json.dumps({'question':'Question?', 'expected_answer':'Expected', 'actual_response':None})+'\n',encoding='utf-8')
    script = """import os,sys
from pathlib import Path
from lladar.runner import run_agent
source,output=map(Path,sys.argv[1:])
replace=os.replace
def crash(src,dst):
    replace(src,dst)
    if Path(dst)==output: os._exit(9)
os.replace=crash
run_agent(source,output,answer=lambda question:'Captured',verbose=False,max_tool_calls=1000)
"""
    completed=subprocess.run([sys.executable,'-c',script,str(source),str(output)],capture_output=True)
    assert completed.returncode==9
    def forbidden(question):
        raise AssertionError('completed target request must not repeat')
    assert run_agent(source,output,answer=forbidden,resume=Path(str(output)+'.work'),verbose=False,max_tool_calls=1000)==1
    assert json.loads(output.read_text(encoding='utf-8'))['actual_response']=='Captured'
