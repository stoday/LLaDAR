"""One durable budget covers service traffic and protocol research."""
import pytest

pytestmark = pytest.mark.usefixtures("qualified_browser_demonstration")

from lladar.browser_target import BrowserDiscoveryError, DiscoveryLedger


def test_service_and_public_resource_requests_share_one_limit_and_survive_reload(tmp_path):
    ledger=DiscoveryLedger(tmp_path,deadline_seconds=60)
    ledger.phase='exploration'
    ledger.debit('research_requests',2,shared_kind='requests')
    ledger.phase='probe'
    ledger.debit('website_requests',2,shared_kind='requests')
    reloaded=DiscoveryLedger(tmp_path,deadline_seconds=60)
    reloaded.phase='formal'
    with pytest.raises(BrowserDiscoveryError,match='discovery_budget_exhausted'):
        reloaded.debit('website_requests',2,shared_kind='requests')
    assert reloaded.data['counts']=={'requests':2,'research_requests':1,'website_requests':1}
    assert reloaded.data['phase_counts']=={
        'exploration':{'requests':1,'research_requests':1},
        'probe':{'requests':1,'website_requests':1}}
    assert len(reloaded.data['events'])==2


def test_tampered_phase_usage_cannot_be_used_for_resume(tmp_path):
    import json
    ledger=DiscoveryLedger(tmp_path,deadline_seconds=60)
    ledger.debit('model_calls',10)
    payload=json.loads(ledger.path.read_text(encoding='utf-8'))
    payload['phase_counts']['exploration']['model_calls']=0
    ledger.path.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(BrowserDiscoveryError,match='checkpoint_integrity_failed'):
        DiscoveryLedger(tmp_path,deadline_seconds=60)


def test_a_failed_full_answer_reference_is_not_recorded_as_completed(tmp_path, monkeypatch):
    from test_browser_discovery_security import PROGRAM, SOURCE_ANSWER, target_for
    target,_site=target_for(tmp_path,PROGRAM.replace('SUBMIT_FLAG','True'),[])
    # Inspect the first failed reference before the separate recovery workflow.
    def stop_after_reference_failure(question, request_id, error):
        raise error
    monkeypatch.setattr(target, '_repair', stop_after_reference_failure)
    try:
        target.reference_reader=lambda turn: SOURCE_ANSWER if turn['role']!='formal' else 'A different complete source answer.'
        target.prepare([],interactive=False,request_count=1)
        with pytest.raises(BrowserDiscoveryError, match='reference_mismatch'):
            target.answer('A formal question requiring full source verification','unverified-formal')
        state=target.ledger.data['turns']['unverified-formal']
        assert state['status']=='accepted'
        assert state['extraction_complete'] is True
        assert not state.get('reference_method')
        assert target.evidence['status']=='blocked'
    finally:
        target.close()
