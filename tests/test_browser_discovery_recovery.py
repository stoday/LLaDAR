"""Recovery uses durable dispatch evidence, never a second remote answer."""
import json

import pytest

pytestmark = pytest.mark.usefixtures("qualified_browser_demonstration")

from lladar.browser_target import BrowserDiscoveryError, BrowserTarget
from test_browser_discovery_security import PROGRAM, SOURCE_ANSWER, Website, target_for


def test_repair_after_accepted_but_unacknowledged_send_collects_original_turn(tmp_path):
    class LostAcknowledgement(Website):
        def http_request(self,spec,*,submit=False):
            result=super().http_request(spec,submit=submit)
            if self.active=='formal-unknown':
                raise BrowserDiscoveryError('browser_action_failed_after_submission',request_attempted=True)
            return result
        def activate_turn(self,turn_id):
            self.active=turn_id
    calls=[]
    target,site=target_for(tmp_path,PROGRAM.replace('SUBMIT_FLAG','True'),calls,LostAcknowledgement)
    try:
        target.prepare([],interactive=False,request_count=1)
        assert target.answer('A new formal question','formal-unknown')==SOURCE_ANSWER
        sends=[e for e in site.events_seen if e['kind']=='http_request' and e['turn_id']=='formal-unknown']
        assert len(sends)==1
        assert target.ledger.data['counts']['repairs']==1
        assert target.candidate['revision']==2
        assert len(target.evidence['verification'])==2
        assert target.ledger.data['turns']['formal-unknown']['status']=='completed'
        transitions=[e['status'] for e in target.ledger.data['events'] if e.get('turn_id')=='formal-unknown']
        assert transitions==['not_attempted','attempted_unknown','accepted','accepted','completed']
    finally:
        target.close()


def test_corrupt_ledger_cannot_reset_usage_before_reload(tmp_path):
    target,_site=target_for(tmp_path,PROGRAM.replace('SUBMIT_FLAG','True'),[])
    target.prepare([],interactive=False,request_count=1)
    target.close()
    ledger_path=tmp_path/'ledger.json'
    ledger=json.loads(ledger_path.read_text(encoding='utf-8'))
    ledger['counts']={}
    ledger_path.write_text(json.dumps(ledger),encoding='utf-8')
    with pytest.raises(BrowserDiscoveryError,match='checkpoint_integrity_failed'):
        BrowserTarget(page_url='https://example.test/chat',driver=Website(tmp_path),workspace=tmp_path)


def test_completed_turn_is_never_replayed_on_duplicate_call(tmp_path):
    target,site=target_for(tmp_path,PROGRAM.replace('SUBMIT_FLAG','True'),[])
    try:
        target.prepare([],interactive=False,request_count=1)
        target.answer('A formal question','same-formal-id')
        before=len(site.submission_intents)
        with pytest.raises(BrowserDiscoveryError):
            target.answer('A formal question','same-formal-id')
        assert len(site.submission_intents)==before
    finally:
        target.close()


def test_repair_before_dispatch_rearms_the_same_unsubmitted_turn_without_duplicate_begin(tmp_path):
    from types import SimpleNamespace
    from lladar.answer_extraction import ExtractionOptions
    class StrictWebsite(Website):
        page=None
        def begin_turn(self,turn_id):
            if turn_id in self.turns:
                raise BrowserDiscoveryError('browser_turn_id_invalid')
            return super().begin_turn(turn_id)
        def rearm_unsubmitted_turn(self,turn_id,*,page=None):
            assert not any(event['kind']=='submission_attempted' for event in self.turns[turn_id])
            self.active=turn_id
            self.turns[turn_id]=[]
            return {'turn_id':turn_id,'sequence':len(self.events_seen),'page':self.inspect()}
    broken=PROGRAM.replace('SUBMIT_FLAG','True').replace('    if payload["phase"] == "send":',
        '    if payload["phase"] == "send" and payload["role"] == "formal":\n        return {"actions":[],"context":{}}\n    if payload["phase"] == "send":')
    starts=[]
    def factory(**settings):
        def agent(_):
            starts.append(True)
            tools=settings['tools']
            tools['submit_adapter'](broken if len(starts)==1 else PROGRAM.replace('SUBMIT_FLAG','True'),'Observed request')
            assert json.loads(tools['test_adapter']())['status']=='accepted'
        return agent
    website=StrictWebsite(tmp_path)
    target=BrowserTarget(page_url='https://example.test/chat',driver=website,workspace=tmp_path,
        agent_factory=factory,confirmed=True,timeout=10,
        extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=lambda **_:SimpleNamespace(
            extract=lambda request,**kwargs:{'result':{'status':'final','text':SOURCE_ANSWER},'usage':None})),
        reference_reader=lambda turn:SOURCE_ANSWER,output_fn=lambda _:None)
    try:
        target.prepare([],interactive=False,request_count=1)
        assert target.answer('A formal question after a pre-dispatch failure','formal-rearmed')==SOURCE_ANSWER
        assert len(starts)==2
        assert len([event for event in website.events_seen if event['kind']=='http_request' and event['turn_id']=='formal-rearmed'])==1
        assert target.ledger.data['turns']['formal-rearmed']['status']=='completed'
    finally:
        target.close()


