"""Origin-scoped HTTP Basic credentials owned only by the running host process."""
from __future__ import annotations

import base64
import getpass
import re
import sys
import warnings
from urllib.parse import urlsplit

from .language import tr
from .live_status import read_user_input


class BasicAuthChallenge(RuntimeError):
    def __init__(self):
        super().__init__('browser_authentication_required')


def auth_origin(url):
    parsed = urlsplit(url)
    scheme = {'ws': 'http', 'wss': 'https'}.get(parsed.scheme, parsed.scheme)
    if scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('browser_url_invalid')
    host = parsed.hostname
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    suffix = '' if port is None or port == (443 if scheme == 'https' else 80) else ':' + str(port)
    return f'{scheme}://{host}{suffix}'


class BrowserHttpAuth:
    """Never serialize this object or place it in a model-visible plan/state."""

    def __init__(self, *, input_fn=None, password_fn=None, output_fn=None):
        self.input_fn = input_fn
        self.password_fn = password_fn
        self.output_fn = output_fn or (lambda message: print(message, file=sys.stderr, flush=True))
        self._credentials = {}
        self._private = set()

    def prompt(self, url):
        site = auth_origin(url)
        self.output_fn(tr('This website requires HTTP Basic authentication: ') + site)
        self.output_fn(tr('Credentials stay in memory for this run. They are not saved or sent to the model. Ctrl+C cancels.'))
        username = read_user_input(tr('Website username: '), input_fn=self.input_fn)
        # Refuse getpass's echoing fallback on terminals without secure input.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                password = read_user_input(tr('Website password (hidden): '),
                    input_fn=self.password_fn or getpass.getpass)
            except getpass.GetPassWarning:
                raise BasicAuthChallenge() from None
        self._private.update(value for value in (username, password) if value)
        if not username or ':' in username:
            raise BasicAuthChallenge()
        token = base64.b64encode(f'{username}:{password}'.encode()).decode()
        self._private.update((token, 'Basic ' + token))
        self._credentials[site] = {'origin': site, 'username': username, 'password': password}

    def launch_options(self, url):
        credentials = self._credentials.get(auth_origin(url))
        return {'http_credentials': dict(credentials)} if credentials else {}

    def authorization(self, url):
        credentials = self._credentials.get(auth_origin(url))
        if credentials is None:
            return None
        token = base64.b64encode((credentials['username'] + ':' + credentials['password']).encode()).decode()
        return 'Basic ' + token

    def required_origins(self):
        return sorted(self._credentials)

    def restore(self, sites, allowed_origins):
        """Only the requirement is persisted; every new process asks again."""
        allowed = {auth_origin(url) for url in allowed_origins}
        for site in sites:
            if auth_origin(site) not in allowed:
                raise ValueError('browser_origin_not_authorized')
            if self.authorization(site) is None:
                self.prompt(site)

    def navigate(self, page, url, **options):
        """Identify a real Basic challenge; do not prompt for every 401 or network error."""
        response = None
        try:
            response = page.goto(url, **options)
        except Exception as error:
            if 'net::ERR_INVALID_AUTH_CREDENTIALS' not in str(error):
                raise
            # Chromium can reject the navigation before emitting a response.
            # Probe only this initial document; never follow an unknown redirect.
            try:
                response = page.context.request.get(url, max_redirects=0,
                    timeout=options.get('timeout', 30000))
                challenged = response.status == 401 and bool(re.search(
                    r'(?:^|,)\s*Basic\b', response.headers.get('www-authenticate', ''), re.I))
                response.dispose()
            except Exception:
                raise error from None
            if challenged:
                raise BasicAuthChallenge() from None
            raise error from None
        if response is not None and response.status == 401 and re.search(
                r'(?:^|,)\s*Basic\b', response.headers.get('www-authenticate', ''), re.I):
            raise BasicAuthChallenge()
        return response

    def redact(self, value):
        """Remove entrance credentials before any durable or model-visible capture."""
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if str(key).lower() == 'authorization' and isinstance(item, str) and item.lower().startswith('basic '):
                    continue
                if key == 'binary_b64' and isinstance(item, str):
                    try:
                        raw = base64.b64decode(item, validate=True)
                        for secret in sorted(self._private, key=len, reverse=True):
                            raw = raw.replace(secret.encode(), b'[host-http-basic-auth]')
                        item = base64.b64encode(raw).decode()
                    except ValueError:
                        pass
                result[self.redact(key)] = self.redact(item)
            return result
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(self._private, key=len, reverse=True):
                value = value.replace(secret, '[host-http-basic-auth]')
        return value

    def clear(self):
        self._credentials.clear()
        self._private.clear()
