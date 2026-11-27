"""A recording pass-through between the agents and the local model server.

Every request an agent sends to the model server, and the server's reply (streamed or not), is
appended to the current log file as one JSON line. The harness points the proxy at a new file for
each run (`POST /_proxy/log` with `{"path": ...}`), so each run's model traffic is kept apart and
the turns, tool calls and replies of any agent can be read back the same way.

Nothing is changed on the way through: the body, the headers that matter and the streamed chunks
are passed as they are. Standard library only.

    python scripts/agent_vs_knaif/model_proxy.py --listen 127.0.0.1:11436 --upstream 127.0.0.1:11435
"""

from __future__ import annotations

import argparse
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host"}


def control_allowed(client_ip: str) -> bool:
    """Only this machine may switch the log or the turn limit; the sandbox reaches the proxy over
    its virtual network and is refused."""
    return client_ip in ("127.0.0.1", "::1")


#: The model calls a turn limit counts (Ollama's own API and the OpenAI-compatible ones).
CHAT_PATHS = ("/api/chat", "/api/generate", "/v1/chat/completions", "/v1/responses")


class _State:
    def __init__(self, upstream: tuple[str, int]):
        self.upstream = upstream
        self.log_path: Path | None = None
        self.max_requests: int | None = None
        self.chat_requests = 0
        self.lock = threading.Lock()

    def admit(self, path: str) -> bool:
        """Count a model request against the run's turn limit; False once it is used up."""
        if not path.startswith(CHAT_PATHS):
            return True
        with self.lock:
            self.chat_requests += 1
            return self.max_requests is None or self.chat_requests <= self.max_requests

    def write(self, record: dict) -> None:
        with self.lock:
            if self.log_path is None:
                return
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet: the JSONL file is the record
            pass

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _control(self, body: bytes) -> None:
            cfg = json.loads(body or b"{}")
            path = cfg.get("path")
            with state.lock:
                state.log_path = Path(path) if path else None
                state.max_requests = cfg.get("max_requests")
                state.chat_requests = 0
            data = json.dumps({"log": path, "max_requests": state.max_requests}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, body: bytes, t0: float) -> None:
            """The run's turn limit is used up: answer 429 without asking the model."""
            data = json.dumps({"error": "turn limit reached for this run"}).encode()
            try:
                req = json.loads(body) if body else None
            except ValueError:
                req = body.decode("utf-8", "replace")
            # Record first, answer second: a client that has its reply finds the record there.
            state.write(
                {
                    "t": round(t0, 3),
                    "dur_s": 0.0,
                    "method": self.command,
                    "path": self.path,
                    "status": 429,
                    "request": req,
                    "response": data.decode(),
                }
            )
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _forward(self) -> None:
            body = self._body()
            if self.path == "/_proxy/log":
                if not control_allowed(self.client_address[0]):
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._control(body)
                return
            t0 = time.time()
            if not state.admit(self.path):
                self._refuse(body, t0)
                return
            conn = http.client.HTTPConnection(*state.upstream, timeout=900)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            conn.request(self.command, self.path, body=body or None, headers=headers)
            resp = conn.getresponse()
            if self.command == "HEAD" or resp.status in (204, 304):
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in _HOP:
                        self.send_header(k, v)
                self.end_headers()
                conn.close()
                return
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunks = []
            while True:
                piece = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not piece:
                    break
                chunks.append(piece)
                self.wfile.write(f"{len(piece):X}\r\n".encode() + piece + b"\r\n")
                self.wfile.flush()
            conn.close()
            if self.command == "POST":
                try:
                    req = json.loads(body) if body else None
                except ValueError:
                    req = body.decode("utf-8", "replace")
                # Before the final chunk: the client cannot finish reading the reply until the
                # record is written, so a harness reading the log right after an agent exits
                # (or a test right after a call) always finds it.
                state.write(
                    {
                        "t": round(t0, 3),
                        "dur_s": round(time.time() - t0, 3),
                        "method": self.command,
                        "path": self.path,
                        "status": resp.status,
                        "request": req,
                        "response": b"".join(chunks).decode("utf-8", "replace"),
                    }
                )
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        do_GET = do_POST = do_HEAD = do_DELETE = _forward

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--listen",
        action="append",
        default=None,
        help="host:port, repeatable: 127.0.0.1 for the harness, the sandbox network's host address "
        "for the agents (default 127.0.0.1:11436)",
    )
    ap.add_argument("--upstream", default="127.0.0.1:11435")
    ap.add_argument(
        "--log", default=None, help="initial log file (the harness switches it per run)"
    )
    a = ap.parse_args()
    uhost, uport = a.upstream.rsplit(":", 1)
    state = _State((uhost, int(uport)))
    if a.log:
        state.log_path = Path(a.log)
    servers = []
    for addr in a.listen or ["127.0.0.1:11436"]:
        host, port = addr.rsplit(":", 1)
        servers.append(ThreadingHTTPServer((host, int(port)), make_handler(state)))
        print(f"proxy {addr} -> {a.upstream}", flush=True)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
