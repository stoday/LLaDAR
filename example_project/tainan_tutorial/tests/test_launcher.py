import contextlib
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT = PROJECT_ROOT / "start-demo.ps1"
POWERSHELL = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")


def run_launcher(port: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )


@pytest.mark.skipif(not POWERSHELL.is_file(), reason="Windows PowerShell 5.1 only")
def test_launcher_is_utf8_bom_for_windows_powershell_51():
    assert SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(not POWERSHELL.is_file(), reason="Windows PowerShell 5.1 only")
def test_launcher_treats_an_existing_demo_service_as_success():
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/health":
                self.send_error(404)
                return
            body = json.dumps({"service": "tainan-bias-demo", "status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_launcher(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0
    assert "展示頁已在執行" in result.stdout
    assert str(server.server_port) in result.stdout
    assert "ERROR" not in result.stderr


@pytest.mark.skipif(not POWERSHELL.is_file(), reason="Windows PowerShell 5.1 only")
def test_launcher_rejects_an_unknown_port_owner_with_a_clear_error():
    with contextlib.closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = run_launcher(port)

    assert result.returncode == 2
    assert f"連接埠 {port} 已被其他程序占用" in result.stderr
