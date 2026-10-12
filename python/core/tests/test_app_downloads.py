"""The Python CLI's URL-input approval: --allow-download, the per-URL question, no terminal.

--yes never covers network access (like --overwrite): without the flag a person at a terminal is
asked once per URL, default No, and with nobody to ask the agent stops naming the flag.
"""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from knaif.app import cli, download_lines, wire_downloads


def _agent() -> SimpleNamespace:
    return SimpleNamespace(allow_download=None, download_confirmer="unset")


def test_the_flag_approves_every_download_and_asks_nobody():
    agent = _agent()
    wire_downloads(agent, allow_download=True, interactive=True, ask=lambda *_: False)
    assert agent.allow_download is True
    assert agent.download_confirmer is None


def test_a_terminal_is_asked_per_url_with_no_as_the_default():
    asked: list[tuple[str, bool]] = []
    agent = _agent()
    wire_downloads(
        agent, allow_download=False, interactive=True, ask=lambda q, d: asked.append((q, d))
    )
    assert agent.download_confirmer("https://h.test/a.mp4", 2_000_000_000) is False
    assert asked == [("Download https://h.test/a.mp4 (up to 2,000 MB)?", False)]


def test_with_nobody_to_ask_there_is_no_confirmer():
    agent = _agent()
    wire_downloads(agent, allow_download=False, interactive=False, ask=lambda *_: True)
    assert agent.allow_download is False
    assert agent.download_confirmer is None  # the agent then stops, naming --allow-download


def test_download_lines_say_what_was_or_would_be_fetched(tmp_path):
    lines = download_lines(
        [
            {"url": "https://h.test/a.mp4", "path": str(tmp_path / "a.mp4"), "dry_run": True},
            {"url": "https://h.test/b.pdf", "path": str(tmp_path / "b.pdf"), "bytes": 1200},
        ]
    )
    assert lines[0].startswith("would download https://h.test/a.mp4")
    assert lines[1].startswith("downloaded https://h.test/b.pdf (1,200 bytes)")


def test_run_offers_the_flag():
    out = CliRunner().invoke(cli, ["run", "--help"]).output
    assert "--allow-download" in out
