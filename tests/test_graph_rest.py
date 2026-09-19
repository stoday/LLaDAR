"""Real graph extraction and localhost processes; no model or HTTP mocks."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from lladar.graph_discovery import CodeGraph, resolve_graph_python
from lladar.interfaces import choose_interface, validate_plan, validate_service_url
from lladar.adapter_workspace import WorkspaceExplorer


def test_graph_off_and_missing_are_explicit(tmp_path):
    source, evidence = tmp_path / 'source', tmp_path / 'evidence'
    source.mkdir(); evidence.mkdir()
    assert CodeGraph(source, evidence, enabled=False).summary['status'] == 'disabled'
    graph = CodeGraph(source, evidence, python=tmp_path / 'missing')
    assert graph.summary['status'] == 'fallback'
    assert graph.query('anything')['nodes'] == []


def test_real_multilingual_graph(tmp_path):
    try:
        resolve_graph_python(None)
    except (ValueError, subprocess.SubprocessError):
        pytest.skip('Install independent graphifyy tool for integration test')
    source, evidence = tmp_path / 'source', tmp_path / 'evidence'
    source.mkdir(); evidence.mkdir()
    (source / 'app.py').write_text('def inner(): return 1\ndef outer(): return inner()\n')
    (source / 'client.ts').write_text('export function ask() { return fetch("/api/chat"); }\n')
    (source / '.env').write_text('SECRET=never-copy')
    graph = CodeGraph(source, evidence)
    assert graph.summary['status'] == 'ready', graph.summary
    assert set(graph.summary['parser_inputs']) == {'app.py', 'client.ts'}
    assert not (evidence / 'graph-source/.env').exists()
    assert any(n.get('source_file') == 'client.ts' for n in graph.nodes)
    result = graph.query('outer', 2)
    assert len(result['nodes']) <= 6 and len(result['edges']) <= 4
    assert any(e.get('relation') == 'calls' for e in result['edges'])
    assert all(any(all(str(original.get(k)) == v for k, v in e.items()) for original in graph.edges)
               for e in result['edges'])


@pytest.mark.parametrize('mode', ['success', 'readiness-failure', 'body-failure'])
def test_real_service_cleanup(tmp_path, mode):
    if not shutil.which('node'):
        pytest.skip('Node is required for real service lifecycle integration')
    source = Path(__file__).parent / 'fixtures/rest_agent/server.js'
    shutil.copyfile(source, tmp_path / 'server.js')
    code = '''
import json, sys
from pathlib import Path
from urllib.request import urlopen
from lladar.service_runtime import managed_service
try:
    with managed_service(['node','server.js','--port','{port}','--host','{host}'],
                         readiness_path='/missing' if sys.argv[1]=='readiness-failure' else '/health',
                         timeout=1 if sys.argv[1]=='readiness-failure' else 10) as url:
        Path('url.txt').write_text(url)
        assert json.load(urlopen(url+'/health'))['ready']
        if sys.argv[1]=='body-failure': raise ValueError('failure during adapter')
except (TimeoutError, ValueError):
    if sys.argv[1]=='success': raise
'''
    result = subprocess.run([sys.executable, '-c', code, mode], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'lladar-service.log').exists()
    state = json.loads((tmp_path / 'lladar-service.json').read_text())
    assert state['stopped']
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', state['port'])) != 0
    if mode != 'readiness-failure':
        assert (tmp_path / 'url.txt').exists()
    if (tmp_path / 'url.txt').exists():
        port = int((tmp_path / 'url.txt').read_text().rsplit(':', 1)[1])
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0


def test_missing_service_requirements_cannot_be_waived(tmp_path):
    (tmp_path / 'server.py').write_text('def chat(): pass\n')
    candidate = dict(id='http', label='HTTP', entrypoint='POST /chat', public_boundary=True,
                     rationale='Public route', flow=['HTTP', 'agent'], output='answer', transport='http',
                     service=dict(command=[], readiness_path='', request='POST /chat', response='answer',
                                  coverage=['API'], excluded=[], missing=['How to start?']),
                     evidence=[dict(path='server.py', line=1, quote='def chat(): pass')])
    plan = validate_plan(dict(candidates=[candidate], unresolved=[]), WorkspaceExplorer(tmp_path))
    assert choose_interface(plan, interactive=False) == ('pause', '')
    with pytest.raises(ValueError, match='incomplete'):
        choose_interface(plan, interactive=False, candidate_id='http')


def test_adapter_timeout_stops_actual_service(tmp_path, isolated_target_python):
    if not shutil.which('node'):
        pytest.skip('Node required')
    from lladar.auto_adapter import AutoAdapter
    project = tmp_path / 'project'
    project.mkdir()
    shutil.copyfile(Path(__file__).parent / 'fixtures/rest_agent/server.js', project / 'server.js')
    adapter = AutoAdapter(project, python=isolated_target_python, env_file=None,
                          model='unused', timeout=5, graphify=False)
    source = b'''from lladar_service_runtime import managed_service
import time
with managed_service(['node','server.js','--port','{port}'],readiness_path='/health'):
    time.sleep(60)
'''
    result = adapter.execute(source, 'question', phase='verification')
    assert not result['ok'] and 'TimeoutExpired' in result['error']
    state = json.loads((Path(result['workspace']) / 'lladar-service.json').read_text())
    assert state['stopped'] and state['cleanup'] == 'runner'
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', state['port'])) != 0


@pytest.mark.parametrize('url', ['file:///tmp/service', 'http://user:password@localhost',
                                 'http://localhost/?token=secret', 'http://localhost/#secret'])
def test_service_url_rejects_credentials_and_non_http(url):
    with pytest.raises(ValueError):
        validate_service_url(url)


def test_existing_service_requires_explicit_exact_url(tmp_path):
    (tmp_path / 'server.py').write_text('def chat(): pass\n')
    service = dict(mode='existing', base_url='http://127.0.0.1:8000', command=[],
                   readiness_path='', request='POST /chat', response='answer',
                   coverage=['API'], excluded=[], missing=[])
    plan = dict(candidates=[dict(id='chat', label='Chat', entrypoint='POST /chat', public_boundary=True,
        rationale='Public route', flow=['HTTP', 'agent'], output='answer', transport='http', service=service,
        evidence=[dict(path='server.py', line=1, quote='def chat(): pass')])], unresolved=[])
    explorer = WorkspaceExplorer(tmp_path)
    with pytest.raises(ValueError, match='user-supplied'):
        validate_plan(plan, explorer)
    validate_plan(plan, explorer, service_url=service['base_url'])
    assert choose_interface(plan, interactive=False) == ('automatic', 'chat')


def test_stale_service_record_is_not_cleaned(tmp_path):
    from lladar.service_runtime import cleanup_saved_service
    path = tmp_path / 'lladar-service.json'
    # Invalid PID must not even reach validation for another request's record.
    state = dict(pid=-1, port=8000, stopped=False, request_id='previous-request')
    path.write_text(json.dumps(state))
    cleanup_saved_service(tmp_path, request_id='current-request')
    assert json.loads(path.read_text()) == state
