"""A zero-dependency public HTTP boundary for the LLaDAR documentation demo."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import answer_question


class SupportHandler(BaseHTTPRequestHandler):
    """Serve the demo Agent's one public workflow: POST /api/chat."""

    server_version = "SupportDemoAgent/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep the example quiet unless a user adds their own logging."""

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"ready": True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/api/chat":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            question = request["input"]["text"]
            answer = answer_question(question)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, {"data": {"answer": answer}})


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    """Create the demo service without starting its serving loop."""
    return ThreadingHTTPServer((host, port), SupportHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the support-demo-agent HTTP service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
