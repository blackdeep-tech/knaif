"""A locked snapshot names the run it was locked from (plan 2026-10-14, T2b).

A snapshot strips per-row detail, so the release bar's per-row comparison (D2) needs the
scoreboard behind it. `evalsuite lock` writes the snapshot from a saved scoreboard, without
re-running anything, and records `source_run`: the folder, the file and its sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from knaif.evalsuite.snapshot import save_snapshot


def _board(**kw):
    return {
        "verifier": "success",
        "total": 2,
        "outcome_accuracy": 1.0,
        "avg_knaif_score": 1.0,
        "backend_public_name": "knaif-qwen3-4b-v2",
        "rows": [{"id": "a"}, {"id": "b"}],
        **kw,
    }


def test_a_snapshot_records_its_source_run(tmp_path: Path) -> None:
    src = {"folder": "evals/runs/x", "scoreboard": "ffmpeg_m_success.json", "sha256": "0" * 64}
    save_snapshot(_board(), tmp_path / "snap.json", source_run=src)
    snap = json.loads((tmp_path / "snap.json").read_text(encoding="utf-8"))
    assert snap["source_run"] == src and "rows" not in snap


def test_lock_writes_the_snapshot_from_a_saved_board_and_names_it(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    monkeypatch.chdir(tmp_path)
    run = Path("evals/runs/2026-10-20_relock_success/4b")
    run.mkdir(parents=True)
    board = run / "ffmpeg_qwen3-4b-sft-v4-flat-q4_success.json"
    board.write_text(json.dumps(_board()), encoding="utf-8")
    snap = Path("skills/ffmpeg/data/eval_snapshot.json")
    snap.parent.mkdir(parents=True)
    monkeypatch.setattr(cli, "_snapshot_path", lambda skill, model=None: snap)
    monkeypatch.setattr(cli, "_load_skill_verifiers", lambda skill: ({}, None))
    monkeypatch.setattr(cli, "_assert_skill_owns_verifier", lambda *a, **k: None)
    cli.cmd_lock(argparse.Namespace(skill="ffmpeg", current=str(board)))
    written = json.loads(snap.read_text(encoding="utf-8"))
    assert written["source_run"] == {
        "folder": "evals/runs/2026-10-20_relock_success/4b",
        "scoreboard": "ffmpeg_qwen3-4b-sft-v4-flat-q4_success.json",
        "sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
    }
    assert written["total"] == 2 and "rows" not in written


def test_lock_refuses_a_board_outside_the_run_history(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    monkeypatch.chdir(tmp_path)
    board = tmp_path / "board.json"
    board.write_text(json.dumps(_board()), encoding="utf-8")
    with pytest.raises(SystemExit, match="evals/runs"):
        cli.cmd_lock(argparse.Namespace(skill="ffmpeg", current=str(board)))


def test_lock_refuses_a_board_without_rows(tmp_path, monkeypatch) -> None:
    from knaif.evalsuite import cli

    monkeypatch.chdir(tmp_path)
    run = Path("evals/runs/r")
    run.mkdir(parents=True)
    board = run / "b.json"
    board.write_text(json.dumps({k: v for k, v in _board().items() if k != "rows"}), "utf-8")
    with pytest.raises(SystemExit, match="per-row"):
        cli.cmd_lock(argparse.Namespace(skill="ffmpeg", current=str(board)))
