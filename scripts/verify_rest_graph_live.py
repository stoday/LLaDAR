"""Paid acceptance: Graphify + Node REST public boundary + real Python agent."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket

from lladar.runner import run_agent
from lladar.run_context import inventory
from verify_auto_adapter_live import dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-python', required=True, type=Path)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--model', default='gemini:gemini-3-flash-preview')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / '.lladar' / ('live-rest-graph-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir()
    target = root / 'tests/fixtures/rest_agent'
    before = inventory(target)
    data, answers = output / 'dataset.jsonl', output / 'answers.jsonl'
    data.write_text(json.dumps(dataset('langchain'), ensure_ascii=False) + '\n', encoding='utf-8')
    count = run_agent(data, answers, project=target, target_python=args.target_python,
                      env_file=args.env_file, model=args.model, runs_root=output / 'runs',
                      interactive=False, intent='測試原有 POST /api/chat 的完整 REST 流程，包括 Node 請求處理與最後的 API客服 前綴。已提供有 langchain 與 langchain-google-genai 的 target Python，GEMINI_API_KEY 由 env-file 注入。')
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
