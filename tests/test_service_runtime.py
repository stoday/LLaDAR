"""Runner cleanup must finish before reporting a managed service stopped."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading

import pytest

from lladar import service_runtime


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signal-delivery regression')
def test_saved_service_cleanup_waits_for_socket_close(tmp_path, monkeypatch):
    process = subprocess.Popen(
        [sys.executable, '-u', '-c',
         "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); "
         "s.listen(); print(s.getsockname()[1],flush=True); time.sleep(60)"],
        start_new_session=True, stdout=subprocess.PIPE, text=True,
    )
    timer = None
    killpg = os.killpg
    try:
        port = int(process.stdout.readline())
        path = tmp_path / 'lladar-service.json'
        path.write_text(json.dumps(dict(pid=process.pid, port=port, stopped=False,
                                        request_id='current')))

        def delayed_signal(pid, sig):
            nonlocal timer
            assert pid == process.pid and sig == signal.SIGKILL
            # Model the gap between requesting termination and socket teardown.
            timer = threading.Timer(0.2, killpg, args=(pid, sig))
            timer.start()

        monkeypatch.setattr(service_runtime.os, 'killpg', delayed_signal)
        service_runtime.cleanup_saved_service(tmp_path, request_id='current')
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(('127.0.0.1', port)) != 0
        state = json.loads(path.read_text())
        assert state['stopped'] and state['cleanup'] == 'runner'
    finally:
        if timer is not None:
            timer.join()
        process.kill()
        process.wait(timeout=5)
        process.stdout.close()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signal-delivery regression')
def test_saved_service_cleanup_timeout_does_not_report_stopped(tmp_path, monkeypatch):
    # A listener that outlives the termination request must not be marked stopped.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        state = dict(pid=os.getpid(), port=listener.getsockname()[1], stopped=False,
                     request_id='current')
        path = tmp_path / 'lladar-service.json'
        path.write_text(json.dumps(state))
        monkeypatch.setattr(service_runtime.os, 'killpg', lambda pid, sig: None)
        with pytest.raises(TimeoutError, match='did not stop'):
            service_runtime.cleanup_saved_service(tmp_path, request_id='current', timeout=0.1)
        assert json.loads(path.read_text()) == state
