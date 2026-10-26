"""Committed evidence is written without local paths (AGENTS.md, Public Output Hygiene rule 1).

Parity reports recorded every rendered ffmpeg command with absolute fixture paths, and the repo
is public. The writers now pass their JSON through `redact_local_paths` instead of relying on
someone scrubbing the output afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

from knaif.evalsuite.redact import redact_local_paths

ROOT = Path(".").resolve()


def test_the_checkout_and_home_become_placeholders_in_every_string() -> None:
    doc = {
        "command": "ffmpeg -i C:\\src\\knaif\\sandbox\\clip.mp4 C:\\src\\knaif\\sandbox\\out.mp4",
        "model": "C:\\Users\\alice\\.knaif\\models\\m.gguf",
        "nested": [{"p": "C:/src/knaif/evals/x.json"}, 3, None],
        "keep": "relative/sandbox/clip.mp4",
    }

    out = redact_local_paths(doc, root="C:\\src\\knaif", home="C:\\Users\\alice")

    assert out["command"] == "ffmpeg -i <repo>\\sandbox\\clip.mp4 <repo>\\sandbox\\out.mp4"
    assert out["model"] == "~\\.knaif\\models\\m.gguf"
    assert out["nested"] == [{"p": "<repo>/evals/x.json"}, 3, None]
    assert out["keep"] == "relative/sandbox/clip.mp4"


def test_the_checkout_wins_over_a_home_it_lives_under() -> None:
    out = redact_local_paths(
        "/home/alice/knaif/sandbox/a", root="/home/alice/knaif", home="/home/alice"
    )
    assert out == "<repo>/sandbox/a"


def test_windows_paths_match_in_any_case_and_a_longer_name_does_not() -> None:
    out = redact_local_paths(
        ["c:\\users\\ALICE\\x", "C:\\Users\\alicexyz\\x"],
        root="C:\\src\\knaif",
        home="C:\\Users\\alice",
    )
    assert out == ["~\\x", "C:\\Users\\alicexyz\\x"]


def test_a_container_root_home_is_left_alone() -> None:
    assert redact_local_paths("/root/.cargo/x", root="/src", home="/root") == "/root/.cargo/x"


def test_a_saved_scoreboard_carries_no_local_path(tmp_path: Path) -> None:
    # `evalsuite run` and `evalsuite native` saved boards whose rows held the checkout's
    # absolute path, so the macOS 1.3.0 boards could not be committed.
    from knaif.evalsuite.report import save_scoreboard_json

    checkout, home = str(Path.cwd().resolve()), str(Path.home())
    board = {"rows": [{"command": f"knaif run {checkout}/sandbox/a.mp4", "model": f"{home}/m"}]}

    out = tmp_path / "board.json"
    save_scoreboard_json(board, out)

    row = json.loads(out.read_text(encoding="utf-8"))["rows"][0]
    assert row == {"command": "knaif run <repo>/sandbox/a.mp4", "model": "~/m"}


def test_the_committed_evidence_writers_redact() -> None:
    parity = (ROOT / "scripts" / "parity_check.py").read_text(encoding="utf-8")
    assert parity.count("redact_local_paths(") >= 2  # report.json and meta.json
    for rel in ("python/core/knaif/evalsuite/gate.py", "python/core/knaif/evalsuite/cli.py"):
        assert "redact_local_paths(" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_a_run_scratch_folder_becomes_work_with_its_run_id_dropped() -> None:
    """Scratch lives in the OS temp dir, under the home directory on Windows. The run id is
    random per run, so keeping it would also make two identical runs' evidence differ."""
    cmd = (
        "ffmpeg -i C:\\Users\\alice\\AppData\\Local\\Temp\\knaif\\run-812-a1b2c3d4\\mid.mp4 "
        "C:\\src\\knaif\\sandbox\\out.mp4"
    )
    out = redact_local_paths(
        cmd,
        root="C:\\src\\knaif",
        home="C:\\Users\\alice",
        scratch="C:\\Users\\alice\\AppData\\Local\\Temp\\knaif",
    )
    assert out == "ffmpeg -i <work>\\mid.mp4 <repo>\\sandbox\\out.mp4"


def test_posix_scratch_is_redacted_too() -> None:
    out = redact_local_paths(
        "/tmp/knaif/run-1-ff/a/x.mp4", root="/src", home="/home/alice", scratch="/tmp/knaif"
    )
    assert out == "<work>/a/x.mp4"
