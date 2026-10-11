"""Recoverable generation exercised through the agreed public dataset API."""

import json
from pathlib import Path

from lladar.api import create_test_dataset
from test_github_dataset import CorpusAgent, CorpusClient


def test_atomic_question_submission_preserves_text_and_source_evidence(tmp_path):
    question = "Can't we keep C:\\notes\\raw.txt?\n請依原文回答保存天數。"

    class ToolAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            source = self.tools['list_sources']()[0]
            page = self.tools['read_source'](source['source_id'])
            result = self.tools['submit_question'](
                request_id='retention-question', question=question,
                expected_answer='30 days', source_refs=[page['source_refs'][0]['ref_id']],
                answer_basis='derived_criterion', rationale='Ask the supplied retention annotation.',
            )
            assert result['status'] == 'accepted'
            return {'loaded_skills': [self.skill.name]}

    output = tmp_path / 'qa.jsonl'
    records = create_test_dataset(
        from_github='https://github.com/example/corpus', output=output,
        github_client=CorpusClient(), skill_agent_factory=ToolAgent, verbose=False,
    )
    metadata = json.loads(Path(str(output) + '.generation.json').read_text(encoding='utf-8'))
    assert records == [{'question': question, 'expected_answer': '30 days', 'actual_response': None}]
    assert metadata['dataset']['lines'][0]['answer_basis'] == 'derived_criterion'
    assert metadata['knowledge_points'][0]['evidence'][0]['quote'].startswith('{"data_row": 1')


def test_bad_source_reference_can_be_revised_without_losing_an_accepted_question(tmp_path):
    class RepairAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            reference = page['source_refs'][0]['ref_id']
            first = self.tools['submit_question'](
                'first', 'What is the retention?', '30 days', [reference],
                'derived_criterion', 'Use the retention annotation.',
            )
            invalid = self.tools['submit_question'](
                'second', 'How many days are records kept?', '30 days', ['missing'],
                'derived_criterion', 'Use the retention annotation.',
            )
            assert invalid['status'] == 'needs_revision'
            assert invalid['issues'][0]['field'] == 'source_refs'
            corrected = self.tools['revise_question'](
                item_id=invalid['item_id'], request_id='repair-second', revision=invalid['revision'],
                question='How many days are records kept?', expected_answer='30 days',
                source_refs=[reference], answer_basis='derived_criterion',
                rationale='Use the retention annotation.',
            )
            assert corrected['status'] == 'accepted'
            assert corrected['item_id'] == invalid['item_id']
            assert self.tools['get_question'](first['item_id'])['status'] == 'accepted'
            return {'loaded_skills': [self.skill.name]}

    output = tmp_path / 'qa.jsonl'
    records = create_test_dataset(
        from_github='https://github.com/example/corpus', output=output,
        github_client=CorpusClient(), skill_agent_factory=RepairAgent, verbose=False,
    )
    assert [row['question'] for row in records] == ['What is the retention?', 'How many days are records kept?']


def test_revising_an_accepted_question_does_not_overwrite_a_later_question(tmp_path):
    class RevisionAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            refs = [page['source_refs'][0]['ref_id']]
            first = self.tools['submit_question']('first', 'Retention?', '30 days', refs,
                                                   'derived_criterion', 'Use the retention annotation.')
            self.tools['revise_question'](first['item_id'], 'update', first['revision'],
                                          'How long is the retention?', '30 days', refs,
                                          'derived_criterion', 'Clarify the same question.')
            self.tools['submit_question']('next', 'How many days are records kept?', '30 days', refs,
                                           'derived_criterion', 'Use the retention annotation.')
            return {'loaded_skills': [self.skill.name]}

    records = create_test_dataset(
        from_github='https://github.com/example/corpus', output=tmp_path / 'qa.jsonl',
        github_client=CorpusClient(), skill_agent_factory=RevisionAgent, verbose=False,
    )
    assert [row['question'] for row in records] == ['How long is the retention?', 'How many days are records kept?']


