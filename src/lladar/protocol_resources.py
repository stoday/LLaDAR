"""Bounded public protocol documents and pinned package assets, never executed."""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
from urllib.parse import urljoin, urlsplit
import zipfile

from .resource_limits import resource_limit
from .work_session import atomic_json


class ProtocolResourceError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _public_bytes(url, *, debit, byte_limit):
    """Pin a public address for each TLS connection; no cookies, proxies or auth."""
    for _ in range(resource_limit('GITHUB_REDIRECTS') + 1):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ProtocolResourceError('protocol_resource_url_invalid')
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ProtocolResourceError('protocol_resource_private_address')
            destination = addresses[0][4][0]
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                timeout=resource_limit('GITHUB_TIMEOUT_SECONDS'), context=ssl.create_default_context())
            connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection(
                (destination, address[1]), timeout, source_address)
            debit()
            connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''),
                headers={'Accept-Encoding': 'identity', 'User-Agent': 'LLaDAR protocol research'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                connection.close()
                if not location:
                    raise ProtocolResourceError('protocol_resource_redirect_invalid')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ProtocolResourceError('protocol_resource_unavailable')
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise ProtocolResourceError('protocol_resource_encoding_unsupported')
            length = response.getheader('Content-Length')
            if length is not None and int(length) > byte_limit:
                raise ProtocolResourceError('protocol_resource_limit')
            body = response.read(byte_limit + 1)
            if len(body) > byte_limit:
                raise ProtocolResourceError('protocol_resource_limit')
            return body, response.getheader('Content-Type', ''), url
        except ProtocolResourceError:
            raise
        except Exception:
            raise ProtocolResourceError('protocol_resource_unavailable') from None
        finally:
            if 'connection' in locals():
                connection.close()
    raise ProtocolResourceError('protocol_resource_redirect_limit')


class ProtocolResources:
    """Adapter-local source assets, with immutable versions and hashes.

    Wheels are data only: no install script, package import, arbitrary shell or
    global environment modification. The Agent can read schemas/source and
    generate pure VM code from them. Native or CPython dependencies are refused.
    """
    def __init__(self, workspace, *, debit):
        self.workspace = Path(workspace)
        self.debit = debit
        self.manifest = {}

    def public_document(self, url, *, offset=0, limit=None):
        body, media, actual = _public_bytes(url, debit=self.debit,
            byte_limit=resource_limit('PROJECT_READ_BYTES'))
        try:
            text = body.decode('utf-8')
        except UnicodeError:
            raise ProtocolResourceError('protocol_resource_not_text') from None
        return self._page(text, offset, limit, url=actual, content_type=media,
                          sha256=hashlib.sha256(body).hexdigest())

    def package_asset(self, package, version, path='', *, offset=0, limit=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', package) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', version):
            raise ProtocolResourceError('protocol_dependency_invalid')
        key = package.lower()+'=='+version
        root = self.workspace/hashlib.sha256(key.encode()).hexdigest()
        archive = root/'package.whl'
        if key not in self.manifest:
            metadata, _, _ = _public_bytes(f'https://pypi.org/pypi/{package}/{version}/json',
                debit=self.debit, byte_limit=resource_limit('PROJECT_READ_BYTES'))
            try:
                choices = [entry for entry in json.loads(metadata)['urls'] if entry['packagetype']=='bdist_wheel']
                # Schema/source assets may live in a platform wheel; none of its
                # executable binaries are loaded or installed.
                entry = sorted(choices, key=lambda e: (not e['filename'].endswith('py3-none-any.whl'), e['filename']))[0]
                body, _, _ = _public_bytes(entry['url'], debit=self.debit,
                    byte_limit=resource_limit('PROTOCOL_DEPENDENCY_BYTES'))
                checksum = hashlib.sha256(body).hexdigest()
                if checksum != entry['digests']['sha256']:
                    raise ProtocolResourceError('protocol_dependency_integrity_failed')
                with zipfile.ZipFile(io.BytesIO(body)) as bundle:
                    files = bundle.infolist()
                    if len(files)>resource_limit('PROJECT_LIST_FILES') or sum(f.file_size for f in files)>resource_limit('PROTOCOL_DEPENDENCY_BYTES'):
                        raise ProtocolResourceError('protocol_dependency_limit')
                root.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(body)
                self.manifest[key] = {'package':package,'version':version,'filename':entry['filename'],
                    'sha256':checksum,'capability':'source_assets_only','files':[f.filename for f in files]}
                atomic_json(self.workspace/'dependencies.json', self.manifest)
            except ProtocolResourceError:
                raise
            except Exception:
                raise ProtocolResourceError('protocol_dependency_unavailable') from None
        record = self.manifest[key]
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=record['sha256']:
            raise ProtocolResourceError('protocol_dependency_integrity_failed')
        if not path:
            return {**record,'files':record['files'][offset:offset+(limit or resource_limit('PROJECT_PREVIEW_CHARS'))]}
        try:
            with zipfile.ZipFile(archive) as bundle:
                item = bundle.getinfo(path)
                if item.file_size>resource_limit('PROJECT_READ_BYTES'):
                    raise ProtocolResourceError('protocol_resource_limit')
                body = bundle.read(item)
                text = body.decode('utf-8')
        except ProtocolResourceError:
            raise
        except Exception:
            raise ProtocolResourceError('protocol_package_asset_unavailable') from None
        return self._page(text, offset, limit, package=key, path=path,
                          sha256=hashlib.sha256(body).hexdigest())

    @staticmethod
    def _page(text, offset, limit, **metadata):
        count = limit or resource_limit('PROJECT_PREVIEW_CHARS')
        if type(offset) is not int or offset<0 or type(count) is not int or count<1:
            raise ProtocolResourceError('protocol_resource_page_invalid')
        return {**metadata,'text':text[offset:offset+count],'total_chars':len(text),
                'next_offset':offset+count if offset+count<len(text) else None}
