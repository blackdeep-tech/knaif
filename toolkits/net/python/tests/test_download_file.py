"""The net toolkit's download_file: the owner's network policy, against a local server.

One URL in, one file in the run scratch out, or a refusal that names the reason and leaves
nothing behind: other schemes and credentials (before any request, and on every redirect),
too many redirects, a web page or a type the skill does not take, a size over the cap (by
header and by stream), a timeout, an error status. No cookies or credentials are ever sent, and
a dry run makes no request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.evalsuite.url_server import UrlServer
from knaif.handler_api import HandlerContext
from knaif.registry import load_registry
from knaif.urls import DownloadRefused
from toolkits.net.python.download import DownloadFile

KIT = Path(__file__).resolve().parents[2]
VIDEO = ["video/*", "application/octet-stream"]


@pytest.fixture
def server(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 4000)
    (src / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"y" * 1000)
    files = {
        "/media/holiday.mp4": src / "clip.mp4",
        "/ffmpeg/talk.mp4": src / "clip.mp4",
        "/docs/report.pdf": src / "report.pdf",
    }
    with UrlServer(files) as s:
        yield s


def _ctx(tmp_path: Path, *, dry_run: bool = False) -> HandlerContext:
    return HandlerContext(
        root=tmp_path, dry_run=dry_run, confirmed=True, skill_dir=tmp_path, work_dir=tmp_path / "w"
    )


def _get(tmp_path: Path, url: str, **over) -> dict:
    dry = over.pop("_dry", False)
    args = {
        "url": url,
        "dest_dir": str(tmp_path / "w" / "download"),
        "accepted": VIDEO,
        "max_bytes": 1_000_000,
        **over,
    }
    return DownloadFile().handle(args, _ctx(tmp_path, dry_run=dry))


def _left(tmp_path: Path) -> list[str]:
    d = tmp_path / "w" / "download"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# ── a download ─────────────────────────────────────────────────────────────────────────────


def test_a_url_lands_in_the_scratch_under_its_own_name(tmp_path, server):
    out = _get(tmp_path, server.url("media/holiday.mp4"))
    path = Path(out["path"])
    assert path == tmp_path / "w" / "download" / "holiday.mp4"
    assert path.read_bytes() == (tmp_path / "src" / "clip.mp4").read_bytes()
    assert out["bytes"] == path.stat().st_size
    assert out["content_type"] == "video/mp4"


def test_the_reserved_test_host_is_served_locally_only_when_mapped(tmp_path, server, monkeypatch):
    monkeypatch.setenv("KNAIF_NET_TEST_HOST", server.test_host_env())
    out = _get(tmp_path, "https://files.knaif.test/ffmpeg/talk.mp4")
    assert Path(out["path"]).name == "talk.mp4"
    assert server.requests[-1]["path"] == "/ffmpeg/talk.mp4"


def test_redirects_are_followed_and_the_last_hop_reported(tmp_path, server):
    out = _get(tmp_path, server.url("_/redirect/3/media/holiday.mp4"))
    assert Path(out["path"]).name == "holiday.mp4"
    assert out["final_url"] == server.url("media/holiday.mp4")


def test_a_taken_name_gets_a_free_one(tmp_path, server):
    first = _get(tmp_path, server.url("media/holiday.mp4"))
    second = _get(tmp_path, server.url("media/holiday.mp4"))
    assert Path(first["path"]).name == "holiday.mp4"
    assert Path(second["path"]).name == "holiday-1.mp4"


def test_no_cookie_or_credentials_are_sent_and_the_agent_is_named(tmp_path, server):
    _get(tmp_path, server.url("media/holiday.mp4"))
    headers = {k.lower(): v for k, v in server.requests[-1]["headers"].items()}
    assert headers["user-agent"].startswith("knaif/")
    assert "cookie" not in headers and "authorization" not in headers


def test_a_dry_run_makes_no_request(tmp_path, server):
    out = _get(tmp_path, server.url("media/holiday.mp4"), _dry=True)
    assert server.requests == []
    assert out["dry_run"] is True
    assert Path(out["path"]).name == "holiday.mp4"
    assert _left(tmp_path) == []


# ── refusals: nothing requested, or nothing left ───────────────────────────────────────────


@pytest.mark.parametrize(
    "url, phrase",
    [
        ("ftp://127.0.0.1/a.mp4", "only http and https"),
        ("https://alice:secret@files.knaif.test/a.mp4", "user name or password"),
    ],
)
def test_a_refused_url_makes_no_request(tmp_path, server, url, phrase):
    with pytest.raises(DownloadRefused, match=phrase):
        _get(tmp_path, url)
    assert server.requests == []


@pytest.mark.parametrize(
    "location, phrase",
    [
        ("ftp://127.0.0.1/a.mp4", "only http and https"),
        ("file:///etc/passwd", "only http and https"),
        ("http://alice:secret@127.0.0.1/a.mp4", "user name or password"),
    ],
)
def test_every_redirect_is_held_to_the_same_rules(tmp_path, server, location, phrase):
    with pytest.raises(DownloadRefused, match=phrase):
        _get(tmp_path, server.url(f"_/redirect-to?location={location}"))
    assert _left(tmp_path) == []


def test_too_many_redirects_are_refused(tmp_path, server):
    with pytest.raises(DownloadRefused, match="too many redirects"):
        _get(tmp_path, server.url("_/redirect/6/media/holiday.mp4"))


def test_a_web_page_is_refused_by_name(tmp_path, server):
    with pytest.raises(DownloadRefused, match="web page"):
        _get(tmp_path, server.url("_/html"))
    assert _left(tmp_path) == []


def test_a_type_the_skill_does_not_take_is_refused(tmp_path, server):
    with pytest.raises(DownloadRefused, match="not a type this skill accepts"):
        _get(tmp_path, server.url("docs/report.pdf"), accepted=["video/*"])
    assert _left(tmp_path) == []


def test_a_declared_size_over_the_cap_is_refused_before_the_body(tmp_path, server):
    with pytest.raises(DownloadRefused, match="larger than"):
        _get(tmp_path, server.url("_/big/5000"), max_bytes=1000)
    assert _left(tmp_path) == []


def test_a_stream_over_the_cap_is_cut_off_and_removed(tmp_path, server):
    with pytest.raises(DownloadRefused, match="larger than"):
        _get(tmp_path, server.url("_/big/50000?nolen=1"), max_bytes=1000)
    assert _left(tmp_path) == []


def test_a_server_that_does_not_answer_times_out(tmp_path, server):
    with pytest.raises(DownloadRefused, match="timed out"):
        _get(tmp_path, server.url("_/slow/3"), read_timeout_s=0.5)
    assert _left(tmp_path) == []


def test_an_error_status_is_refused_with_its_code(tmp_path, server):
    with pytest.raises(DownloadRefused, match="404"):
        _get(tmp_path, server.url("nothing/here.mp4"))


# ── the contract ───────────────────────────────────────────────────────────────────────────


def test_download_file_declares_its_effects_and_is_internal():
    tools = load_registry(KIT / "tools.yaml")
    tool = tools["download_file"]
    assert set(tool.effects or ()) == {"net", "fs.write"}
    assert tool.internal