def test_repair_limit_rejects_only_the_unresolved_draft(tmp_path):
    class LimitedRepairAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            self.tools['submit_question']('good', 'Retention?', '30 days', [page['source_refs'][0]['ref_id']],
                                           'derived_criterion', 'Use the annotation.')
            draft = self.tools['submit_question']('bad', 'Missing evidence?', '30 days', ['missing'],
                                                  'derived_criterion', 'Use the annotation.')
            for attempt in range(3):
                draft = self.tools['revise_question'](
                    draft['item_id'], f'repair-{attempt}', draft['revision'],
                    'Missing evidence?', '30 days', ['missing'], 'derived_criterion', 'Use the annotation.',
                )
            assert draft['status'] == 'rejected'
            return {'loaded_skills': [self.skill.name]}

    output = tmp_path / 'qa.jsonl'
    records = create_test_dataset(
        from_github='https://github.com/example/corpus', output=output,
        github_client=CorpusClient(), skill_agent_factory=LimitedRepairAgent, verbose=False,
    )
    assert [row['question'] for row in records] == ['Retention?']
    metadata = json.loads(Path(str(output) + '.generation.json').read_text(encoding='utf-8'))
    assert any(item['reason'] == 'question_repair_exhausted' for item in metadata['skipped'])


def test_verbose_cli_shows_source_progress_without_logging_terminal_redraws(tmp_path, monkeypatch):
    import io
    from lladar.cli import main
    from test_agent_selected_generation import DirectAgent

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    monkeypatch.setattr('sys.stderr', terminal)
    source = tmp_path / 'notes.md'
    source.write_text('Service A keeps data for 30 days.', encoding='utf-8')
    log = tmp_path / 'progress.log'
    assert main(['create', 'test-dataset', '--knowledge', str(source),
                 '--output', str(tmp_path / 'qa.jsonl'), '--log', str(log)],
                skill_agent_factory=DirectAgent) == 0
    assert 'Read sources' in terminal.getvalue() and '\r' in terminal.getvalue()
    assert '\r' not in log.read_text(encoding='utf-8')
    assert '[GROUP]' in log.read_text(encoding='utf-8')


def test_equal_source_text_keeps_distinct_source_references(tmp_path):
    from test_agent_selected_generation import DirectAgent

    sources = tmp_path / 'sources'
    sources.mkdir()
    for name in ['a.md', 'b.md']:
        (sources / name).write_text('Service A keeps data for 30 days.', encoding='utf-8')

    class ReferenceAgent(DirectAgent):
        def __call__(self, request):
            pages = [self.tools['read_source'](source['source_id'])
                     for source in self.tools['list_sources']()]
            assert pages[0]['source_refs'][0]['ref_id'] != pages[1]['source_refs'][0]['ref_id']
            self.tools['submit_question'](
                'first-source', 'Retention?', '30 days', [pages[0]['source_refs'][0]['ref_id']],
                'derived_criterion', 'Use the first source.',
            )
            return self.evidence()

    output = tmp_path / 'qa.jsonl'
    create_test_dataset(sources, output=output, skill_agent_factory=ReferenceAgent, verbose=False)
    metadata = json.loads(Path(str(output) + '.generation.json').read_text(encoding='utf-8'))
    assert metadata['knowledge_points'][0]['evidence'][0]['source_id'] == 'source_001'


def test_invalid_revision_request_id_returns_repair_feedback(tmp_path):
    class RequestAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            refs = [page['source_refs'][0]['ref_id']]
            accepted = self.tools['submit_question'](
                'good', 'Retention?', '30 days', refs, 'derived_criterion', 'Use the annotation.',
            )
            invalid = self.tools['revise_question'](
                accepted['item_id'], [], accepted['revision'], 'Updated retention?', '30 days',
                refs, 'derived_criterion', 'Use the annotation.',
            )
            assert invalid['status'] == 'needs_revision'
            assert invalid['issues'][0]['field'] == 'request_id'
            assert self.tools['get_question'](accepted['item_id'])['revision'] == accepted['revision']
            return {'loaded_skills': [self.skill.name]}

    records = create_test_dataset(
        from_github='https://github.com/example/corpus', output=tmp_path / 'qa.jsonl',
        github_client=CorpusClient(), skill_agent_factory=RequestAgent, verbose=False,
    )
    assert records[0]['question'] == 'Retention?'


