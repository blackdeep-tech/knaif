"""Does the harness measure the command the plan actually rendered?

Until T5b it did not, in two separate ways, and both flattered the Python lane:

1. **Failure reporting.** A non-zero ffmpeg exit was invisible to the outcome. The row still
   recorded ``outcome = plan`` and counted as *correct*; only the artifact score noticed, and
   not always. ``_run_artifact`` returned ``None`` for a non-zero exit, a missing binary, a
   timeout, **and** a command that exited 0 without writing the expected file — four different
   things, one indistinguishable answer.
2. **Execution-path fidelity.** ``_run_artifact`` rewrote ``-i`` to the fixture path *and* the
   output into a separate directory. Python therefore never executed the command the plan
   rendered: the ``output == input`` collision that ``ffmpeg_175`` is about was removed before
   ffmpeg saw it. Native executes it as rendered and fails. Exit-code propagation alone does
   not make the two lanes comparable — the rewrite has to go.

Native is the reference here, not the thing being fixed: it already gives every utterance its
own work dir holding every fixture and runs the binary with ``cwd=work_dir`` and no path
rewriting at all. The target is *Python behaves like native*.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.chain import resolve_command
from knaif.evalsuite.provisioning import (
    fixture_content_hashes,
    provision_row_dir,
    verify_fixture_integrity,
)


@pytest.fixture()
def fixtures(tmp_path: Path) -> Path:
    d = tmp_path / "fixtures"
    (d / "sub").mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"original-clip")
    (d / "other.mp4").write_bytes(b"original-other")
    (d / "sub" / "clip.mp4").write_bytes(b"original-sub-clip")
    (d / ".cache.json").write_text("{}", encoding="utf-8")
    return d


# -- 1. provisioning ----------------------------------------------------------


def test_a_row_dir_holds_every_fixture_not_only_the_named_one(
    fixtures: Path, tmp_path: Path
) -> None:
    """Python ran the whole corpus in one shared sandbox, so every row saw every fixture.

    Provisioning only the fixture a row *declares* is stricter than that, and the asymmetry
    was scored against the runtime rather than the harness.
    """
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    assert (row_dir / "clip.mp4").exists()
    assert (row_dir / "other.mp4").exists()


def test_directory_structure_is_preserved(fixtures: Path, tmp_path: Path) -> None:
    """`a/clip.mp4` and `b/clip.mp4` are two files; flattening merges them."""
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    assert (row_dir / "sub" / "clip.mp4").read_bytes() == b"original-sub-clip"
    assert (row_dir / "clip.mp4").read_bytes() == b"original-clip"


def test_the_cache_file_is_not_provisioned_as_media(fixtures: Path, tmp_path: Path) -> None:
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    assert not (row_dir / ".cache.json").exists()


def test_fixtures_are_copied_not_hardlinked(fixtures: Path, tmp_path: Path) -> None:
    """**The integrity property.** Hardlinks made a write to a row's copy destroy the source.

    Every rendered command carries ``-y``, so a plan whose output lands on a fixture's *name*
    writes straight through the link and corrupts the shared fixture for every later row.
    Nothing would catch it: ``.cache.json`` hashes the generation command, not the bytes.
    """
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    (row_dir / "clip.mp4").write_bytes(b"clobbered")
    assert (fixtures / "clip.mp4").read_bytes() == b"original-clip"


# -- 2. one path rule ---------------------------------------------------------


def _echo_command(inp: str, out: str) -> str:
    """A command shaped like ffmpeg's. Only ever *resolved* here, never executed: the path
    rule is pure, which is what lets a core test hold it without the binary."""
    return f"ffmpeg -y -i {inp} -c:v libx264 {out}"


def test_a_command_runs_against_the_row_dir_copy(fixtures: Path, tmp_path: Path) -> None:
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    toks, _ = resolve_command(_echo_command("clip.mp4", "out.mp4"), row_dir, [fixtures])
    toks = toks.split()
    assert Path(toks[toks.index("-i") + 1]) == row_dir / "clip.mp4"
    assert Path(toks[-1]) == row_dir / "out.mp4"


def test_a_relative_path_keeps_its_directory(fixtures: Path, tmp_path: Path) -> None:
    """Re-rooting by BASENAME merges `a/clip.mp4` with `b/clip.mp4`, and turns the perfectly
    legal `source/clip.mp4 -> exports/clip.mp4` into a false collision. Native preserves
    directories, so a basename rule would also make the lanes disagree about paths."""
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    toks, _ = resolve_command(_echo_command("sub/clip.mp4", "sub/out.mp4"), row_dir, [fixtures])
    toks = toks.split()
    assert Path(toks[toks.index("-i") + 1]) == row_dir / "sub" / "clip.mp4"
    assert Path(toks[-1]) == row_dir / "sub" / "out.mp4"


def test_an_output_equal_to_its_input_survives_into_execution(
    fixtures: Path, tmp_path: Path
) -> None:
    """The whole point: `ffmpeg_175`'s collision must reach ffmpeg, not be tidied away.

    The old runner rewrote the input to the fixture directory and the output to a separate
    one, so the two paths could never be equal and Python could not reproduce the failure
    native hits.
    """
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    toks, _ = resolve_command(_echo_command("clip.mp4", "clip.mp4"), row_dir, [fixtures])
    toks = toks.split()
    assert toks[toks.index("-i") + 1] == toks[-1]


def test_a_chain_feeds_its_own_intermediate(fixtures: Path, tmp_path: Path) -> None:
    """Step 1 reads what step 0 wrote — because both are re-rooted at the same dir, not
    because of a prior-output-then-fixture lookup."""
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    second, _ = resolve_command(_echo_command("mid.mp4", "final.mp4"), row_dir, [fixtures])
    second = second.split()
    assert Path(second[second.index("-i") + 1]) == row_dir / "mid.mp4"


# -- 3. fixture integrity -----------------------------------------------------


def test_content_hashes_are_recorded_per_fixture(fixtures: Path) -> None:
    """Scores trace to the exact media they were measured against.

    `.cache.json` hashed the *generation command*, which cannot notice a fixture whose bytes
    changed underneath it — the failure mode hardlinking used to cause.
    """
    hashes = fixture_content_hashes(fixtures)
    assert set(hashes) == {"clip.mp4", "other.mp4", "sub/clip.mp4"}
    assert all(len(h) == 64 for h in hashes.values())


def test_a_changed_fixture_changes_its_hash(fixtures: Path) -> None:
    before = fixture_content_hashes(fixtures)
    (fixtures / "clip.mp4").write_bytes(b"different")
    after = fixture_content_hashes(fixtures)
    assert before["clip.mp4"] != after["clip.mp4"]
    assert before["other.mp4"] == after["other.mp4"]


def test_only_a_regenerated_fixture_gets_its_content_hash_written(
    fixtures: Path, monkeypatch
) -> None:
    """Blessing whatever is on disk would certify the corruption it exists to detect.

    The command hash skips a fixture whose generation command has not changed. If the content
    hash were then taken from the current bytes, the sequence *generate -> alter the bytes ->
    re-run the recommended command* would end with the altered bytes recorded as trusted, and
    the check would report everything as fine forever after.
    """
    import argparse
    import subprocess

    from knaif.evalsuite import cli

    monkeypatch.setattr(
        cli, "_load_skill_fixtures", lambda skill: ({"clip.mp4": "ffmpeg {output}"}, {})
    )
    monkeypatch.setattr(cli, "_default_fixture_dir", lambda sandbox, skill: fixtures)

    class _Ok:
        returncode, stderr = 0, ""

    # First pass: the fixture is generated, so its bytes are recorded.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Ok())
    cli.cmd_fixtures_regen(argparse.Namespace(skill="ffmpeg", sandbox=None, force=False))
    trusted = json.loads((fixtures / ".cache.json").read_text(encoding="utf-8"))["__content__"]
    assert "clip.mp4" in trusted

    # Now corrupt it and re-run: the command is unchanged, so the fixture is skipped...
    (fixtures / "clip.mp4").write_bytes(b"corrupted")
    cli.cmd_fixtures_regen(argparse.Namespace(skill="ffmpeg", sandbox=None, force=False))
    after = json.loads((fixtures / ".cache.json").read_text(encoding="utf-8"))["__content__"]

    assert after["clip.mp4"] == trusted["clip.mp4"], "the corruption was certified as trusted"
    assert fixture_content_hashes(fixtures)["clip.mp4"] != after["clip.mp4"]
    assert verify_fixture_integrity(fixtures), "the check must now report the drift"


def test_an_absolute_sandbox_path_is_re_rooted_relative_to_its_anchor(
    fixtures: Path, tmp_path: Path
) -> None:
    """Python renders absolute paths where native renders bare names.

    Running Python's command verbatim would write into the real fixture directory and corrupt
    it for every later row; taking the basename would lose `sub/`. Re-rooting relative to the
    anchor keeps both properties.
    """
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    absolute = _echo_command(str(fixtures / "sub" / "clip.mp4"), str(fixtures / "out.mp4"))
    toks, collapsed = resolve_command(absolute, row_dir, [fixtures])
    toks = toks.split()
    assert collapsed == [], "an absolute path under the anchor must not be collapsed"
    assert Path(toks[toks.index("-i") + 1]) == row_dir / "sub" / "clip.mp4"
    assert Path(toks[-1]) == row_dir / "out.mp4"


def test_a_relative_anchor_does_not_collapse_every_path(fixtures: Path, tmp_path: Path) -> None:
    """The CLI's default fixture dir is RELATIVE; Python renders ABSOLUTE paths.

    An unresolved anchor never matches, so every token fell through to the basename fallback
    and `source/clip.mp4 -> exports/clip.mp4` became `row/clip.mp4 -> row/clip.mp4`: a false
    collision, on the default code path, invisible unless someone reads the command. Every
    other test here passes an absolute `tmp_path` anchor, which is exactly why they missed it.
    """
    import os

    row_dir = tmp_path / "row"
    absolute = _echo_command(str(fixtures / "sub" / "clip.mp4"), str(fixtures / "sub" / "out.mp4"))
    relative_anchor = Path(os.path.relpath(fixtures, Path.cwd()))
    assert not relative_anchor.is_absolute()

    resolved, collapsed = resolve_command(absolute, row_dir, [relative_anchor])
    toks = resolved.split()
    assert collapsed == [], "a relative anchor must still match an absolute token"
    assert Path(toks[toks.index("-i") + 1]) == row_dir / "sub" / "clip.mp4"
    assert Path(toks[-1]) == row_dir / "sub" / "out.mp4"


def test_a_path_outside_every_anchor_is_reported_not_silently_collapsed(tmp_path: Path) -> None:
    """Re-rooting a path from outside the eval tree loses a directory. Say so."""
    outside = tmp_path / "elsewhere" / "clip.mp4"
    resolved, collapsed = resolve_command(
        _echo_command(str(outside), "out.mp4"), tmp_path / "row", [tmp_path / "anchor"]
    )
    assert collapsed == [str(outside)]
    assert resolved.split()[3] == str(tmp_path / "row" / "clip.mp4")


def test_provisioning_refuses_a_row_dir_inside_the_fixture_dir(fixtures: Path) -> None:
    """Otherwise the row's outputs join every later row's fixture inventory."""
    with pytest.raises(ValueError, match="inside the fixture dir"):
        provision_row_dir(fixtures, fixtures / "row")


def test_a_row_dir_is_emptied_before_it_is_reused(fixtures: Path, tmp_path: Path) -> None:
    """Row dirs are deterministic and failed ones are kept on purpose.

    Overlaying onto what is already there lets an intermediate from an earlier run satisfy a
    later plan's *missing* input — a pass the plan did not earn, and one that disappears the
    moment someone clears the sandbox.
    """
    row_dir = tmp_path / "row"
    provision_row_dir(fixtures, row_dir)
    (row_dir / "leftover.mp4").write_bytes(b"from an earlier run")
    provision_row_dir(fixtures, row_dir)
    assert not (row_dir / "leftover.mp4").exists()


# -- 4. what survives a run ---------------------------------------------------


def test_a_graded_failure_keeps_its_work_dir(tmp_path: Path) -> None:
    """A row can route correctly, exit 0, and still produce the wrong codec.

    That is a *graded* failure with no execution error, and it is exactly the case someone
    needs the produced file for. Deciding retention from the outcome label alone deletes it.
    """
    from knaif.evalsuite.cli import _reclaim_row_dirs

    class _Out:
        def __init__(self, rid, idx, outcome):
            self.id, self.utterance_idx, self.outcome, self.error = rid, idx, outcome, None

    class _Row:
        def __init__(self, rid):
            self.id, self.expected_outcome = rid, "plan"

    for rid in ("clean", "badcodec"):
        (tmp_path / f"{rid}__0").mkdir()
    scoreboard = {
        "rows": [
            {"id": "clean", "utterance_idx": 0, "knaif_score": 1.0, "knaif_failed": []},
            {
                "id": "badcodec",
                "utterance_idx": 0,
                "knaif_score": 0.667,
                "knaif_failed": ["video_codec: expected 'h264', got 'hevc'"],
            },
        ]
    }
    _reclaim_row_dirs(
        [_Out("clean", 0, "plan"), _Out("badcodec", 0, "plan")],
        [_Row("clean"), _Row("badcodec")],
        tmp_path,
        scoreboard,
    )
    assert not (tmp_path / "clean__0").exists(), "a passing row's copies are reclaimed"
    assert (tmp_path / "badcodec__0").exists(), "a graded failure keeps its evidence"


# -- 5. the subprocess boundary ----------------------------------------------
#
# `_run_artifact` collapsed a non-zero exit, a missing binary, a timeout and a command that
# exited 0 without writing the file into one `None`. Those four are now four answers, and the
# translation is what the runner's exit-code rule depends on — so it is tested here rather
# than assumed.


def _chain(commands: list[str], fixtures: Path, row_dir: Path, fake):
    import knaif.evalsuite.chain as chain_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(chain_mod.subprocess, "run", fake)
        return chain_mod.run_command_chain(commands, fixtures, row_dir)


def test_a_missing_binary_is_reported_as_127(fixtures: Path, tmp_path: Path) -> None:
    def _missing(*a, **k):
        raise FileNotFoundError

    results = _chain([_echo_command("clip.mp4", "out.mp4")], fixtures, tmp_path / "row", _missing)
    assert results[0]["returncode"] == 127
    assert "not found" in results[0]["stderr"]


def test_a_timeout_is_reported_as_124(fixtures: Path, tmp_path: Path) -> None:
    import subprocess

    def _slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)

    results = _chain([_echo_command("clip.mp4", "out.mp4")], fixtures, tmp_path / "row", _slow)
    assert results[0]["returncode"] == 124
    assert "timeout" in results[0]["stderr"]


def test_a_chain_halts_on_the_first_failure(fixtures: Path, tmp_path: Path) -> None:
    """Later commands read earlier outputs, so running on would grade a file nobody made."""
    calls: list[list[str]] = []

    class _R:
        def __init__(self, rc):
            self.returncode, self.stderr = rc, "boom" if rc else ""

    def _fake(toks, *a, **k):
        calls.append(toks)
        return _R(0 if len(calls) == 1 else 1)

    results = _chain(
        [
            _echo_command("clip.mp4", "mid.mp4"),
            _echo_command("mid.mp4", "final.mp4"),
            _echo_command("final.mp4", "never.mp4"),
        ],
        fixtures,
        tmp_path / "row",
        _fake,
    )
    assert len(results) == 2, "the third command must not run"
    assert [r["returncode"] for r in results] == [0, 1]
