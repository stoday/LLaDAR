"""Failure artifacts contain metadata, never model or target content."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from lladar.exceptions import ProviderError


def load_script(monkeypatch):
    scripts = Path(__file__).parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('live_rest_acceptance_test', scripts / 'verify_rest_graph_live.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_export_drops_untrusted_content(tmp_path, monkeypatch):
    script = load_script(monkeypatch)
    rows = [{'event': 'start', 'thinking_level': 'low', 'include_thoughts': False,
             'prompt': 'private prompt', 'api_key': 'private credential'},
            {'event': 'end', 'usage': {'input_tokens': 7, 'output_tokens': 3, 'private': 'private usage'},
             'thinking_tokens': 2, 'finish_reason': 'STOP', 'answer': 'private answer'},
            {'event': 'error', 'error_code': 'private error response'}, ['private malformed record']]
    (tmp_path / 'model-events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    (tmp_path / 'trace.jsonl').write_text(json.dumps({'event': 'model_error', 'detail': json.dumps({
        'event': 'error', 'error_code': 'provider_timeout', 'raw_response': 'private response'})}), encoding='utf-8')
    script.collect_diagnostics(tmp_path)
    text = (tmp_path / 'diagnostics.json').read_text()
    assert 'private' not in text
    events = json.loads(text)['model_events']
    assert events[0]['thinking_level'] == 'low' and events[0]['include_thoughts'] is False
    assert any(row.get('thinking_tokens') == 2 for row in events)
    assert any(row.get('error_code') == 'provider_timeout' for row in events)


def test_failed_acceptance_always_writes_safe_summary(tmp_path, monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setattr(script, '__file__', str(tmp_path / 'scripts/verify.py'))
    def fail(_args, _output):
        raise ProviderError('provider_transport_error')
    monkeypatch.setattr(script, 'verify', fail)
    monkeypatch.setattr(sys, 'argv', ['verify.py', '--target-python', sys.executable])
    with pytest.raises(SystemExit) as caught:
        script.main()
    assert caught.value.code == 1
    output, = (tmp_path / '.lladar').glob('live-rest-graph-*')
    summary = json.loads((output / 'verification.json').read_text())
    assert summary == {'status': 'failed', 'error_type': 'ProviderError', 'error_code': 'provider_transport_error'}
    assert (output / 'diagnostics.json').is_file()
