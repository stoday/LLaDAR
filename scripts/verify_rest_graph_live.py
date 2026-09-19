"""Paid acceptance: Graphify + Node REST public boundary + real Python agent."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket

from lladar.runner import run_agent
from lladar.interfaces import NeedsConfirmation
from lladar.run_context import inventory
from verify_auto_adapter_live import dataset
from live_acceptance_runtime import runtime_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-python', required=True, type=Path)
    parser.add_argument('--env-file', type=Path, help='Optional dotenv file; defaults to process environment')
    parser.add_argument('--model', default='gemini:gemini-3-flash-preview')
    args = parser.parse_args()
    runtime = runtime_evidence(args.target_python)
    root = Path(__file__).resolve().parents[1]
    output = root / '.lladar' / ('live-rest-graph-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True)
    target = root / 'tests/fixtures/rest_agent'
    before = inventory(target)
    data, answers = output / 'dataset.jsonl', output / 'answers.jsonl'
    data.write_text(json.dumps(dataset('langchain'), ensure_ascii=False) + '\n', encoding='utf-8')
    try:
        count = run_agent(data, answers, project=target, target_python=args.target_python,
                          env_file=args.env_file, model=args.model, runs_root=output / 'runs',
                          interactive=False, intent='測試原有 POST /api/chat 的完整 REST 流程，包括 Node 請求處理與最後的 API客服 前綴。已提供有 langchain 與 langchain-google-genai 的 target Python，GEMINI_API_KEY 已透過執行環境提供。 ' + runtime)
    except NeedsConfirmation as paused:
        plan = json.loads((paused.run / 'adapter/interfaces.json').read_text(encoding='utf-8'))
        # This fixture explicitly tests its documented REST API, so select only
        # that complete public contract. Never guess among routes or waive gaps.
        candidates = [candidate for candidate in plan['candidates']
                      if candidate['public_boundary'] and candidate.get('transport') == 'http'
                      and candidate['service']['missing'] == []
                      and 'POST' in candidate['service']['request']
                      and '/api/chat' in candidate['service']['request']
                      and candidate['service']['readiness_path'] == '/health'
                      and 'server.js' in candidate['service']['command']
                      and 'node' in candidate['service']['command']]
        assert len(candidates) == 1, 'Expected exactly one complete public POST /api/chat candidate'
        assert not answers.exists(), 'A paused discovery must not execute the target'
        count = run_agent(data, answers, project=target, target_python=args.target_python,
                          env_file=args.env_file, model=args.model, resume_run=paused.run,
                          candidate_id=candidates[0]['id'], interactive=False, intent=runtime)
    assert count == 3
    run = next((output / 'runs').iterdir())
    evidence = run / 'adapter'
    report = json.loads((evidence / 'run.json').read_text(encoding='utf-8'))
    assert report['graph']['status'] == 'ready'
    assert {'server.js', 'client.ts', 'engine.py'} <= set(report['graph']['parser_inputs'])
    assert report['interface_selection']['candidate']['transport'] == 'http'
    assert report['status'] == 'verified'
    observations = [json.loads(line) for line in (evidence / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]
    final = [row for row in observations if row['phase'] in ('verification', 'dataset')]
    assert len(final) == 5 and all(row['ok'] for row in final)
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
    (output / 'verification.json').write_text(json.dumps({'answers': count, 'verified_requests': len(final),
        'graph_version': report['graph']['version'], 'run': str(run)}, indent=2), encoding='utf-8')
    print(f'Paid REST and graph verification passed: {output}')


if __name__ == '__main__':
    main()
