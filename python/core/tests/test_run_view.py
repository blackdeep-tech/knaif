"""`knaif.run_view` pieces the end-to-end contract (mock runs) cannot reach."""

from __future__ import annotations

import os
import time
from pathlib import Path

from knaif import ui
from knaif.run_view import QuietPlanning, choose_style, step_detail


def test_planning_keeps_llama_off_the_tree_and_still_spins(capfd) -> None:
    """Native silences fd 2 while the model loads and plans, so llama.cpp and the GPU
    libraries cannot write over the tree, and draws its spinner on a private copy."""
    with QuietPlanning(ui.Style(color=False), active=True):
        os.write(2, b"ggml_cuda_init: noise\n")
        time.sleep(0.25)
    os.write(2, b"after\n")
    err = capfd.readouterr().err
    assert "noise" not in err
    assert "Planning…" in err
    assert err.endswith("after\n"), "fd 2 is restored"


def test_an_inactive_window_changes_nothing(capfd) -> None:
    with QuietPlanning(None, active=True):
        os.write(2, b"seen\n")
    assert capfd.readouterr().err == "seen\n"


def test_the_view_follows_knaif_view(monkeypatch) -> None:
    monkeypatch.setenv("KNAIF_VIEW", "plain")
    assert choose_style() is None
    monkeypatch.setenv("KNAIF_VIEW", "rich")
    monkeypatch.setenv("NO_COLOR", "1")
    assert choose_style() == ui.Style(color=False)


def test_a_plan_row_names_the_files_a_glob_finds(tmp_path: Path) -> None:
    for name in ("city.mov", "beach.mov", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    kinds = {"mov": "video", "mp4": "video"}
    assert step_detail({"inputs": ["*.mp4", "*.mov"]}, tmp_path, kinds) == "beach.mov (+1 more)"
    assert step_detail({"inputs": ["*.avi"]}, tmp_path, kinds) == "*.avi"
    assert step_detail({"input": "a.pdf", "output": "b.pdf"}, tmp_path, kinds) == "a.pdf → b.pdf"
