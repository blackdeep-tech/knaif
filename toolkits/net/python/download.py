"""download_file (net toolkit): one http(s) URL into the run scratch, under the owner's policy.

Network access is new for knaif, so the policy lives in one place:
- only http and https, no credentials, on the first request and on every redirect (at most 5);
- no cookies, a user agent that names knaif, timeouts on connecting, on each read and overall;
- the response must be a content type the skill accepts, and a web page is named as one;
- a body over the cap is refused by its Content-Length, or cut off while streaming;
- a refusal leaves nothing behind, and a dry run makes no request at all.

The bytes go to ``<dest_dir>/<name>.part`` and are renamed once complete, inside the run
scratch; nothing here writes to the user's folder. Asking the user before a download is the
caller's job (the pipeline asks once per URL, or requires ``--allow-download``).

``KNAIF_NET_TEST_HOST=<host>=<base>`` sends requests for that one host to ``<base>``: the eval
harness's local server answers the reserved ``files.knaif.test`` (RFC 6761) this way. The
product never sets it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from knaif.handler_api import HandlerContext
from knaif.tool import Handler
from knaif.urls import DownloadRefused, check_content_type, check_url, media_type, url_file_name

MAX_REDIRECTS = 5
_REDIRECTS = (301, 302, 303, 307, 308)
_CHUNK = 64 * 1024


def _user_agent() -> str:
    try:
        from importlib.metadata import version

        return f"knaif/{version('knaif')}"
    except Exception:  # noqa: BLE001 - an uninstalled checkout still names itself
        return "knaif/dev"


def _mapped(url: str) -> str:
    """*url* with the test host (``KNAIF_NET_TEST_HOST``) sent to its local base, if set."""
    spec = os.environ.get("KNAIF_NET_TEST_HOST", "")
    host, _, base = spec.partition("=")
    parts = urlsplit(url)
    if not host or not base or (parts.hostname or "").lower() != host.lower():
        return url
    b = urlsplit(base)
    return urlunsplit((b.scheme, b.netloc, parts.path, parts.query, parts.fragment))


def _human(n: int) -> str:
    return f"{n / 1_000_000:g} MB" if n >= 1_000_000 else f"{n:,} bytes"


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise DownloadRefused(f"no free name for {path.name} in {path.parent}")


def _fetch(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args["url"])
    dest_dir = Path(args["dest_dir"])
    accepted = list(args["accepted"])
    max_bytes = int(args["max_bytes"])
    timeout = (float(args.get("connect_timeout_s", 10)), float(args.get("read_timeout_s", 60)))
    deadline = time.monotonic() + float(args.get("total_timeout_s", 600))
    headers = {"User-Agent": _user_agent()}

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if (error := check_url(current)) is not None:
            raise DownloadRefused(error if current == url else f"{url} redirected: {error}")
        try:
            response = requests.get(
                _mapped(current),
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise DownloadRefused(f"{url} timed out ({exc.__class__.__name__})") from exc
        except requests.exceptions.RequestException as exc:
            raise DownloadRefused(f"{url} could not be fetched: {exc}") from exc
        with response:
            if response.status_code in _REDIRECTS and response.headers.get("Location"):
                current = urljoin(current, response.headers["Location"])
                continue
            if response.status_code >= 400:
                raise DownloadRefused(f"the server answered {response.status_code} for {url}")
            ctype = response.headers.get("Content-Type")
            if (error := check_content_type(ctype, accepted)) is not None:
                raise DownloadRefused(f"{url}: {error}")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise DownloadRefused(f"{url} is larger than the {_human(max_bytes)} limit")
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = _free(dest_dir / url_file_name(current, ctype))
            part = final.with_name(final.name + ".part")
            written = 0
            try:
                with part.open("wb") as fh:
                    for chunk in response.iter_content(_CHUNK):
                        written += len(chunk)
                        if written > max_bytes:
                            raise DownloadRefused(
                                f"{url} is larger than the {_human(max_bytes)} limit"
                            )
                        if time.monotonic() > deadline:
                            raise DownloadRefused(f"{url} timed out (took too long overall)")
                        fh.write(chunk)
            except requests.exceptions.RequestException as exc:
                part.unlink(missing_ok=True)
                raise DownloadRefused(f"{url} timed out or broke off: {exc}") from exc
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            part.replace(final)
            return {
                "path": str(final),
                "bytes": written,
                "content_type": media_type(ctype),
                "url": url,
                "final_url": current,
            }
    raise DownloadRefused(f"{url}: too many redirects (more than {MAX_REDIRECTS})")


class DownloadFile(Handler):
    name = "download_file"

    def handle(self, args: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        url = str(args["url"])
        if (error := check_url(url)) is not None:
            raise DownloadRefused(error)
        if ctx.dry_run:
            path = Path(args["dest_dir"]) / url_file_name(url)
            return {"path": str(path), "bytes": 0, "url": url, "dry_run": True}
        return _fetch(args)
