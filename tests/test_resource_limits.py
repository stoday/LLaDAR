import pytest

from lladar.resource_limits import DEFAULT_LIMITS, resource_limit


def test_workload_limit_can_be_increased_without_a_code_change(monkeypatch):
    monkeypatch.setenv('LLADAR_MAX_INPUT_BYTES', str(32 * 1024 * 1024))
    assert resource_limit('MAX_INPUT_BYTES') == 32 * 1024 * 1024


@pytest.mark.parametrize('value', ['0', '-1', 'bad'])
def test_invalid_workload_budget_has_a_safe_configuration_error(monkeypatch, value):
    monkeypatch.setenv('LLADAR_MAX_INPUT_BYTES', value)
    with pytest.raises(ValueError, match='LLADAR_MAX_INPUT_BYTES must be a positive integer'):
        resource_limit('MAX_INPUT_BYTES')


def test_unset_workload_budget_uses_shared_default(monkeypatch):
    monkeypatch.delenv('LLADAR_MAX_INPUT_BYTES', raising=False)
    assert resource_limit('MAX_INPUT_BYTES') == DEFAULT_LIMITS['MAX_INPUT_BYTES']


@pytest.mark.parametrize('value', ['1', '2'])
def test_independent_browser_verification_cannot_be_reduced_below_three(monkeypatch, value):
    monkeypatch.setenv('LLADAR_BROWSER_DISCOVERY_VERIFICATIONS', value)
    with pytest.raises(ValueError, match='LLADAR_BROWSER_DISCOVERY_VERIFICATIONS must be at least 3'):
        resource_limit('BROWSER_DISCOVERY_VERIFICATIONS')


def test_independent_browser_verification_can_be_increased(monkeypatch):
    monkeypatch.setenv('LLADAR_BROWSER_DISCOVERY_VERIFICATIONS', '7')
    assert resource_limit('BROWSER_DISCOVERY_VERIFICATIONS') == 7


def test_changing_workload_override_rejects_resume(tmp_path, monkeypatch):
    import json
    from lladar.runner import run_agent

    dataset = tmp_path / 'dataset.jsonl'
    output = tmp_path / 'answers.jsonl'
    dataset.write_text(json.dumps({'question': 'Q', 'expected_answer': 'A', 'actual_response': None}) + '\n', encoding='utf-8')
    monkeypatch.setenv('LLADAR_MAX_INPUT_BYTES', '8388608')

    def interrupted(question):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_agent(dataset, output, answer=interrupted, skill='src/lladar/skill_assets/run-agent-random-sample', verbose=False)
    session = next(tmp_path.glob('answers.jsonl.work*'))
    saved = json.loads((session / 'session.json').read_text(encoding='utf-8'))['data']['configuration']
    assert saved['resource_limits']['MAX_INPUT_BYTES'] == 8388608
    monkeypatch.setenv('LLADAR_MAX_INPUT_BYTES', '16777216')
    with pytest.raises(ValueError, match='resume configuration differs: resource_limits'):
        run_agent(dataset, output, answer=interrupted, skill='src/lladar/skill_assets/run-agent-random-sample', verbose=False, resume=session)
