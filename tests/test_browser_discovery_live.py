"""Real Akasha wiring and explicitly opted-in random local website acceptance."""
import os
from pathlib import Path

import pytest


def _harness():
    import importlib.util
    path = Path(__file__).parents[1] / 'scripts' / 'verify_browser_discovery_live.py'
    spec = importlib.util.spec_from_file_location('browser_discovery_live_harness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paid_harness_cli_requires_explicit_opt_in(monkeypatch):
    harness = _harness()
    monkeypatch.setattr(harness, 'verify_live', lambda **_: pytest.fail('Unapproved model call'))
    with pytest.raises(SystemExit) as error:
        harness.main([])
    assert error.value.code == 2


def test_actual_akasha_async_graph_honors_model_callbacks_before_generation():
    import akasha
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    calls = []
    class Budget(BaseCallbackHandler):
        raise_error = True
        def on_chat_model_start(self, *args, **kwargs):
            calls.append('budget')
            raise RuntimeError('budget_denied_before_model')

    class Client(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
        async def _agenerate(self, *args, **kwargs):
            calls.append('generation')
            return await super()._agenerate(*args, **kwargs)
    client = Client(responses=[AIMessage(content='must not generate')])
    def inspect_fixture() -> str:
        return 'Local fixture metadata'
    agent = akasha.agents(model=client, tools=[akasha.create_tool('Inspect fixture', inspect_fixture, 'inspect_fixture')],
                          stream=False, thinking=False, verbose=False, keep_logs=False)
    # Same timing as the production browser broker: attach after graph creation.
    client.callbacks = [Budget()]
    with pytest.raises(RuntimeError, match='budget_denied_before_model'):
        agent('Local callback wiring probe; no provider request')
    assert calls == ['budget']


def test_actual_akasha_tool_rounds_each_trigger_model_budget_callback():
    import akasha
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    calls = []
    class Budget(BaseCallbackHandler):
        raise_error = True
        def on_chat_model_start(self, *args, **kwargs):
            calls.append('budget')
    class Client(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
        async def _agenerate(self, *args, **kwargs):
            calls.append('generation')
            return await super()._agenerate(*args, **kwargs)
    def inspect_fixture() -> str:
        calls.append('tool')
        return 'Local fixture metadata'
    client = Client(responses=[AIMessage(content='', tool_calls=[{
        'name': 'inspect_fixture', 'args': {}, 'id': 'local-call', 'type': 'tool_call'}]), AIMessage(content='Done')])
    agent = akasha.agents(model=client, tools=[akasha.create_tool('Inspect fixture', inspect_fixture, 'inspect_fixture')],
                          stream=False, thinking=False, verbose=False, keep_logs=False)
    client.callbacks = [Budget()]
    assert agent('Inspect the local fixture') == 'Done'
    assert calls == ['budget', 'generation', 'tool', 'budget', 'generation']


@pytest.mark.browser
@pytest.mark.parametrize('mode', ['json', 'sse'])
def test_randomized_acceptance_fixture_is_a_working_normal_webpage(tmp_path, mode):
    from lladar.browser_discovery_driver import GenericBrowserDriver
    with _harness().randomized_site(mode) as site:
        driver = GenericBrowserDriver(page_url=site['url'], profile_dir=tmp_path / 'profile', headless=True, timeout=5)
        try:
            driver.open()
            question = 'A new local fixture question'
            driver.page.get_by_label('Question', exact=True).fill(question)
            driver.page.get_by_role('button', name='Send question', exact=True).click()
            driver.page.wait_for_function("() => document.querySelector('pre').textContent.includes('終段：完整傳送。')")
            assert driver.page.locator('pre').inner_text() == site['answers'][question]
            assert len(site['answer_history'][question]) == 1
            assert site['endpoint'] in driver.client_resource(site['url'] + site['client_resource'])['text']
        finally:
            driver.close()


@pytest.mark.browser
@pytest.mark.live_gemini
@pytest.mark.parametrize('mode', ['json', 'sse'])
def test_real_akasha_discovers_randomized_local_service_without_preseeded_adapter(tmp_path, mode):
    if os.environ.get('LLADAR_LIVE_BROWSER_DISCOVERY') != '1':
        pytest.skip('Set LLADAR_LIVE_BROWSER_DISCOVERY=1 to permit real model calls')
    result = _harness().verify_live(mode=mode, output_root=tmp_path / 'live',
                              model=os.environ.get('LLADAR_BROWSER_DISCOVERY_MODEL', 'gemini:gemini-3.8-flash'),
                              env_file=os.environ.get('LLADAR_BROWSER_DISCOVERY_ENV_FILE', '.env'),
                              timeout=600, reuse=True, entry='target')
    assert result['status'] == 'passed'
    assert result['preseeded_adapter'] is False
    assert result['formal_answers_exact'] == 2
    assert result['independent_verification_rounds'] >= 3
    assert result['reuse']['fresh_verification_rounds'] >= 3
    assert result['non_extraction_model_calls'] > 0
