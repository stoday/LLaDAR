from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from functools import cached_property
from inspect import getattr_static
import io
import json
import sys
from typing import Any
from urllib.parse import urlsplit

from ..exceptions import ProviderError
from ..model_profiles import resolve_model_profile
from .base import parse_json_object
from ..language import current_language
from ..live_status import status_activity


_CLIENT_CHILDREN = (
    "root_client", "client", "_client", "_api_client", "_http_options",
    "http_options", "_client_params", "model", "root_async_client", "async_client",
    "_async_client", "http_client", "_http_client", "transport", "_transport",
    "client_options", "_client_options",
)
_ENDPOINT_NAMES = (
    "base_url", "_base_url", "api_endpoint", "azure_endpoint",
    "openai_api_base", "anthropic_api_url", "api_base", "endpoint", "url",
)
_ROUTE_SETTINGS = (
    "project", "location", "api_version", "deployment_name", "azure_deployment", "organization",
)
_LOCAL_CLIENT_MODULES = {
    "akasha.utils.models.hf", "akasha.utils.models.llamacpp2",
    "akasha.utils.models.chglm", "akasha.utils.models.gtq",
}


class _DestinationResolutionError(ProviderError):
    pass


class ModelDestinationError(ProviderError):
    """Closed, safe failures at the pre-invocation destination gate."""
    def __init__(self, code: str):
        self.code = code if code in {
            "model_destination_changed", "model_destination_invalid", "model_destination_unavailable"
        } else "model_destination_unavailable"
        super().__init__(self.code)


