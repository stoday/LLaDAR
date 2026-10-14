"""Protocol research stays public, pinned and inert through its public seam."""
import hashlib
import io
import json
import zipfile

import pytest

from lladar.protocol_resources import ProtocolResources, ProtocolResourceError


def test_public_document_refuses_local_addresses_before_any_request(tmp_path):
    requests=[]
    resources=ProtocolResources(tmp_path,debit=lambda:requests.append(True))
    with pytest.raises(ProtocolResourceError,match='private_address'):
        resources.public_document('https://127.0.0.1/private')
    assert requests==[]


@pytest.mark.parametrize('url',['http://example.test/doc','https://user:password@example.test/doc','file:///private'])
def test_public_document_refuses_credentials_or_non_https_urls(tmp_path,url):
    resources=ProtocolResources(tmp_path,debit=lambda:pytest.fail('must not send'))
    with pytest.raises(ProtocolResourceError,match='url_invalid'):
        resources.public_document(url)


def package_fixture(monkeypatch, *, correct_hash=True):
    from lladar import protocol_resources
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,'w') as bundle:
        bundle.writestr('package/schema.proto','message 證據 { string paragraph = 7; }')
        bundle.writestr('package/__init__.py','raise RuntimeError("must never execute")')
    wheel=memory.getvalue()
    checksum=hashlib.sha256(wheel).hexdigest() if correct_hash else '0'*64
    metadata=json.dumps({'urls':[{'packagetype':'bdist_wheel','filename':'package-2.0-py3-none-any.whl',
        'url':'https://files.pythonhosted.org/package.whl','digests':{'sha256':checksum}}]}).encode()
    calls=[]
    def fetch(url,*,debit,byte_limit):
        debit()
        calls.append(url)
        return (metadata if url.endswith('/json') else wheel),'application/octet-stream',url
    monkeypatch.setattr(protocol_resources,'_public_bytes',fetch)
    return calls


def test_package_schema_is_locked_and_read_without_import_or_install(tmp_path,monkeypatch):
    calls=package_fixture(monkeypatch)
    debits=[]
    resources=ProtocolResources(tmp_path,debit=lambda:debits.append(True))
    listing=resources.package_asset('package','2.0')
    assert listing['capability']=='source_assets_only'
    assert 'package/__init__.py' in listing['files']
    asset=resources.package_asset('package','2.0','package/schema.proto',limit=9)
    assert asset['next_offset']==9
    tail=resources.package_asset('package','2.0','package/schema.proto',offset=9)
    assert asset['text']+tail['text']=='message 證據 { string paragraph = 7; }'
    assert len(calls)==len(debits)==2
    manifest=json.loads((tmp_path/'dependencies.json').read_text(encoding='utf-8'))
    assert manifest['package==2.0']['sha256']==listing['sha256']
    assert not (tmp_path/'package').exists()


def test_package_digest_mismatch_cannot_publish_dependency(tmp_path,monkeypatch):
    package_fixture(monkeypatch,correct_hash=False)
    resources=ProtocolResources(tmp_path,debit=lambda:None)
    with pytest.raises(ProtocolResourceError,match='integrity_failed'):
        resources.package_asset('package','2.0')
    assert not (tmp_path/'dependencies.json').exists()


def test_tampered_local_dependency_cannot_be_reused(tmp_path,monkeypatch):
    package_fixture(monkeypatch)
    resources=ProtocolResources(tmp_path,debit=lambda:None)
    resources.package_asset('package','2.0')
    next(tmp_path.rglob('package.whl')).write_bytes(b'altered')
    with pytest.raises(ProtocolResourceError,match='integrity_failed'):
        resources.package_asset('package','2.0','package/schema.proto')
