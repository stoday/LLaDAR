"""HTTP helpers inlined into standalone portable API adapters (stdlib only)."""
from __future__ import annotations

import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def service_url() -> str:
    value = os.environ.get("LLADAR_SERVICE_URL", "").rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Set LLADAR_SERVICE_URL to an HTTP(S) base URL without credentials, query or fragment")
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path: str, *, method: str = "POST", data: bytes | None = None,
            headers: dict[str, str] | None = None, timeout: float | None = None):
    """Open a relative API path; callers parse JSON/SSE and own the response context.

    Authentication headers come from the generated adapter, following the project
    contract. Redirects are rejected so credentials cannot follow another URL.
    """
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or "\\" in path:
        raise ValueError("API path must be relative to LLADAR_SERVICE_URL")
    seconds = float(os.environ.get("LLADAR_API_TIMEOUT", "60")) if timeout is None else timeout
    if not 0 < seconds < float("inf"):
        raise ValueError("API timeout must be finite and positive")
    req = Request(service_url() + "/" + path.lstrip("/"), data=data,
                  headers=headers or {}, method=method)
    try:
        return build_opener(_NoRedirect()).open(req, timeout=seconds)
    except HTTPError as error:
        # Do not echo a response body, headers, credentials or a private URL.
        status = error.code
        error.close()
        if status in {401, 403}:
            raise RuntimeError(f"API HTTP {status}: check your credentials and server permissions") from None
        raise RuntimeError(f"API HTTP {status}") from None
    except URLError:
        raise RuntimeError("API connection failed; check service address and network access") from None
