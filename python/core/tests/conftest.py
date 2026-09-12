"""Shared pytest fixtures for knaif core tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knaif.agent import CommandAgent
from knaif.registry import load_registry

_MEDIA_BINARIES = {"ffmpeg", "ffprobe"}


@pytest.fixture(autouse=True)
def _no_media_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core tests must never shell out to ffmpeg/ffprobe.

    Several core tests build the *real* ffmpeg skill — it is the repo's richest
    expander, so it is the honest fixture for testing stem resolution, the NL
    clarify gate and eval-suite sandbox wiring. None of them care about media:
    every one runs ``dry_run=True`` against a zero-byte ``.mp4``, which ffprobe
    rejects ("moov atom not found"), so they already take the ``_dummy_probe``
    preview path on a developer box.

    The binary was therefore doing nothing but changing which exception was
    raised — ``RuntimeError`` where it exists, ``FFmpegNotAvailable`` where it
    does not, and ``InspectMediaStep`` re-raises only the latter. That is why
    seven core tests failed the first time CI ran them on a clean runner, and
    why nobody had noticed: every machine that ever ran this suite happened to
    have ffmpeg installed.

    Guarding ``subprocess.run`` rather than patching the skill's ``_deps`` module
    is deliberate. The skill loader imports handlers under a synthetic package
    key (``_skill_oop_ffmpeg_<hash>``), so the module object a test could reach
    by name is not necessarily the one the loaded skill holds; the guard sits
    below that and cannot be defeated by it, applies before any skill loads, and
    covers tests not yet written.

    Preview paths swallow the error and stub the probe — the behaviour these
    tests already exercise. An *executing* path surfaces it loudly, which is the
    correct outcome: a core test that genuinely needs to run ffmpeg belongs in
    ``skills/ffmpeg/python/tests/``, next to the skill it is testing.
    """
    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
        if isinstance(first, (str, Path)) and Path(str(first)).stem.lower() in _MEDIA_BINARIES:
            raise RuntimeError(
                f"core tests must not invoke {Path(str(first)).stem!r}: stub the probe, "
                "or move the test to skills/ffmpeg/python/tests/ where the binary is a "
                "declared dependency"
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """Create a seeded sandbox directory under tmp_path."""
    sb = tmp_path / "sandbox"
    sb.mkdir()
    reports = sb / "reports"
    reports.mkdir()
    tmp = sb / "tmp"
    tmp.mkdir()
    (reports / "jan_report.txt").write_text("jan", encoding="utf-8")
    (reports / "feb_report.txt").write_text("feb", encoding="utf-8")
    (tmp / "cache.tmp").write_text("cache", encoding="utf-8")
    (tmp / "old.log").write_text("log", encoding="utf-8")
    bin_dir = sb / "bin"
    bin_dir.mkdir()
    (bin_dir / "app.exe").write_bytes(b"")
    (bin_dir / "script.sh").write_bytes(b"")
    return sb


TOOLS_YAML = Path("skills") / "io" / "tools.yaml"
IO_SKILL_DIR = Path("skills") / "io"


@pytest.fixture()
def registry():
    """Return the ToolDef registry loaded from skills/io/tools.yaml."""
    return load_registry(TOOLS_YAML)


@pytest.fixture()
def agent(sandbox: Path, tmp_path: Path) -> CommandAgent:
    """Return a CommandAgent configured against the test sandbox."""
    return CommandAgent.from_skill(
        skill_dir=IO_SKILL_DIR,
        sandbox=sandbox,
        root=tmp_path,
    )


# ── temporary: the committed snapshots outlived the corpus they measured ─────
# Delete this together with the strict xfail in test_acceptance.py when S5 re-locks
# (docs/plans/2026-09-11-reject-clarify-taxonomy.md, T7).


def rebase_snapshot_tag_counts(board: dict, skill: str) -> dict:
    """Move every stale tag's row *count* onto today's corpus, keeping its measured rate.

    Tests that exercise the `accept` commands need "a scoreboard that passes the bar"; the
    committed snapshot was that board by construction until T4 of
    docs/plans/2026-09-11-reject-clarify-taxonomy.md relabelled 18 utterances from `reject`
    to `clarify`. The snapshot's `reject` entry still describes 34 utterances, so a
    `max_failures` budget written for the 16 that carry the tag today reads it as 5 failures
    against a budget of 3 — a real mismatch, but not one about the CLI under test.

    Rates carry over untouched: this restates the same measurement over the population the
    bar now describes, it does not invent a kinder one. It also cannot fix every kind of
    staleness — a tag whose *count* is unchanged while its membership flipped (ffmpeg's
    `safety` slice: 8 of 16 utterances went `reject` -> `clarify`) looks current here and is
    not. Counts are what a scoreboard records.

    Asserts that it changed something, so the day the snapshot is re-locked this fails and
    forces its own deletion rather than quietly becoming a no-op.
    """
    import json
    from pathlib import Path

    corpus = Path(__file__).resolve().parents[3] / "skills" / skill / "data" / "eval.jsonl"
    counts: dict[str, int] = {}
    for line in corpus.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        n = len(row.get("utterances") or [row.get("utterance")])
        for tag in row.get("tags") or []:
            counts[tag] = counts.get(tag, 0) + n

    rebased = []
    for tag, entry in (board.get("by_tag") or {}).items():
        if tag in counts and entry.get("total") != counts[tag]:
            entry["total"] = counts[tag]
            rebased.append(tag)
    assert rebased, (
        f"{skill}: nothing to rebase — the snapshot and the corpus agree again, so this "
        "helper and the strict xfail in test_acceptance.py should both be deleted"
    )
    return board
