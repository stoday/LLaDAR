"""Native browser transport dry run and explicitly authorized Akasha acceptance."""
import importlib.util
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


def _harness():
    path = Path(__file__).parents[1] / 'scripts' / 'verify_streamlit_discovery_live.py'
    spec = importlib.util.spec_from_file_location('streamlit_discovery_live_harness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('url', ['https://example.org', 'http://example.org', 'http://user:password@127.0.0.1:8517'])
def test_native_harness_only_accepts_local_page_without_embedded_credentials(url):
    with pytest.raises(ValueError, match='local HTTP'):
        _harness()._local_url(url)


@pytest.mark.parametrize('previous_status', ['failed', 'interrupted'])
def test_native_resume_preserves_approved_ledger_and_prior_failed_report(tmp_path, previous_status):
    root = tmp_path / 'existing'
    (root / 'initial').mkdir(parents=True)
    ledger = {'approved': True, 'started_at': 1234.5, 'counts': {'model_calls': 73, 'requests': 147},
              'turns': {'probe-1': {'status': 'not_attempted'}}}
    (root / 'initial' / 'ledger.json').write_text(json.dumps(ledger), encoding='utf-8')
    (root / 'initial' / 'candidate.json').write_text('{}', encoding='utf-8')
    (root / 'acceptance.json').write_text(json.dumps({'status': previous_status, 'reason': 'browser_agent_time_exhausted'}), encoding='utf-8')
    baseline = _harness()._prepare_output(root, resume=True, paid=True)
    assert baseline['counts'] == ledger['counts']
    assert baseline['started_at'] == ledger['started_at']
    assert json.loads((root / 'initial' / 'ledger.json').read_text(encoding='utf-8')) == ledger
    assert json.loads((root / baseline['previous_report']).read_text(encoding='utf-8'))['reason'] == 'browser_agent_time_exhausted'


@pytest.mark.parametrize('status', ['attempted_unknown', 'accepted', 'completed'])
def test_native_resume_refuses_to_repeat_previously_submitted_website_turn(tmp_path, status):
    (tmp_path / 'initial').mkdir()
    (tmp_path / 'initial' / 'ledger.json').write_text(json.dumps({'approved': True,
        'turns': {'formal-1': {'status': status}}}), encoding='utf-8')
    (tmp_path / 'initial' / 'candidate.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'acceptance.json').write_text('{"status":"failed"}', encoding='utf-8')
    with pytest.raises(ValueError, match='no prior website submissions'):
        _harness()._prepare_output(tmp_path, resume=True, paid=True)
    assert not list(tmp_path.glob('acceptance-attempt-*.json'))


@pytest.mark.browser
def test_native_streamlit_ui_uses_persistent_binary_websocket_without_model_calls(tmp_path):
    url = os.environ.get('LLADAR_STREAMLIT_DISCOVERY_URL', 'http://127.0.0.1:8517')
    try:
        with urlopen(_harness()._local_url(url), timeout=2):
            pass
    except (URLError, TimeoutError, OSError):
        pytest.skip('Start the native example_project/streamlit_qa/app.py page first')
    result = _harness().verify_streamlit(page_url=url, output_root=tmp_path / 'dry', timeout=30)
    assert result['status'] == 'passed'
    assert result['model_calls'] == 0
    assert result['ui_answers_exact'] == 2
    assert result['persistent_connection_reused'] is True
    assert result['formal_history_preserved'] is True
    assert all(turn['binary_sent'] > 0 and turn['binary_received'] > 0 for turn in result['turns'])
    assert result['binary_decoding_proven'] is False


@pytest.mark.browser
@pytest.mark.live_gemini
def test_real_akasha_discovers_native_streamlit_binary_protocol_without_preseed(tmp_path):
    if os.environ.get('LLADAR_LIVE_STREAMLIT_DISCOVERY') != '1':
        pytest.skip('Set LLADAR_LIVE_STREAMLIT_DISCOVERY=1 to permit real model calls')
    result = _harness().verify_streamlit(page_url=os.environ.get('LLADAR_STREAMLIT_DISCOVERY_URL', 'http://127.0.0.1:8517'),
        output_root=tmp_path / 'live', paid=True,
        model=os.environ.get('LLADAR_BROWSER_DISCOVERY_MODEL', 'gemini:gemini-3.8-flash'),
        env_file=os.environ.get('LLADAR_BROWSER_DISCOVERY_ENV_FILE', '.env'), timeout=600)
    assert result['status'] == 'passed'
    assert result['preseeded_adapter'] is False
    assert result['preseeded_codec'] is False
    assert result['initial']['formal_answers_exact'] == 5
    assert result['initial']['verification_rounds'] >= 3
    assert result['initial']['receive_method'] == 'network'
    assert result['initial']['binary_decoding_proven'] is True
    assert result['initial']['native_material_proofs']
    assert result['reuse']['verification_rounds'] >= 3
    assert result['reuse']['formal_answers_exact'] == 1
    assert result['reuse']['receive_method'] == 'network'
    assert result['reuse']['adapter_sha256'] == result['initial']['adapter_sha256']
