"""Opt-in autonomous discovery against the existing native Streamlit chat page.

The dry run only submits synthetic text through the normal UI and records native
binary WebSocket receipts. The paid mode uses the public BrowserTarget with real
Akasha exploration and extraction. Neither mode imports the backend, adds HTTP
routes, supplies a protocol decoder, nor supplies an initial adapter.
"""
from __future__ import annotations

import argparse
import base64
from functools import partial
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
import traceback
from urllib.parse import urlsplit


class AcceptanceFailure(AssertionError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _local_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'http' or parts.hostname not in {'127.0.0.1', 'localhost'} or parts.username or parts.password:
        raise ValueError('This harness only accepts a local HTTP test page')
    return url


def _prepare_output(root, *, resume, paid):
    if not resume:
        root.mkdir(parents=True, exist_ok=False)
        return {}
    if not paid:
        raise ValueError('Resume is only available for an explicitly authorized paid attempt')
    ledger_path = root / 'initial' / 'ledger.json'
    candidate_path = root / 'initial' / 'candidate.json'
    report_path = root / 'acceptance.json'
    if not all(path.is_file() for path in (ledger_path, candidate_path, report_path)):
        raise ValueError('Resume requires an existing failed unqualified discovery workspace')
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    previous = json.loads(report_path.read_text(encoding='utf-8'))
    if previous.get('status') not in {'failed', 'interrupted'} or ledger.get('approved') is not True:
        raise ValueError('Resume requires a previously approved failed discovery attempt')
    if any(turn.get('status') != 'not_attempted' for turn in ledger.get('turns', {}).values()):
        raise ValueError('This harness only resumes unqualified candidates with no prior website submissions')
    index = len(list(root.glob('acceptance-attempt-*.json'))) + 1
    archive = root / ('acceptance-attempt-' + str(index) + '.json')
    archive.write_text(report_path.read_text(encoding='utf-8'), encoding='utf-8')
    return {'counts': dict(ledger['counts']), 'started_at': ledger['started_at'],
            'previous_report': archive.name, 'previous_reason': previous.get('reason')}


def _binary_receipt(driver, turn_id):
    """Inspect native host receipts without interpreting or exporting payloads."""
    events = [driver.raw_event(event['event_id']) for event in driver.observe_turn(turn_id)['events']]
    sent = [event for event in events if event['kind'] == 'websocket_sent'
            and event.get('observation_source') == 'browser_network' and event.get('binary_b64')]
    received = [event for event in events if event['kind'] == 'websocket_received'
                and event.get('observation_source') == 'browser_network' and event.get('binary_b64')]
    links = {event['connection_id'] for event in sent} & {event['connection_id'] for event in received}
    if not links:
        raise AssertionError('The submitted turn has no native binary WebSocket send/receive pair')
    if any(event['kind'] == 'websocket_close' and event.get('connection_id') in links for event in events):
        raise AssertionError('The question connection closed during a native persistent turn')
    return {'binary_sent': len(sent), 'binary_received': len(received), 'connection_ids': sorted(links)}


def _safe_receipt(receipt):
    return {key: value for key, value in receipt.items() if key != 'connection_ids'}


def _assert_preserved(driver, page, questions):
    if driver.page is not page:
        raise AssertionError('The existing formal conversation page was replaced')
    # This is only an independent test assertion; these selectors are never
    # supplied to the discovery Agent or stored as its integration strategy.
    displayed = [text.strip() for text in driver.page.locator('[data-testid="stChatMessage"]')
                 .locator('[data-testid="stMarkdownContainer"]').all_inner_texts()]
    for question in questions:
        if question not in displayed or 'Answer: ' + question not in displayed:
            raise AssertionError('The formal native conversation lost a previous question or complete answer')


def _dry_run(driver):
    driver.open()
    driver.page.locator('textarea').wait_for(state='visible')
    page = driver.page
    questions, receipts = [], []
    for number in (1, 2):
        question = 'Native transport check ' + str(number) + ' ' + secrets.token_hex(5)
        turn_id = 'dry-' + str(number)
        driver.begin_turn(turn_id)
        snapshot = driver.inspect()
        editable = [node for frame in snapshot['frames'] if frame['page_index'] == snapshot['active_page_index']
                    for node in frame['nodes'] if node['tag'] == 'textarea' and node['editable'] and not node['private']]
        if len(editable) != 1:
            raise AssertionError('The native sample must expose exactly one chat text control')
        ref = editable[0]['ref']
        driver.action({'kind': 'fill', 'ref': ref, 'value': question})
        driver.action({'kind': 'press', 'ref': ref, 'key': 'Enter'}, submit=True)
        driver.page.get_by_text('Answer: ' + question, exact=True).wait_for()
        receipts.append(_binary_receipt(driver, turn_id))
        questions.append(question)
        _assert_preserved(driver, page, questions)
    if not (set(receipts[0]['connection_ids']) & set(receipts[1]['connection_ids'])):
        raise AssertionError('The native questions used different WebSocket connections')
    return {'status': 'passed', 'ui_answers_exact': len(questions), 'formal_page_preserved': True,
            'formal_history_preserved': True, 'persistent_connection_reused': True,
            'turns': [_safe_receipt(item) for item in receipts],
            'model_calls': 0, 'binary_decoding_proven': False}


def verify_streamlit(*, page_url, output_root, paid=False, model='gemini:gemini-3.8-flash',
                     env_file='.env', timeout=600, reuse=True, resume=False):
    from lladar.browser_discovery_driver import GenericBrowserDriver

    page_url = _local_url(page_url)
    root = Path(output_root).resolve()
    baseline = _prepare_output(root, resume=resume, paid=paid)
    report = {'status': 'running', 'entry': 'target' if paid else 'dry-run', 'service': 'native Streamlit',
              'browser': 'headless local Chromium', 'page_url': page_url,
              'preseeded_adapter': False, 'preseeded_codec': False, 'backend_imported': False,
              'extra_rest_routes': False, 'real_remote_site_acceptance': False,
              'binary_hash_alone_proves_semantics': False, 'resume': resume}
    if resume:
        report['resume_baseline'] = baseline
    started = time.monotonic()
    driver, target = None, None
    try:
        if not paid:
            driver = GenericBrowserDriver(page_url=page_url, profile_dir=root / 'profile', headless=True, timeout=timeout)
            report.update(_dry_run(driver))
        else:
            from lladar.answer_extraction import ExtractionOptions
            from lladar.browser_target import BrowserTarget
            from lladar.extraction_provider import AkashaExtractionProvider

            class AuditedTarget(BrowserTarget):
                def _validate_evidence(self, result, observation, baseline, question):
                    super()._validate_evidence(result, observation, baseline, question)
                    if self.evidence.get('receive_method') != 'network':
                        return
                    materials = result.get('materials', [])
                    if not materials:
                        raise AcceptanceFailure('native_decoded_material_missing')
                    source_count = 0
                    for material in materials:
                        decoded = material.get('data')
                        if isinstance(decoded, str):
                            try:
                                decoded = json.loads(decoded)
                            except (ValueError, TypeError):
                                pass
                        if material.get('frame_id') or not isinstance(decoded, (dict, list)):
                            raise AcceptanceFailure('native_whole_binary_envelope_required')
                        references = material.get('event_ids', [])
                        if not references:
                            raise AcceptanceFailure('native_binary_source_reference_missing')
                        for reference in references:
                            source = self.driver.raw_event(reference)
                            if (source['kind'] != 'websocket_received'
                                or source.get('observation_source') != 'browser_network'
                                or not source.get('binary_b64')):
                                raise AcceptanceFailure('native_canonical_binary_source_required')
                            # Raw provenance was checked by the superclass using
                            # its private receipt. Candidate hashes identify the
                            # exact safe wire view issued to the interpreter.
                            view = self._protocol_authority(reference)
                            digest = hashlib.sha256(base64.b64decode(view['binary_b64'], validate=True)).hexdigest()
                            if material.get('source_hashes', {}).get(reference) != digest:
                                raise AcceptanceFailure('native_binary_source_hash_mismatch')
                            source_count += 1
                    self.native_material_proofs = getattr(self, 'native_material_proofs', []) + [{
                        'turn_id': observation['turn_id'], 'decoded_envelopes': len(materials),
                        'canonical_binary_sources': source_count, 'source_hashes_exact': True,
                        'original_wire_host_verified': True}]

            def new_target(workspace, adapter_path=None):
                nonlocal driver, target
                driver = GenericBrowserDriver(page_url=page_url, profile_dir=workspace / 'profile',
                                               headless=True, timeout=timeout)
                options = ExtractionOptions(model_destination=model, transfer_approved=True,
                    provider_factory=partial(AkashaExtractionProvider, env_file=env_file))
                target = AuditedTarget(page_url=page_url, driver=driver, workspace=workspace,
                    env_file=env_file, agent_factory=None, confirmed=True, extraction_options=options,
                    adapter_path=adapter_path, conversation_policy='existing', timeout=timeout,
                    verbose=True, input_fn=lambda _: '',
                    reference_reader=lambda turn: 'Answer: ' + target._current_question)
                if resume and workspace == root / 'initial':
                    if target.ledger.data['started_at'] != baseline['started_at'] or target.ledger.data['counts'] != baseline['counts']:
                        raise AcceptanceFailure('resume_ledger_reset')
                return target

            def formal_questions(prefix, count):
                page, questions, receipts = driver.page, [], []
                prompts = ['第一題：請保留繁體中文與標點。', '第二題：不是零，是 17。',
                           '第三題：引號「測試」與冒號：都要保留。',
                           '第四題：此回應只依據目前頁面服務。', '第五題：同一段對話中的最後一題。']
                for number in range(1, count + 1):
                    question = prompts[number - 1] + ' ' + secrets.token_hex(5)
                    request_id = prefix + '-' + str(number)
                    if target.answer(question, request_id) != 'Answer: ' + question:
                        raise AssertionError('The extracted native answer differs from the host-only reference')
                    receipts.append(_binary_receipt(driver, request_id))
                    questions.append(question)
                    _assert_preserved(driver, page, questions)
                if count > 1 and not (set(receipts[0]['connection_ids']) & set(receipts[1]['connection_ids'])):
                    raise AssertionError('Formal questions did not preserve their native conversation connection')
                return [_safe_receipt(item) for item in receipts]

            def phase_metrics():
                counts = dict(target.ledger.data['counts'])
                if len(target.evidence['verification']) < 1:
                    raise AssertionError('The independent native verification round did not complete')
                network = target.evidence.get('receive_method') == 'network'
                if not network:
                    raise AcceptanceFailure('native_network_receive_required')
                return {'ledger': counts, 'verification_rounds': len(target.evidence['verification']),
                        'adapter_sha256': target.evidence['adapter_sha256'],
                        'send_method': target.evidence.get('send_method'),
                        'receive_method': target.evidence.get('receive_method'),
                        'binary_decoding_proven': network,
                        'native_material_proofs': getattr(target, 'native_material_proofs', []),
                        'non_extraction_model_calls': counts.get('model_calls', 0) - counts.get('extraction_calls', 0)}

            new_target(root / 'initial').prepare([], interactive=False, record_count=5, request_count=5)
            phase_metrics()
            report['initial'] = {'formal_answers_exact': 5, 'formal_turns': formal_questions('Native formal', 5),
                                 'formal_page_preserved': True, 'formal_history_preserved': True,
                                 'persistent_connection_reused': True, **phase_metrics()}
            if resume:
                report['initial']['attempt_ledger_delta'] = {
                    key: value - baseline['counts'].get(key, 0)
                    for key, value in report['initial']['ledger'].items()}
            adapter_path = target.workspace / 'adapter.json'
            adapter_hash = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
            target.close()
            if reuse:
                new_target(root / 'reused', adapter_path).prepare([], interactive=False, record_count=1, request_count=1)
                phase_metrics()
                report['reuse'] = {'formal_answers_exact': 1, 'formal_turns': formal_questions('Native reload', 1),
                                   **phase_metrics()}
                if hashlib.sha256(adapter_path.read_bytes()).hexdigest() != adapter_hash:
                    raise AssertionError('Cold validation modified the original generated adapter')
                if report['reuse']['adapter_sha256'] != report['initial']['adapter_sha256']:
                    raise AssertionError('Cold validation did not use the same generated strategy')
            report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', reason=getattr(error, 'reason', 'acceptance_failed'), error_type=type(error).__name__)
        report['source_frames'] = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                                   for frame in traceback.extract_tb(error.__traceback__)]
        raise
    finally:
        if target is not None:
            report['last_ledger'] = dict(target.ledger.data.get('counts', {}))
            if resume and target.workspace == root / 'initial':
                report['attempt_ledger_delta'] = {key: value - baseline['counts'].get(key, 0)
                    for key, value in report['last_ledger'].items()}
                report['global_ledger_started_at_preserved'] = target.ledger.data['started_at'] == baseline['started_at']
            target.close()
        elif driver is not None:
            driver.close()
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (root / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page-url', default='http://127.0.0.1:8517')
    parser.add_argument('--paid', action='store_true', help='Explicitly authorize real Akasha discovery and extraction calls')
    parser.add_argument('--model', default='gemini:gemini-3.8-flash')
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--no-reuse', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Continue the same failed unqualified workspace and cumulative ledger; requires --paid and its existing --output-root')
    parser.add_argument('--output-root', type=Path, default=Path('.tmp') / ('streamlit-discovery-' + secrets.token_hex(6)))
    args = parser.parse_args(argv)
    print('Paid Akasha calls enabled.' if args.paid else 'Dry run: native UI/WebSocket checks only; no model calls.', file=sys.stderr, flush=True)
    try:
        result = verify_streamlit(page_url=args.page_url, output_root=args.output_root, paid=args.paid,
            model=args.model, env_file=args.env_file, timeout=args.timeout, reuse=not args.no_reuse, resume=args.resume)
    except Exception:
        print('Acceptance failed; inspect the private-safe acceptance.json report.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