def test_interrupted_generation_resumes_saved_question_and_request(tmp_path):
    import pytest
    ids = []
    class InterruptedAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            saved = self.tools['submit_question']('saved', 'Retention?', '30 days',
                [page['source_refs'][0]['ref_id']], 'derived_criterion', 'Use annotation.')
            ids.append(saved['item_id'])
            raise KeyboardInterrupt()
    output = tmp_path / 'qa.jsonl'
    with pytest.raises(KeyboardInterrupt):
        create_test_dataset(from_github='https://github.com/example/corpus', output=output,
            github_client=CorpusClient(), skill_agent_factory=InterruptedAgent, verbose=False)
    session = Path(str(output) + '.work')
    assert (session / 'session.json').is_file()
    assert not output.exists()
    class ResumedAgent(CorpusAgent):
        def __call__(self, request):
            assert request['stage'] != 'github_discovery', 'saved discovery must be reused'
            if request['stage'] != 'generation':
                return super().__call__(request)
            assert self.tools['get_question'](ids[0])['status'] == 'accepted'
            assert request['existing_results']['qa_count'] == 1
            return {'loaded_skills': [self.skill.name]}
    records = create_test_dataset(from_github='https://github.com/example/corpus', output=output,
        github_client=CorpusClient(), skill_agent_factory=ResumedAgent, verbose=False, resume=session)
    assert [row['question'] for row in records] == ['Retention?']
    class NoCalls:
        def __init__(self, **kwargs):
            raise AssertionError('completed session must not call the model')
    assert create_test_dataset(from_github='https://github.com/example/corpus', output=output,
        github_client=CorpusClient(), skill_agent_factory=NoCalls, verbose=False, resume=session) == records


def test_cli_resume_uses_the_original_output_and_rejects_force(tmp_path, capsys):
    from lladar.cli import main
    from test_agent_selected_generation import DirectAgent
    source, output = tmp_path / 'notes.md', tmp_path / 'qa.jsonl'
    source.write_text('Service A keeps data for 30 days.', encoding='utf-8')
    assert main(['create', 'test-dataset', '--knowledge', str(source), '--output', str(output), '--no-verbose'],
                skill_agent_factory=DirectAgent) == 0
    session = Path(str(output) + '.work')
    class NoModel:
        def __init__(self, **kwargs):
            raise AssertionError('completed resume must not call model')
    assert main(['create', 'test-dataset', '--knowledge', str(source), '--resume', str(session), '--no-verbose'],
                skill_agent_factory=NoModel) == 0
    assert main(['create', 'test-dataset', '--knowledge', str(source), '--output', str(output),
                 '--resume', str(session), '--force', '--no-verbose'], skill_agent_factory=NoModel) == 2


def test_large_csv_sampling_keeps_logical_rows_and_original_ids(tmp_path):
    class LargeClient(CorpusClient):
        def list_files(self, *args):
            return [{'path': 'README.md', 'size': 120}, {'path': 'large.csv', 'size': 3 * 1024 * 1024}]
        def read_file(self, owner, repo, sha, path):
            if path == 'README.md':
                return b'Use large.csv for retention examples; answers are annotations.'
            return ('id,text,answer\n' + ''.join(f'{i},"line one\nline two {i}",30 days\n' for i in range(100000))).encode()
    class SampleAgent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'github_discovery':
                return super().__call__(request)
            read = self.tools['read_repository_file']('README.md')
            sample = self.tools['sample_dataset_rows']('large.csv', sample_size=3, seed=42)
            assert sample['total_rows'] == 100000
            assert sample['scope'] == 'full_population_sample'
            assert all('\n' in row['values']['text'] for row in sample['rows'])
            self.tools['select_dataset'](['large.csv'], 'Retention benchmark', 'Answer retention questions',
                'Reproducible sample across the entire table.', [{'read_id': read['read_id'], 'quote': 'answers are annotations'}])
            return {'loaded_skills': [self.skill.name]}
    output = tmp_path / 'qa.jsonl'
    create_test_dataset(from_github='https://github.com/example/corpus', output=output,
        github_client=LargeClient(), skill_agent_factory=SampleAgent, verbose=False)
    snapshots = json.loads((tmp_path / 'qa.sources.json').read_text(encoding='utf-8'))
    table = next(item for item in snapshots['files'] if item['path'] == 'large.csv')
    assert table['total_data_rows'] == 100000 and table['selected_data_rows'] == 3
    assert all(row['data_row'] > 3 for row in table['rows'])
    assert table['sha256'] != table['sample_sha256']


