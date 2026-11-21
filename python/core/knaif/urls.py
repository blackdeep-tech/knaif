"""URL inputs: the value class, the refusals made before any request, and download naming.

A plan argument is a URL when it starts with a scheme followed by ``://``. A URL is never a
path: it is not resolved against the sandbox, globbed or stem-matched. Only ``http`` and
``https`` can be downloaded, and a URL that carries a user name or password is refused, since
knaif sends no credentials. The rules are pinned for both runtimes by
``contracts/parity/url_inputs_cases.json`` (native: ``knaif_core::urls``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_ALLOWED_SCHEMES = ("http", "https")
_BAD_NAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_WEB_PAGE_TYPES = ("text/html", "application/xhtml+xml")
_OCTET_STREAM = "application/octet-stream"

#: The extension a download gets when its URL names none, by content type.
EXTENSIONS: dict[str, str] = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/x-matroska": "mkv",
    "video/x-msvideo": "avi",
    "video/mpeg": "mpg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tif",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "application/rtf": "rtf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
}


@dataclass(frozen=True)
class UrlInputs:
    """A skill's ``url_inputs:``: the content types it can work on and the size cap."""

    accepted: tuple[str, ...]
    max_bytes: int


def parse_url_inputs(raw: object, toolkits: list[str]) -> UrlInputs | None:
    """``url_inputs: {accepted: [...], max_mb: N}`` from ``skill.yaml``; None when absent."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("url_inputs must be a mapping with `accepted` and `max_mb`")
    accepted = raw.get("accepted")
    max_mb = raw.get("max_mb")
    if not (isinstance(accepted, list) and accepted and all(isinstance(a, str) for a in accepted)):
        raise ValueError("url_inputs.accepted must be a non-empty list of content types")
    if isinstance(max_mb, bool) or not isinstance(max_mb, int | float) or max_mb <= 0:
        raise ValueError("url_inputs.max_mb must be a positive number")
    if "net" not in toolkits:
        raise ValueError("url_inputs needs the net toolkit: add `net` to `toolkits:`")
    return UrlInputs(tuple(accepted), int(max_mb * 1_000_000))


class DownloadRefused(ValueError):
    """A URL input that will not be downloaded, or a download that was stopped. The message is
    for the user: it names the URL and the reason."""


def is_url(value: object) -> bool:
    """Whether *value* is a URL (a scheme followed by ``://``), whatever the scheme."""
    return isinstance(value, str) and bool(_URL.match(value))


def check_url(value: str) -> str | None:
    """Why *value* cannot be downloaded, or None when it can. Call only on a URL."""
    scheme = value.split("://", 1)[0].lower()
    if scheme not in _ALLOWED_SCHEMES:
        return f"only http and https URLs can be downloaded, not {scheme}:// ({value})"
    parts = urlsplit(value)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return (
            f"a URL with a user name or password is refused; knaif sends no credentials ({value})"
        )
    if not parts.hostname:
        return f"the URL has no host ({value})"
    return None


def media_type(content_type: str | None) -> str:
    """The bare, lower-case media type of a Content-Type header; a missing one is octet-stream."""
    bare = (content_type or "").split(";", 1)[0].strip().lower()
    return bare or _OCTET_STREAM


def url_file_name(url: str, content_type: str | None = None) -> str:
    """A safe local file name for what *url* returns.

    The last segment of the decoded path (a ``/`` or ``\\`` anywhere ends a segment, so an
    encoded path cannot climb out of its folder), with leading dots and the characters Windows
    refuses removed. A name without an extension takes one from *content_type* when known.
    """
    path = unquote(urlsplit(url).path)
    segments = [s for s in re.split(r"[/\\]", path) if s]
    name = segments[-1] if segments else ""
    name = _BAD_NAME_CHARS.sub("_", name).lstrip(".").rstrip(". ")
    if name in ("", ".."):
        name = "download"
    if "." not in name and content_type:
        ext = EXTENSIONS.get(media_type(content_type))
        if ext:
            name = f"{name}.{ext}"
    return name


def check_content_type(content_type: str | None, accepted: list[str]) -> str | None:
    """Why a response of *content_type* is refused for a skill accepting *accepted*, or None.

    Patterns are exact types, or end in ``*`` to match a prefix (``video/*``,
    ``application/vnd.openxmlformats-officedocument.*``). A missing type counts as
    ``application/octet-stream``. A web page is named as one, since that is the usual mistake:
    a link to the page that plays a video rather than to the video.
    """
    media = media_type(content_type)
    for pattern in accepted:
        p = pattern.strip().lower()
        if p == media or (p.endswith("*") and media.startswith(p[:-1])):
            return None
    if media in _WEB_PAGE_TYPES:
        return f"the link is a web page ({media}), not a file this skill can work on"
    return f"the download is {media}, not a type this skill accepts ({', '.join(accepted)})"
