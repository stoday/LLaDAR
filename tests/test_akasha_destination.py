"""Actual client destination disclosure without a generation request."""
from types import SimpleNamespace

import pytest

from lladar.exceptions import ProviderError
from lladar.providers.akasha import disable_model_retries, resolve_model_destination


def test_custom_endpoint_is_resolved_from_sdk_not_model_provider_prefix():
    calls = []
    client = SimpleNamespace(
        model_name="configured-model",
        openai_api_base="https://unused.example/v1",
        root_client=SimpleNamespace(base_url="https://actual.example:8443/deployments/team/v1"),
    )
    def factory(model, **settings):
        calls.append((model, settings))
        return client
    result = resolve_model_destination("openai:configured-model", env_file="fixture.env", model_factory=factory)
    assert result["origin"] == "https://actual.example:8443"
    assert result["model"] == "configured-model"
    assert len(result["configuration_sha256"]) == 64
    assert calls[0][0] == "openai:configured-model"
    assert calls[0][1]["env_file"] == "fixture.env"
    assert calls[0][1]["verbose"] is False


def test_initializer_error_cannot_disclose_private_provider_configuration(capsys):
    def factory(*args, **kwargs):
        print("initializer-private-secret")
        raise ProviderError("initializer-private-secret")
    with pytest.raises(ProviderError) as caught:
        resolve_model_destination("fixture:model", model_factory=factory)
    assert str(caught.value) == "model_destination_unavailable"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("model,client,origin", [
    ("azure:deployment", SimpleNamespace(model_name="deployment", root_client=SimpleNamespace(
        base_url="https://tenant.openai.azure.com/openai/deployments/deployment?api-version=2025-01-01")),
     "https://tenant.openai.azure.com"),
    ("gemini:fixture", SimpleNamespace(model="fixture", client=SimpleNamespace(_api_client=SimpleNamespace(
        _http_options=SimpleNamespace(base_url="https://us-central1-aiplatform.googleapis.com/v1beta1")))),
     "https://us-central1-aiplatform.googleapis.com"),
    ("anthropic:fixture", SimpleNamespace(model="fixture", _client=SimpleNamespace(
        base_url="https://ambient-anthropic.example/v1")), "https://ambient-anthropic.example"),
    ("ollama:fixture", SimpleNamespace(model="fixture", _client=SimpleNamespace(_client=SimpleNamespace(
        base_url="http://localhost:11434"))), "http://localhost:11434"),
    ("remote:fixture", SimpleNamespace(model_name="served-model", model=SimpleNamespace(
        base_url="http://127.0.0.1:8081/v1")), "http://127.0.0.1:8081"),
])
def test_multiple_akasha_sdk_shapes_resolve_actual_transport_origin(model, client, origin):
    result = resolve_model_destination(model, model_factory=lambda *args, **kwargs: client)
    assert result["origin"] == origin


def test_configuration_fingerprint_tracks_actual_paths_and_models_without_persisting_secrets():
    client = SimpleNamespace(model_name="fixture", api_key="private-key-one",
                             root_client=SimpleNamespace(base_url="https://gateway.example/team-one/v1"))
    factory = lambda *args, **kwargs: client
    first = resolve_model_destination("openai:fixture", model_factory=factory)
    assert set(first) == {"origin", "model", "configuration_sha256"}
    assert "private-key" not in str(first)
    assert first == resolve_model_destination("openai:fixture", model_factory=factory)
    client.api_key = "private-key-two"
    assert first == resolve_model_destination("openai:fixture", model_factory=factory)
    client.root_client.base_url = "https://gateway.example/team-two/v1"
    changed_path = resolve_model_destination("openai:fixture", model_factory=factory)
    assert first["origin"] == changed_path["origin"]
    assert first["configuration_sha256"] != changed_path["configuration_sha256"]
    client.model_name = "different-model"
    assert changed_path["configuration_sha256"] != resolve_model_destination(
        "openai:fixture", model_factory=factory)["configuration_sha256"]