def test_failed_question_checkpoint_never_acknowledges_or_retains_unsaved_content(tmp_path, monkeypatch):
    import os
    import pytest
    from test_agent_selected_generation import DirectAgent
    replace = os.replace
    def disk_full(src, dest):
        if Path(dest).name == 'session.json':
            data = json.loads(Path(src).read_text(encoding='utf-8'))['data']
            if data['state'].get('generation_workspace', {}).get('question_drafts'):
                raise OSError('fixture disk full')
        return replace(src, dest)
    monkeypatch.setattr(os, 'replace', disk_full)
    source, output = tmp_path/'notes.md', tmp_path/'qa.jsonl'
    source.write_text('Service A keeps data for 30 days.', encoding='utf-8')
    class Writer(DirectAgent):
        def __call__(self, request):
            page = self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            with pytest.raises(OSError):
                self.tools['submit_question']('unsaved', 'Retention?', '30 days',
                    [page['source_refs'][0]['ref_id']], 'derived_criterion', 'Use annotation.')
            assert self.tools['list_knowledge_points']() == []
            return self.evidence()
    with pytest.raises(OSError):
        create_test_dataset(source, output=output, skill_agent_factory=Writer, verbose=False)
    assert not output.exists()
    saved = json.loads((Path(str(output)+'.work')/'session.json').read_text(encoding='utf-8'))['data']
    assert saved['state']['generation_workspace']['qa'] == {}


