import json
from pathlib import Path

import pytest

from lladar import work_session


@pytest.mark.skipif(work_session.os.name != 'nt', reason='Windows file sharing boundary')
def test_transient_windows_file_lock_preserves_old_checkpoint_until_atomic_replace(tmp_path, monkeypatch):
    path = tmp_path / 'session.json'
    path.write_text('{"old": true}', encoding='utf-8')
    replace = work_session.os.replace
    attempts = []

    def temporarily_locked(source, destination):
        attempts.append(Path(source))
        if len(attempts) == 1:
            assert json.loads(path.read_text(encoding='utf-8')) == {'old': True}
            error = PermissionError('fixture file lock')
            error.winerror = 5
            raise error
        replace(source, destination)

    monkeypatch.setattr(work_session.os, 'replace', temporarily_locked)
    work_session.atomic_json(path, {'new': True})
    assert json.loads(path.read_text(encoding='utf-8')) == {'new': True}
    assert attempts[0] == attempts[1]
    assert list(tmp_path.glob('.checkpoint-*')) == []


@pytest.mark.skipif(work_session.os.name != 'nt', reason='Windows file sharing boundary')
def test_permanent_windows_file_lock_stops_at_deadline_and_preserves_old_data(tmp_path, monkeypatch):
    path = tmp_path / 'session.json'
    path.write_text('{"old": true}', encoding='utf-8')
    attempts = []

    def locked(*args):
        attempts.append(args)
        error = PermissionError('fixture permanent lock')
        error.winerror = 5
        raise error

    monkeypatch.setattr(work_session.os, 'replace', locked)
    monkeypatch.setattr(work_session.time, 'monotonic', lambda: 100 if attempts else 0)
    with pytest.raises(PermissionError):
        work_session.atomic_json(path, {'new': True})
    assert len(attempts) == 1
    assert json.loads(path.read_text(encoding='utf-8')) == {'old': True}
    assert list(tmp_path.glob('.checkpoint-*')) == []
