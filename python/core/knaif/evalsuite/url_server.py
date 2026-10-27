"""A local HTTP server for URL-input tests and evals: files by path, plus misbehaving routes.

Serves the files it is given at the URL paths it is given (an eval maps
``https://files.knaif.test/ffmpeg/holiday.mov`` onto ``/ffmpeg/holiday.mov`` here, through
``KNAIF_NET_TEST_HOST``), and a few routes that misbehave on purpose, under ``/_/``:

- ``/_/redirect/<n>/<path>``: ``n`` redirects, then ``/<path>``;
- ``/_/redirect-to?location=<url>``: one redirect to any Location (another scheme, credentials);
- ``/_/big/<bytes>``: that many bytes of octet-stream, ``?nolen=1`` without a Content-Length;
- ``/_/slow/<seconds>``: answers only after that long;
- ``/_/html``: a web page;
- ``/_/status/<code>``: that status.

``?type=<content type>`` overrides any file's Content-Type. Every request is recorded, so a
test can say that none reached the server. Bound to 127.0.0.1 on a free port; use it as a
context manager.
"""

from __future__ import annotations

import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


class UrlServer:
    def __init__(self, files: dict[str, Path] | None = None) -> None:
        self.files = {("/" + k.lstrip("/")): Path(v) for k, v in (files or {}).items()}
        self.requests: list[dict[str, Any]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base(self) -> str:
        assert self._httpd is not None, "server not started"
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def url(self, path: str) -> str:
        return self.base + "/" + path.lstrip("/")

    def test_host_env(self, host: str = "files.knaif.test") -> str:
        """The ``KNAIF_NET_TEST_HOST`` value that sends *host* here."""
        return f"{host}={self.base}"

    def __enter__(self) -> UrlServer:
        server = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args: Any) -> None:  # quiet
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                server.requests.append({"path": self.path, "headers": dict(self.headers)})
                server._route(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.block_on_close = False  # do not wait for a deliberately slow answer
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    # ── routes ──────────────────────────────────────────────────────────────────────────

    def _route(self, h: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(h.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = parts.path
        if path.startswith("/_/redirect/"):
            n_str, _, rest = path[len("/_/redirect/") :].partition("/")
            n = int(n_str)
            target = f"/_/redirect/{n - 1}/{rest}" if n > 1 else "/" + rest
            return self._send(h, 302, headers={"Location": target})
        if path == "/_/redirect-to":
            return self._send(h, 302, headers={"Location": query["location"]})
        if path.startswith("/_/big/"):
            size = int(path[len("/_/big/") :])
            headers = {"Content-Type": "application/octet-stream"}
            if not query.get("nolen"):
                headers["Content-Length"] = str(size)
            return self._send(h, 200, b"\0" * size, headers, length=False)
        if path.startswith("/_/slow/"):
            time.sleep(float(path[len("/_/slow/") :]))
            return self._send(h, 200, b"late", {"Content-Type": "application/octet-stream"})
        if path == "/_/html":
            body = b"<html><body>a video player</body></html>"
            return self._send(h, 200, body, {"Content-Type": "text/html; charset=utf-8"})
        if path.startswith("/_/status/"):
            return self._send(h, int(path[len("/_/status/") :]), b"")
        file = self.files.get(path)
        if file is None or not file.is_file():
            return self._send(h, 404, b"not found")
        ctype = query.get("type") or mimetypes.guess_type(file.name)[0] or ""
        headers = {"Content-Type": ctype} if ctype else {}
        return self._send(h, 200, file.read_bytes(), headers)

    @staticmethod
    def _send(
        h: BaseHTTPRequestHandler,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        *,
        length: bool = True,
    ) -> None:
        h.send_response(status)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        if length and body:
            h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        try:
            h.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up (a size cap)
            pass


TEST_HOST = "files.knaif.test"
_RUNNING: list[UrlServer] = []  # kept alive for the eval process


def start_for_corpus(data_dir: Path, fixture_dir: Path, utterances: list[str]) -> UrlServer | None:
    """Serve a skill's URL-input rows: a server for ``data/eval_url_files.json`` and
    ``KNAIF_NET_TEST_HOST`` pointing the reserved host at it. None when no utterance names it
    (or the skill has no URL file map), and then nothing is started or set."""
    import json
    import os

    if not any(f"://{TEST_HOST}/" in u for u in utterances):
        return None
    spec = Path(data_dir) / "eval_url_files.json"
    if not spec.is_file():
        return None
    names = json.loads(spec.read_text(encoding="utf-8")).get("files") or {}
    server = UrlServer({path: Path(fixture_dir) / name for path, name in names.items()})
    server.__enter__()
    _RUNNING.append(server)
    os.environ["KNAIF_NET_TEST_HOST"] = server.test_host_env(TEST_HOST)
    return server