def test_jsonl_scan_budget_discloses_unknown_population_and_partial_scope(tmp_path):
    class Client(CorpusClient):
        def list_files(self, *args):
            return [{'path':'README.md','size':80},{'path':'large.jsonl','size':3000}]
        def read_file(self, owner, repo, sha, path):
            if path == 'README.md':
                return b'Answers are retention annotations.'
            return ''.join(json.dumps({'id':i,'text':'Retention?','answer':'30 days'})+'\n' for i in range(100)).encode()
    class Agent(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'github_discovery':
                return super().__call__(request)
            read = self.tools['read_repository_file']('README.md')
            sample = self.tools['sample_dataset_rows']('large.jsonl', sample_size=2, seed=42, max_rows=10)
            assert sample['total_rows'] is None and sample['scope'] == 'scanned_prefix_sample'
            self.tools['select_dataset'](['large.jsonl'],'Retention','Answer retention','Only scanned prefix.',
                [{'read_id':read['read_id'],'quote':'Answers are retention annotations.'}])
            return {'loaded_skills':[self.skill.name]}
    output=tmp_path/'qa.jsonl'
    create_test_dataset(from_github='https://github.com/example/corpus',output=output,
        github_client=Client(),skill_agent_factory=Agent,verbose=False)
    metadata=json.loads(Path(str(output)+'.generation.json').read_text(encoding='utf-8'))
    assert metadata['github']['counts']['total_rows_in_selected_tables'] is None
    assert metadata['status'] == 'partial'
    report=(tmp_path/'qa.generation-report.md').read_text(encoding='utf-8')
    assert 'scanned_prefix_sample' in report and 'scan_budget' in report


def test_builtin_generation_exposes_tools_without_candidate_file_delivery(tmp_path):
    class Builtin(CorpusAgent):
        def __call__(self, request):
            if request['stage'] == 'generation':
                assert 'candidate_path' not in request
                assert 'candidate_contract' not in request
            return super().__call__(request)
    records=create_test_dataset(from_github='https://github.com/example/corpus',output=tmp_path/'qa.jsonl',
        github_client=CorpusClient(),skill_agent_factory=Builtin,verbose=False)
    assert records


def test_claimed_original_answer_must_match_a_saved_source_field(tmp_path):
    class Original(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page=self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            refs=[page['source_refs'][0]['ref_id']]
            wrong=self.tools['submit_question']('wrong','Retention?','90 days',refs,'source_answer','Keep original answer.')
            assert wrong['status']=='needs_revision'
            assert wrong['issues'][0]['field']=='expected_answer'
            corrected=self.tools['revise_question'](wrong['item_id'],'correct',wrong['revision'],
                'Retention?','30 days',refs,'source_answer','Keep original answer.')
            assert corrected['status']=='accepted'
            return {'loaded_skills':[self.skill.name]}
    output=tmp_path/'qa.jsonl'
    create_test_dataset(from_github='https://github.com/example/corpus',output=output,
        github_client=CorpusClient(),skill_agent_factory=Original,verbose=False)
    metadata=json.loads(Path(str(output)+'.generation.json').read_text(encoding='utf-8'))
    mapping=metadata['dataset']['lines'][0]['source_answer_mapping']
    assert mapping[0]['data_row']==1 and mapping[0]['field']=='answer'


def test_unresolved_revision_is_not_published_as_the_previous_valid_answer(tmp_path):
    class Revision(CorpusAgent):
        def __call__(self, request):
            if request['stage'] != 'generation':
                return super().__call__(request)
            page=self.tools['read_source'](self.tools['list_sources']()[0]['source_id'])
            refs=[page['source_refs'][0]['ref_id']]
            self.tools['submit_question']('keep','Retention?','30 days',refs,'derived_criterion','Use annotation.')
            current=self.tools['submit_question']('edit','Previous version?','30 days',refs,'derived_criterion','Use annotation.')
            current=self.tools['revise_question'](current['item_id'],'accepted-edit',current['revision'],
                'Saved version?','30 days',refs,'derived_criterion','Clarified draft.')
            self.tools['revise_question'](current['item_id'],'invalid-edit',current['revision'],
                'New version?','90 days',['missing'],'derived_criterion','Changed draft.')
            return {'loaded_skills':[self.skill.name]}
    output=tmp_path/'qa.jsonl'
    records=create_test_dataset(from_github='https://github.com/example/corpus',output=output,
        github_client=CorpusClient(),skill_agent_factory=Revision,verbose=False)
    assert [row['question'] for row in records]==['Retention?']
    metadata=json.loads(Path(str(output)+'.generation.json').read_text(encoding='utf-8'))
    assert metadata['status']=='partial'


def test_large_local_source_is_read_in_host_assigned_batches(tmp_path):
    from test_agent_selected_generation import DirectAgent
    source, output=tmp_path/'large.md',tmp_path/'qa.jsonl'
    source.write_text('Retention is 30 days.\n'*4000,encoding='utf-8')
    assignments=[]
    class Batched(DirectAgent):
        def __call__(self, request):
            assigned=request['sources'][0]
            assert assigned['assigned_end']-assigned['assigned_start']<=24000
            assignments.append((assigned['assigned_start'],assigned['assigned_end']))
            cursor=assigned['assigned_start']
            refs=[]
            while cursor is not None:
                page=self.tools['read_source'](assigned['source_id'],cursor)
                refs.extend(item['ref_id'] for item in page['source_refs'])
                cursor=page['next_start']
            self.tools['submit_question'](str(assigned['assigned_start']),
                f'What is the retention under policy segment {len(assignments)}?', '30 days', refs,
                'derived_criterion','Read the assigned policy segment.')
            return self.evidence()
    records=create_test_dataset(source,output=output,skill_agent_factory=Batched,verbose=False)
    assert len(records)==len(assignments)>1
    assert assignments[0][0]==0 and assignments[-1][1]==len(source.read_text(encoding='utf-8'))
    metadata=json.loads(Path(str(output)+'.generation.json').read_text(encoding='utf-8'))
    assert metadata['sources'][0]['unread_ranges']==[]


def test_interrupted_discovery_keeps_commit_and_saved_reads(tmp_path):
    import pytest
    output = tmp_path / 'qa.jsonl'
    class Interrupted(CorpusAgent):
        def __call__(self, request):
            if request['stage'] == 'github_discovery':
                self.tools['read_repository_file']('README.md')
                raise KeyboardInterrupt()
            return super().__call__(request)
    with pytest.raises(KeyboardInterrupt):
        create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                            github_client=CorpusClient(), skill_agent_factory=Interrupted, verbose=False)
    class FrozenClient(CorpusClient):
        def resolve_repository(self, *args):
            raise AssertionError('must reuse the frozen discovery commit')
    records = create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                                 github_client=FrozenClient(), skill_agent_factory=CorpusAgent,
                                 resume=Path(str(output) + '.work'), verbose=False)
    assert len(records) == 1


def test_accepted_generation_review_survives_process_interruption(tmp_path):
    import pytest
    output = tmp_path / 'qa.jsonl'
    class Interrupted(CorpusAgent):
        def __call__(self, request):
            evidence = super().__call__(request)
            if request['stage'] == 'github_review':
                raise KeyboardInterrupt()
            return evidence
    with pytest.raises(KeyboardInterrupt):
        create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                            github_client=CorpusClient(), skill_agent_factory=Interrupted, verbose=False)
    class Resumed(CorpusAgent):
        def __call__(self, request):
            if request['stage'] == 'github_review':
                raise AssertionError('accepted reviews must not be repeated')
            return super().__call__(request)
    assert len(create_test_dataset(from_github='https://github.com/example/corpus', output=output,
                                   github_client=CorpusClient(), skill_agent_factory=Resumed,
                                   resume=Path(str(output) + '.work'), verbose=False)) == 1