def test_vertex_project_configuration_change_is_detected_even_at_the_same_origin():
    api_client = SimpleNamespace(project="project-one", location="global",
        _http_options=SimpleNamespace(base_url="https://aiplatform.googleapis.com", api_version="v1"))
    client = SimpleNamespace(model="fixture", client=SimpleNamespace(_api_client=api_client))
    factory = lambda *args, **kwargs: client
    initial = resolve_model_destination("gemini:fixture", model_factory=factory)
    api_client.project = "project-two"
    changed = resolve_model_destination("gemini:fixture", model_factory=factory)
    assert initial["origin"] == changed["origin"]
    assert initial["configuration_sha256"] != changed["configuration_sha256"]


@pytest.mark.parametrize("url", [
    "https://username:private-key@api.example/v1", "https://username@api.example",
    "https://api.example\n.evil.example", "https://api.example\\@evil.example",
    "file:///private/provider-key", "https://api.example:99999/v1",
])
def test_credential_bearing_or_ambiguous_urls_are_refused_without_echoing_them(url):
    client = SimpleNamespace(root_client=SimpleNamespace(base_url=url))
    with pytest.raises(ProviderError, match="^model_destination_invalid$"):
        resolve_model_destination("fixture:model", model_factory=lambda *args, **kwargs: client)


def test_disagreeing_sdk_destinations_do_not_silently_select_one():
    client = SimpleNamespace(root_client=SimpleNamespace(base_url="https://first.example/v1"),
                             root_async_client=SimpleNamespace(base_url="https://second.example/v1"))
    with pytest.raises(ProviderError, match="^model_destination_ambiguous$"):
        resolve_model_destination("fixture:model", model_factory=lambda *args, **kwargs: client)


def test_unknown_client_never_gets_a_guessed_provider_url_or_offline_label():
    with pytest.raises(ProviderError, match="^model_destination_unavailable$"):
        resolve_model_destination("gemini:fixture", model_factory=lambda *args, **kwargs: object())


def test_known_akasha_local_model_is_explicitly_offline():
    class LocalModel:
        model_id = "local-weight-file"
        __module__ = "akasha.utils.models.llamacpp2"
    result = resolve_model_destination("llama-cpp:local-weight-file", model_factory=lambda *args, **kwargs: LocalModel())
    assert result["origin"] == "local/offline"
    assert result["model"] == "local-weight-file"


def test_client_representation_and_invocation_are_never_used():
    class SDK:
        base_url = "https://api.example/v1"
        def __str__(self):
            raise AssertionError("do not print private SDK")
        def invoke(self, *args, **kwargs):
            raise AssertionError("destination discovery must not generate")
    result = resolve_model_destination("fixture:model", model_factory=lambda *args, **kwargs: SDK())
    assert result["origin"] == "https://api.example"


def test_destination_discovery_does_not_construct_unused_lazy_async_client():
    from functools import cached_property
    constructed = []
    class Client:
        root_client = SimpleNamespace(base_url="https://api.example/v1")
        @cached_property
        def _async_client(self):
            constructed.append(True)
            return SimpleNamespace(base_url="https://api.example/v1")
    result = resolve_model_destination("fixture:model", model_factory=lambda *args, **kwargs: Client())
    assert result["origin"] == "https://api.example"
    assert constructed == []


@pytest.mark.parametrize("llm_type,retries", [("google_genai", 1), ("openai-chat", 0), ("anthropic-chat", 0)])
def test_retry_helper_accounts_for_google_total_attempts_and_nested_sdk_retries(llm_type, retries):
    client = SimpleNamespace(_llm_type=llm_type, max_retries=4,
                             root_client=SimpleNamespace(max_retries=7))
    client.root_client._client = client  # SDK reference cycles are bounded.
    disable_model_retries(client)
    assert client.max_retries == retries
    assert client.root_client.max_retries == 0


