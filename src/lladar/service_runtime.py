"""Standalone stdlib helper copied beside generated adapters (no LLaDAR import)."""
from __future__ import annotations

from contextlib import contextmanager
import os
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError


def stop_tree(process):
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait(timeout=5)
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def cleanup_saved_service(workspace: Path, request_id: str | None = None, *,
                          timeout: float = 5):
    """Parent-runner fallback when adapter timeout prevents its finally block."""
    path = workspace / 'lladar-service.json'
    if not path.is_file():
        return
    state = json.loads(path.read_text(encoding='utf-8'))
    # A copied project may contain an old service record. Never act on its PID.
    if request_id is not None and state.get('request_id') != request_id:
        return
    if state.get('stopped'):
        return
    pid = state['pid']
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError('Invalid service PID')
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=10, check=False)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if os.name != 'nt':
        # Sending a signal does not wait for the kernel to close the listener.
        # The service may be an orphan, so the runner cannot use Popen.wait().
        deadline = time.monotonic() + timeout
        while True:
            try:
                with socket.create_connection(('127.0.0.1', state['port']), timeout=0.1):
                    pass
            except ConnectionRefusedError:
                break
            except TimeoutError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Service on port {state['port']} did not stop")
            time.sleep(0.05)
    state['stopped'] = True
    state['cleanup'] = 'runner'
    path.write_text(json.dumps(state), encoding='utf-8')


@contextmanager
def managed_service(command: list[str], *, readiness_path: str, timeout: float = 30,
                    expected_status: int = 200):
    """Run the original server on localhost; substitute {python}, {port}, {host}."""
    if not command or not readiness_path.startswith("/") or readiness_path.startswith("//"):
        raise ValueError("Require command argv and local readiness path")
    if not any("{port}" in arg for arg in command):
        raise ValueError("Managed service command must accept an isolated {port}")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    argv = [arg.replace("{python}", sys.executable).replace("{port}", str(port))
            .replace("{host}", "127.0.0.1") for arg in command]
    base_url = f"http://127.0.0.1:{port}"
    with Path("lladar-service.log").open("ab") as log:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        state = {'pid': process.pid, 'port': port, 'stopped': False,
                 'request_id': os.environ.get('LLADAR_REQUEST_ID')}
        state_path = Path('lladar-service.json')
        try:
            state_path.write_text(json.dumps(state), encoding='utf-8')
            deadline = time.monotonic() + timeout
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Service exited {process.returncode}; see lladar-service.log")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Service readiness timed out; see lladar-service.log")
                try:
                    with urlopen(base_url + readiness_path, timeout=min(1, max(.01, deadline-time.monotonic()))) as response:
                        if response.status == expected_status:
                            break
                except (URLError, TimeoutError, OSError):
                    pass
                time.sleep(.1)
            yield base_url
        finally:
            stop_tree(process)
            state['stopped'] = True
            state_path.write_text(json.dumps(state), encoding='utf-8')
