"""Paid acceptance: Graphify + Node REST public boundary + real Python agent."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import socket
import sys

from lladar.runner import DEFAULT_ADAPTER_MODEL, run_agent
from lladar.project_inventory import inventory
from lladar.model_runtime import PROVIDER_CODES, provider_error_code
from verify_auto_adapter_live import dataset
from live_acceptance_runtime import runtime_evidence


def select_rest_interface(plan):
    candidates = [
        candidate for candidate in plan['candidates']
        if candidate['public_boundary']
        and candidate.get('transport') == 'http'
        and candidate.get('service', {}).get('missing') == []
        and 'POST' in candidate['service'].get('request', '')
        and '/api/chat' in candidate['service'].get('request', '')
        and candidate['service'].get('readiness_path') == '/health'
        and 'server.js' in candidate['service'].get('command', [])
        and 'node' in candidate['service'].get('command', [])
    ]
    assert len(candidates) == 1, 'Expected exactly one complete public POST /api/chat candidate'
    return candidates[0]['id']


def verify(args, output):
    runtime_evidence(args.target_python)
    root = Path(__file__).resolve().parents[1]
    target = root / 'tests/fixtures/rest_agent'
    before = inventory(target)
    data, answers = output / 'dataset.jsonl', output / 'answers.jsonl'
    data.write_text(json.dumps(dataset('langchain'), ensure_ascii=False) + '\n', encoding='utf-8')
    count = run_agent(data, answers, project=target, target_python=args.target_python,
                      env_file=args.env_file, model=args.model, runs_root=output / 'runs',
                      timeout=300, interactive=False, interface_selector=select_rest_interface)
    assert count == 1
    evidence_directories = [path.parent for path in (output / 'runs').glob('*/adapter/run.json')]
    assert len(evidence_directories) == 1, 'Expected exactly one adapter evidence directory'
    evidence = evidence_directories[0]
    run = evidence.parent
    report = json.loads((evidence / 'run.json').read_text(encoding='utf-8'))
    assert report['graph']['status'] == 'ready'
    assert {'server.js', 'client.ts', 'engine.py'} <= set(report['graph']['parser_inputs'])
    assert report['interface_selection']['candidate']['transport'] == 'http'
    assert report['status'] == 'verified'
    observations = [json.loads(line) for line in (evidence / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]
    verification = [row for row in observations if row['phase'] == 'verification']
    trials = [row for row in observations if row['phase'] == 'dataset']
    # Repairs retain earlier failed probes. The final accepted candidate must
    # pass its latest independent probe and all three formal trials.
    assert verification and verification[-1]['ok'], 'Final independent verification failed'
    assert len(trials) == 3 and all(row['ok'] for row in trials), 'Expected three successful formal trials'
    final = [verification[-1], *trials]
    assert all(row['adapter_sha256'] == report['adapter_sha256'] for row in final), 'Final adapter hash mismatch'
    for row in final:
        assert row['output'].startswith('API客服：')
        workspace = Path(row['workspace'])
        events = [json.loads(line)['event'] for line in (workspace / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
        assert {'server_started', 'http_received', 'initialized', 'knowledge_loaded', 'tool_called',
                'model_answered', 'http_postprocessed'} <= set(events), events
        state = json.loads((workspace / 'lladar-service.json').read_text(encoding='utf-8'))
        assert state['stopped']
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', state['port'])) != 0
    assert inventory(target) == before
    audit = json.loads((evidence / 'audit.json').read_text(encoding='utf-8'))
    assert any(item['tool'] == 'query_graph' for item in audit)
    (output / 'verification.json').write_text(json.dumps({'status': 'success', 'answers': count, 'verified_requests': len(final),
        'verification_attempts': len(verification),
        'failed_verification_attempts': sum(not row['ok'] for row in verification),
        'graph_version': report['graph']['version'], 'run': str(run)}, indent=2), encoding='utf-8')
    print(f'Paid REST and graph verification passed: {output}')


def collect_diagnostics(output):
    """Export only allowlisted metadata; runtime files can contain private data."""
    events = []
    allowed_errors = PROVIDER_CODES | {'model_error', 'target_execution_timeout'}
    for name in ('model-events.jsonl', 'trace.jsonl'):
        for path in output.rglob(name):
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    if name == 'trace.jsonl':
                        if not row.get('event', '').startswith('model_'):
                            continue
                        row = json.loads(row['detail'])
                except (ValueError, KeyError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get('event') not in {'start', 'end', 'error'}:
                    continue
                safe = {'event': row['event'], 'source': 'target' if name == 'trace.jsonl' else 'controller'}
                for key in ('elapsed_seconds', 'timeout_seconds', 'attempts', 'thinking_tokens'):
                    if type(row.get(key)) in (int, float) and math.isfinite(row[key]) and row[key] >= 0:
                        safe[key] = row[key]
                if row.get('error_code') in allowed_errors:
                    safe['error_code'] = row['error_code']
                if row.get('finish_reason') in {'STOP', 'MAX_TOKENS', 'SAFETY', 'stop', 'length', 'tool_calls'}:
                    safe['finish_reason'] = row['finish_reason']
                usage = row.get('usage')
                if isinstance(usage, dict):
                    safe['usage'] = {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
                                     if type(usage.get(key)) is int}
                events.append(safe)
    (output / 'diagnostics.json').write_text(json.dumps({'model_events': events}, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-python', required=True, type=Path)
    parser.add_argument('--env-file', type=Path, help='Optional dotenv file; defaults to process environment')
    parser.add_argument('--model', default=DEFAULT_ADAPTER_MODEL)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / '.lladar' / ('live-rest-graph-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True)
    summary = output / 'verification.json'
    summary.write_text(json.dumps({'status': 'running'}), encoding='utf-8')
    try:
        verify(args, output)
    except BaseException as error:
        # No arbitrary SDK exception messages, prompts, answers or credentials.
        code = provider_error_code(error)
        known = PROVIDER_CODES | {'target_' + item for item in PROVIDER_CODES} | {'target_execution_timeout'}
        if str(error) in known:
            code = str(error)
        summary.write_text(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                                       'error_code': code or 'acceptance_failed'}), encoding='utf-8')
        print(f'Live acceptance failed: {type(error).__name__} ({code or "acceptance_failed"})',
              file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    finally:
        collect_diagnostics(output)


if __name__ == '__main__':
    main()