@pytest.mark.parametrize("model,configuration,ambient,origin", [
    ("openai:fixture", "OPENAI_API_KEY=synthetic-key\nOPENAI_BASE_URL=https://custom.example/v1\n", {},
     "https://custom.example"),
    ("azure:fixture", "AZURE_OPENAI_API_KEY=synthetic-key\nAZURE_OPENAI_BASE_URL=https://tenant.openai.azure.com/openai/v1\n", {},
     "https://tenant.openai.azure.com"),
    ("gemini:fixture", "GEMINI_API_KEY=synthetic-key\nGOOGLE_GENAI_USE_VERTEXAI=true\n", {},
     "https://aiplatform.googleapis.com"),
    ("anthropic:fixture", "ANTHROPIC_API_KEY=synthetic-key\n", {"ANTHROPIC_BASE_URL": "https://ambient.example/v1"},
     "https://ambient.example"),
    ("ollama:fixture", "OLLAMA_API_BASE=http://localhost:11434\n", {}, "http://localhost:11434"),
])
def test_installed_akasha_and_provider_sdk_initialize_without_any_network_or_generation(
    model, configuration, ambient, origin, tmp_path, monkeypatch
):
    import httpx
    import requests
    import socket
    from langchain_core.language_models.chat_models import BaseChatModel

    def forbidden(*args, **kwargs):
        raise AssertionError("destination discovery attempted network or generation")

    original_connect = socket.socket.connect
    def connect_guard(sock, address):
        # Windows asyncio builds a private self-pipe using a loopback socket.
        # Block service HTTP requests above the socket layer, while permitting
        # that interpreter infrastructure to initialize and clean up normally.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return forbidden()
    monkeypatch.setattr(socket.socket, "connect", connect_guard)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    monkeypatch.setattr(BaseChatModel, "invoke", forbidden)
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(configuration, encoding="utf-8")
    result = resolve_model_destination(model, env_file=str(env_file))
    assert result["origin"] == origin
    assert result["model"] == "fixture"
    assert "synthetic-key" not in str(result)


@pytest.mark.parametrize("changed_url", ["https://different.example/v1", "https://approved.example/other-tenant/v1"])
def test_extraction_checks_actual_initialized_client_before_invoking_it(monkeypatch, changed_url):
    from akasha.helper import handle_objects
    from lladar.providers.akasha import AkashaProvider

    invocations = []
    class Model:
        model_name = "fixture"
        root_client = SimpleNamespace(base_url="https://approved.example/v1")
        def invoke(self, *args, **kwargs):
            invocations.append(True)
            return object()
    client = Model()
    expected = resolve_model_destination("openai:fixture", max_output_tokens=65536,
        model_factory=lambda *args, **kwargs: client)
    client.root_client = SimpleNamespace(base_url=changed_url)
    monkeypatch.setattr(handle_objects, "handle_model", lambda *args, **kwargs: client)
    provider = AkashaProvider(max_output_tokens=65536, expected_destination=expected)
    with pytest.raises(ProviderError) as caught:
        provider.generate_message("private capture", system_prompt="extract", model="openai:fixture")
    assert caught.value.code == "model_destination_changed"
    assert invocations == []


def test_matching_actual_client_is_invoked_once_and_consent_identity_is_copied(monkeypatch):
    from akasha.helper import handle_objects
    from lladar.providers.akasha import AkashaProvider

    invocations = []
    response = object()
    class Model:
        model_name = "fixture"
        root_client = SimpleNamespace(base_url="https://approved.example/v1")
        def invoke(self, *args, **kwargs):
            invocations.append(True)
            return response
    client = Model()
    expected = resolve_model_destination("openai:fixture", max_output_tokens=65536,
        model_factory=lambda *args, **kwargs: client)
    provider = AkashaProvider(max_output_tokens=65536, expected_destination=expected)
    expected["origin"] = "https://mutated-consent.example"
    monkeypatch.setattr(handle_objects, "handle_model", lambda *args, **kwargs: client)
    assert provider.generate_message("private capture", system_prompt="extract", model="openai:fixture") is response
    assert invocations == [True]