def copy_model_destination(expected: dict[str, str]) -> dict[str, str]:
    """Validate and privately copy the consented, non-secret routing identity."""
    if (type(expected) is not dict or set(expected) != {"origin", "model", "configuration_sha256"}
            or any(type(value) is not str for value in expected.values())
            or not expected["model"].strip()
            or len(expected["configuration_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in expected["configuration_sha256"])):
        raise ModelDestinationError("model_destination_invalid")
    try:
        if expected["origin"] != "local/offline" and _url_origin(expected["origin"]) != expected["origin"]:
            raise ValueError
    except (ProviderError, ValueError):
        raise ModelDestinationError("model_destination_invalid") from None
    return dict(expected)


def _client_attribute(value: Any, name: str) -> Any:
    try:
        return value.get(name) if type(value) is dict else getattr(value, name, None)
    except Exception:
        return None


def _client_nodes(client: Any):
    """Inspect only known SDK attributes, without walking arbitrary objects."""
    queue = [(client, 0, "model")]
    seen = set()
    while queue and len(seen) < 64:
        node, depth, edge = queue.pop(0)
        if node is None or id(node) in seen:
            continue
        if isinstance(node, (str, bytes, int, float, bool)):
            continue
        seen.add(id(node))
        yield node, depth, edge
        if depth < 6:
            for name in _CLIENT_CHILDREN:
                if name in {"root_async_client", "async_client", "_async_client"}:
                    # A destination check must not initialize an unused async
                    # transport merely to inspect a synchronous model route.
                    try:
                        descriptor = getattr_static(node, name)
                    except AttributeError:
                        descriptor = None
                    if isinstance(descriptor, (property, cached_property)):
                        continue
                child = _client_attribute(node, name)
                if child is not None:
                    queue.append((child, depth + 1, name))


def _endpoint_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    # SDK URL objects are safe to render; arbitrary client repr/str may contain
    # API keys and must never be used for destination discovery.
    kind = type(value)
    if kind.__module__ in {"httpx", "httpx._urls", "pydantic.networks", "yarl", "yarl._url"}:
        if kind.__name__ in {"URL", "HttpUrl", "AnyUrl", "AnyHttpUrl"}:
            return str(value)
    return None


def _url_origin(url: str) -> str:
    try:
        if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if "%" in host:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        host = "[" + host + "]" if ":" in host else host
        scheme = parsed.scheme.lower()
        suffix = "" if port is None or port == {"http": 80, "https": 443}[scheme] else ":" + str(port)
        return scheme + "://" + host + suffix
    except (ValueError, UnicodeError):
        raise _DestinationResolutionError("model_destination_invalid") from None


def resolve_model_destination(
    model: str,
    env_file: str | None = ".env",
    max_output_tokens: int | None = None,
    *,
    model_factory: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """Disclose the initialized Akasha client's destination without invoking it.

    Actual SDK endpoints take priority over declarative ChatModel fields, so
    custom endpoints and ambient SDK configuration are disclosed accurately.
    Only the origin, effective model name and a digest of non-secret routing
    configuration leave this function. No credential/client representation is
    inspected or persisted. Unsupported clients fail instead of inventing a
    provider URL or labelling an unknown destination as local.
    """
    if type(model) is not str or not model.strip():
        raise ProviderError("model_destination_invalid")
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if model_factory is None:
                from akasha.helper.handle_objects import handle_model
                model_factory = handle_model
            profile = resolve_model_profile(model, max_output_tokens=max_output_tokens)
            client = model_factory(model, env_file=env_file or "", verbose=False,
                                   temperature=0.0,
                                   max_output_tokens=profile.max_output_tokens)
            endpoints, routing_settings = [], set()
            for node, depth, edge in _client_nodes(client):
                for name in _ROUTE_SETTINGS:
                    value = _client_attribute(node, name)
                    if type(value) in (str, int, bool) and value not in ("", None):
                        routing_settings.add(json.dumps([edge, name, value], separators=(",", ":")))
                for name in _ENDPOINT_NAMES:
                    text = _endpoint_text(_client_attribute(node, name))
                    # Empty/relative HTTP-client bases do not carry a route.
                    if not text or text == "/":
                        continue
                    origin = _url_origin(text)
                    score = 3 if edge in {"_http_options", "http_options"} else 2 if depth else 1
                    endpoints.append((score, origin, text))
            if endpoints:
                best = max(item[0] for item in endpoints)
                routes = [item for item in endpoints if item[0] == best]
                origins = {item[1] for item in routes}
                if len(origins) != 1:
                    raise _DestinationResolutionError("model_destination_ambiguous")
                origin = origins.pop()
                routing_urls = sorted({item[2] for item in routes})
            elif type(client).__module__ in _LOCAL_CLIENT_MODULES:
                origin, routing_urls = "local/offline", []
            else:
                raise _DestinationResolutionError("model_destination_unavailable")
            resolved_model = next(
                (value for name in ("model_name", "model", "model_id", "deployment_name")
                 if isinstance(value := _client_attribute(client, name), str) and value),
                model,
            )
            configuration = {
                "origin": origin, "routing_urls": routing_urls,
                "routing_settings": sorted(routing_settings),
                "model": resolved_model, "requested_model": model,
                "max_output_tokens": profile.max_output_tokens,
                "client_type": type(client).__module__ + "." + type(client).__name__,
            }
            fingerprint = sha256(json.dumps(configuration, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    except _DestinationResolutionError:
        raise
    except Exception:
        raise ProviderError("model_destination_unavailable") from None
    return {"origin": origin, "model": resolved_model,
            "configuration_sha256": fingerprint}


def disable_model_retries(client: Any) -> None:
    """Disable model/SDK retries; Google max_retries is a total-attempt count."""
    llm_type = _client_attribute(client, "_llm_type")
    retries = 1 if isinstance(llm_type, str) and "google" in llm_type.lower() else 0
    for node, depth, _edge in _client_nodes(client):
        try:
            if hasattr(node, "max_retries"):
                node.max_retries = retries if depth == 0 else 0
        except Exception:
            raise ProviderError("model_retry_configuration_failed") from None


def verify_model_destination(
    client: Any,
    *,
    model: str,
    expected_destination: dict[str, str],
    max_output_tokens: int | None = None,
) -> None:
    """Check the final initialized client before any provider invocation.

    Reuse that actual client rather than initializing another one from .env.
    This catches both origin changes and same-origin deployment/path changes.
    Failure contains no endpoint, model response, environment or credential.
    """
    expected = copy_model_destination(expected_destination)
    try:
        actual = resolve_model_destination(model, max_output_tokens=max_output_tokens,
            model_factory=lambda *args, **kwargs: client)
    except ProviderError:
        raise ModelDestinationError("model_destination_unavailable") from None
    if actual != expected:
        raise ModelDestinationError("model_destination_changed")


class AkashaProvider:
    def __init__(
        self,
        *,
        env_file: str = ".env",
        agent_factory: Callable[..., Any] | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        verbose: bool = False,
        stream: bool = False,
        expected_destination: dict[str, str] | None = None,
        stream_observer: Callable[[str, str], None] | None = None,
    ) -> None:
        self.env_file = env_file
        self._agent_factory = agent_factory
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.verbose = verbose
        self.stream = stream
        self.stream_observer = stream_observer
        self.expected_destination = (
            copy_model_destination(expected_destination) if expected_destination is not None else None
        )

    @status_activity("Waiting for model response")
    def generate_message(self, prompt: str, *, system_prompt: str, model: str,
                         temperature: float = 0.0):
        """One tool-free Akasha model invocation, retaining completion metadata.

        Unlike agent generation, extraction must not run tools, normalize the
        website's language, or retry an empty answer through call_model().
        Provider selection and credentials remain Akasha's responsibility.
        """
        from akasha.helper.handle_objects import handle_model
        from langchain_core.messages import HumanMessage, SystemMessage

        profile = resolve_model_profile(model, max_input_tokens=self.max_input_tokens,
                                        max_output_tokens=self.max_output_tokens)
        client = handle_model(model, env_file=self.env_file, verbose=False,
                              temperature=temperature,
                              max_output_tokens=profile.max_output_tokens)
        if self.expected_destination is not None:
            verify_model_destination(client, model=model,
                expected_destination=self.expected_destination,
                max_output_tokens=profile.max_output_tokens)
        # Google expresses this as total attempts; other SDKs count retries.
        retries = 1 if 'google' in str(getattr(client, '_llm_type', '')).lower() else 0
        if hasattr(client, 'max_retries'):
            client.max_retries = retries
        for name in ('client', '_client', 'root_client', 'model'):
            sdk = getattr(client, name, None)
            if hasattr(sdk, 'max_retries'):
                sdk.max_retries = 0
        try:
            with redirect_stdout(sys.stderr):
                return client.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)],
                                     config={'callbacks': []})
        except ProviderError:
            raise
        except Exception as error:
            # Keep provider-specific SDK exceptions, including HTTP 429 rate
            # limits, behind LLaDAR's safe provider boundary. The CLI can then
            # report a concise failure instead of exposing an SDK traceback.
            raise ProviderError("Akasha generation failed") from error

    def generate_structured(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> dict[str, Any]:
        return parse_json_object(
            self.generate_text(prompt, model=model, temperature=temperature)
        )

    @status_activity("Waiting for model response")
    def generate_text(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        system_prompt: str | None = None,
        thinking: bool = False,
    ) -> str:
        factory = self._agent_factory
        if factory is None:
            import akasha

            factory = akasha.agents
        profile = resolve_model_profile(
            model,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
        )
        agent = factory(
            system_prompt=system_prompt or current_language().instruction(),
            model=model,
            env_file=self.env_file,
            stream=self.stream,
            thinking=thinking,
            max_input_tokens=profile.max_input_tokens,
            max_output_tokens=profile.max_output_tokens,
            temperature=temperature,
            verbose=self.verbose,
            keep_logs=False,
        )
        try:
            # Akasha emits its visible trace to stdout. Keep provider trace
            # separate from the CLI's structured data and returned answer.
            with redirect_stdout(sys.stderr):
                response = agent(prompt)
                if self.stream and not isinstance(response, str):
                    parts = []
                    for event in response:
                        if isinstance(event, str):
                            if self.stream_observer is not None:
                                self.stream_observer("answer", event)
                            parts.append(event)
                        elif isinstance(event, dict):
                            if (self.stream_observer is not None
                                    and event.get("type") in {"thinking", "answer"}
                                    and isinstance(event.get("data"), str)):
                                self.stream_observer(event["type"], event["data"])
                            if event.get("type") == "answer":
                                chunk = event.get("data")
                                if not isinstance(chunk, str):
                                    raise ProviderError("Akasha answer chunks must be text")
                                parts.append(chunk)
                            elif event.get("type") == "error":
                                raise ProviderError("Akasha generation stream failed")
                        else:
                            raise ProviderError("Unsupported Akasha stream event")
                    response = "".join(parts)
            if not isinstance(response, str):
                raise ProviderError("Akasha response must be text")
            if not response.strip():
                raise ProviderError("Akasha response was empty")
            return response
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError("Akasha generation failed") from error
