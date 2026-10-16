"""Real SSE/polling services and independently authored portable clients."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid


SSE_SOURCE = b'''import json, os, sys
from lladar_api_runtime import request
REQUIRED_ENV = ["EXAMPLE_API_KEY"]
r = json.load(sys.stdin)
parts, done = [], False
with request("chat", data=json.dumps({"question": r["message"], "client_id": r["request_id"]}).encode(),
             headers={"X-API-Key": os.environ["EXAMPLE_API_KEY"], "Content-Type": "application/json"}) as response:
    if response.status != 200:
        raise RuntimeError("Unexpected response status")
    for line in response:
        if not line.startswith(b"data: "):
            continue
        event = json.loads(line[6:])
        if event["client_id"] != r["request_id"]:
            raise RuntimeError("Uncorrelated SSE event")
        if "text" in event:
            parts.append(event["text"])
        if event.get("done") is True:
            done = True
            break
if not done or not parts:
    raise RuntimeError("SSE did not complete")
print(json.dumps({"request_id": r["request_id"], "output": "".join(parts), "observation": "Correlated SSE done event"}))
'''

POLL_SOURCE = b'''import json, os, sys, time
from urllib.parse import quote
from lladar_api_runtime import request
REQUIRED_ENV = ["EXAMPLE_API_KEY"]
r = json.load(sys.stdin)
headers = {"X-API-Key": os.environ["EXAMPLE_API_KEY"], "Content-Type": "application/json"}
with request("chat", data=json.dumps({"question": r["message"]}).encode(), headers=headers) as response:
    job = json.load(response)["job_id"]
answer = None
for _ in range(3):
    with request("jobs/" + quote(job, safe=""), method="GET", headers=headers) as response:
        result = json.load(response)
    if result["job_id"] != job:
        raise RuntimeError("Uncorrelated polling result")
    if result["status"] == "done":
        answer = result["answer"]
        break
    time.sleep(0.01)
if not isinstance(answer, str) or not answer.strip():
    raise RuntimeError("Polling did not complete within documented bound")
print(json.dumps({"request_id": r["request_id"], "output": answer, "observation": "Correlated completed job result"}))
'''

# A historical standalone CLI shape without a total response deadline.
LEGACY_SSE_SOURCE = b'''"""Standalone LLaDAR API adapter; Python 3.11+; standard library only."""
import argparse, io, json, os, sys, uuid
from contextlib import redirect_stdout
from urllib.request import Request, urlopen
REQUIRED_ENV = ["EXAMPLE_API_KEY"]
def _lladar_prepare(required):
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-url")
    parser.add_argument("--message")
    args = parser.parse_args()
    if args.service_url:
        os.environ["LLADAR_SERVICE_URL"] = args.service_url
    r = {"request_id": uuid.uuid4().hex, "message": args.message} if args.message is not None else json.load(sys.stdin)
    sys.stdin = io.StringIO(json.dumps(r))
    captured = io.StringIO()
    return r, captured
if __name__ == "__main__":
    try:
        incoming, captured = _lladar_prepare(REQUIRED_ENV)
        with redirect_stdout(captured):
            r = json.load(sys.stdin)
            req = Request(os.environ["LLADAR_SERVICE_URL"] + "/chat", method="POST",
                          data=json.dumps({"question":r["message"], "client_id":r["request_id"]}).encode(),
                          headers={"X-API-Key":os.environ["EXAMPLE_API_KEY"], "Content-Type":"application/json"})
            parts, done = [], False
            with urlopen(req, timeout=float(os.environ.get("LLADAR_API_TIMEOUT", "60"))) as response:
                for line in response:
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        if event["client_id"] != r["request_id"]:
                            raise RuntimeError("Uncorrelated")
                        if "text" in event:
                            parts.append(event["text"])
                        if event.get("done"):
                            done = True
                            break
            if not done:
                raise RuntimeError("Incomplete")
            print(json.dumps({"request_id":r["request_id"], "output":"".join(parts), "observation":"Completed SSE"}))
        print(captured.getvalue())
    except Exception:
        raise SystemExit(1)
'''


@contextmanager
def guide_service(mode, *, complete=True, correlated=True, interrupt_at=None, endless=False):
    calls, jobs = [], {}
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if self.path != "/chat" or self.headers.get("X-API-Key") != "recipient-private-key":
                self.reply(403, {"error": "denied"})
            elif mode == "sse":
                client = payload["client_id"] if correlated else "other-client"
                events = [{"client_id": client, "text": "reply: "}, {"client_id": client, "text": payload["question"]}]
                if complete:
                    events.append({"client_id": client, "done": True})
                data = ''.join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                if not endless:
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                if endless:
                    import time
                    while True:
                        try:
                            self.wfile.write(data)
                            time.sleep(0.02)
                        except (BrokenPipeError, ConnectionResetError):
                            break
            else:
                job = uuid.uuid4().hex
                jobs[job] = {"question": payload["question"], "polls": 0}
                self.reply(202, {"job_id": job})
            if interrupt_at is not None and len(calls) == interrupt_at:
                import _thread
                _thread.interrupt_main()

        def do_GET(self):
            job = self.path.removeprefix("/jobs/")
            state = jobs[job]
            state["polls"] += 1
            done = complete and state["polls"] > 1
            self.reply(200, {"job_id": job if correlated else "other-job", "status": "done" if done else "working",
                             "answer": "reply: " + state["question"] if done else "progress is not an answer"})

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