@pytest.mark.browser
@pytest.mark.parametrize("conversation_policy",["existing","new_each"])
def test_real_chromium_target_repairs_unsubmitted_logical_turn_and_dispatches_question_once(tmp_path,conversation_policy):
    """Real driver rejects duplicate begin; recovery must use its rearm contract.

    Models are deterministic fixture stand-ins. The server-held answer oracle
    checks actual website responses independently of the generated programme.
    """
    from lladar.answer_extraction import ExtractionOptions
    from lladar.browser_discovery_driver import GenericBrowserDriver
    from test_browser_discovery_acceptance import acceptance_site, fixture_models, http_program
    with acceptance_site() as site:
        good=http_program(site.url,"json").replace(
            '        if context.get("scratch"):\n            actions.append({"kind":"close_page"})\n','')
        if conversation_policy=="existing":
            good=good.replace('    if phase == "setup":\n',
                '    if phase == "setup" and payload.get("role") == "formal":\n'
                '        return {"complete":True,"context":context}\n    if phase == "setup":\n')
        broken=good.replace('    if phase == "send":\n',
            '    if phase == "send" and payload["role"] == "formal":\n'
            '        return {"actions":[],"context":context}\n    if phase == "send":\n')
        starts=[]
        def factory(**settings):
            def agent(_):
                starts.append(True)
                tools=settings["tools"]
                tools["submit_adapter"](broken if len(starts)==1 else good,"Fixture supplied observed complete HTTP protocol")
                feedback=json.loads(tools["test_adapter"]())
                assert feedback["status"]=="accepted",feedback
            return agent
        _agent,provider,_extracted=fixture_models(good)
        driver=GenericBrowserDriver(page_url=site.url,profile_dir=tmp_path/"profile",headless=True,timeout=5)
        def reference_reader(turn):
            event=driver.raw_event(turn["evidence"]["submission_events"][0])
            return site.answers[json.loads(event["body"])["question"]]
        target=BrowserTarget(page_url=site.url,driver=driver,workspace=tmp_path/"workspace",timeout=15,
            agent_factory=factory,confirmed=True,conversation_policy=conversation_policy,
            extraction_options=ExtractionOptions(transfer_approved=True,provider_factory=provider),
            reference_reader=reference_reader,output_fn=lambda _:None)
        try:
            target.prepare([],interactive=False,record_count=1,request_count=1)
            original=driver.page
            formal="The formal question must reach the server exactly once."
            assert target.answer(formal,"real-formal-rearmed")==site.answers[formal]
            assert site.submitted.count(formal)==1 and len(starts)==2
            events=[e for e in driver.events() if e["turn_id"]=="real-formal-rearmed"]
            assert len([e for e in events if e["kind"]=="submission_attempted"])==1
            assert len([e for e in events if e["kind"]=="http_request" and e.get("method")=="POST"])==1
            history=[e["status"] for e in target.ledger.data["events"] if e.get("turn_id")=="real-formal-rearmed"]
            assert history[:2]==["not_attempted","not_attempted"]
            assert history.count("attempted_unknown")==1 and history[-1]=="completed"
            assert target.ledger.data["counts"]["repairs"]==1
            assert len(target.evidence["verification"])==2
            if conversation_policy=="existing":
                assert driver.page==original
            else:
                assert driver.page!=original
        finally:
            target.close()
