"""`knaif.run_view` pieces the end-to-end contract (mock runs) cannot reach."""

from __future__ import annotations

import os
import time
from pathlib import Path

from knaif import ui
from knaif.run_view import QuietPlanning, RunView, choose_style, step_detail


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


def test_planning_keeps_llama_quiet_in_the_plain_view_too(capfd) -> None:
    """Native turns llama.cpp's log off in every view; the plain view let `llama_context: …`
    through (2026-10-24). Quiet, and no spinner: the plain view draws nothing."""
    with QuietPlanning(None, active=True):
        os.write(2, b"llama_context: noise\n")
        time.sleep(0.15)
    os.write(2, b"after\n")
    assert capfd.readouterr().err == "after\n"


def test_an_inactive_window_changes_nothing(capfd) -> None:
    # `--verbose` and the debug dumps keep llama.cpp's lines.
    with QuietPlanning(None, active=False):
        os.write(2, b"seen\n")
    assert capfd.readouterr().err == "seen\n"


def test_the_view_sees_the_terminal_behind_the_cli_stderr_guard(monkeypatch) -> None:
    """`knaif-cli`'s `main()` wraps a console stderr so click skips its Windows console writer;
    the wrapper has no fileno, so the CLI printed the plain view in every terminal where native
    draws the tree (owner, 2026-10-24, probed in a real Git Bash window). The
    terminal check looks through the wrapper to the stream behind it."""
    from knaif import app, run_view

    class Console:
        def fileno(self) -> int:
            return 2

        def write(self, s: str) -> int:
            return len(s)

    monkeypatch.setattr(run_view, "_fd_is_terminal", lambda fd: fd == 2)
    guarded = app._console_safe_stderr(Console())
    assert run_view._is_terminal(guarded)


def test_an_msys_pty_is_a_terminal() -> None:
    """Git Bash's terminal is an MSYS pty, a named pipe, not a console: native (Rust's
    `IsTerminal`) reads it as a terminal by its name, so Python must too, or it prints the plain
    view where native draws the tree (2026-10-24)."""
    from knaif.run_view import _msys_pty_name

    assert _msys_pty_name("\\msys-dd50a72ab4668b33-pty0-to-master")
    assert _msys_pty_name("\\Device\\NamedPipe\\cygwin-1888ae32e00d56aa-pty1-from-master")
    assert not _msys_pty_name("\\msys-dd50a72ab4668b33-pipe-0x1")
    assert not _msys_pty_name("\\my-pty-notes")


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


def test_a_path_outside_the_working_folder_is_printed_with_slashes(tmp_path: Path) -> None:
    """(2026-10-24): a known folder's output (`~/Desktop/clip.mkv`)
    printed as `C:\\…\\clip.mkv` on Windows, and its command as `"C:\\…"`; native prints
    `C:/…` in both."""
    from knaif.run_view import shown, shown_argv

    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "known" / "Desktop" / "clip.mkv"
    assert shown(str(out), work) == out.as_posix()
    argv = ["ffmpeg", "-i", "clip.mp4", str(out)]
    assert shown_argv(argv, work) == f"ffmpeg -i clip.mp4 {out.as_posix()}"


# ── what a compress did (manual test, 2026-10-28) ─────────────────────────────────────────────

_GREW = {"before": 157680, "after": 173676}


def test_a_compress_result_says_what_it_saved(tmp_path: Path, capsys) -> None:
    out = str(tmp_path / "clip_compressed.mp4")
    item = {"kind": "output", "message": out, "before": 157680, "after": 110376}
    RunView("ffmpeg", ui.Style(color=False), base=tmp_path).item(item, dry_run=False)
    RunView("ffmpeg", None, base=tmp_path).item(item, dry_run=False)
    assert capsys.readouterr().out.splitlines() == [
        " │   └─ ✓ clip_compressed.mp4",
        " │      30% smaller (154.0 KB → 107.8 KB)",
        "✓ clip_compressed.mp4",
        "  30% smaller (154.0 KB → 107.8 KB)",
    ]


def test_a_compress_that_wrote_nothing_says_so_and_counts_nothing(tmp_path: Path, capsys) -> None:
    view = RunView("ffmpeg", ui.Style(color=False), base=tmp_path)
    item = {"kind": "kept", "message": str(tmp_path / "clip.mov"), "written": False, **_GREW}
    assert view.item(item, dry_run=False) == 0, "not a failure"
    assert capsys.readouterr().out.splitlines() == [
        " │   └─ ⚠ couldn't make clip.mov smaller — left it as it was",
        " │      not smaller (154.0 KB → 169.6 KB)",
    ]
    assert view._written == 0


def test_a_compress_that_delivered_its_input_counts_one_file(tmp_path: Path, capsys) -> None:
    view = RunView("ffmpeg", None, base=tmp_path)
    out = str(tmp_path / "clip_compressed.mp4")
    view.item({"kind": "kept", "message": out, "written": True, **_GREW}, dry_run=False)
    assert capsys.readouterr().out.splitlines() == [
        "⚠ clip_compressed.mp4: couldn't make it smaller — kept it as it was",
        "  not smaller (154.0 KB → 169.6 KB)",
    ]
    assert view._written == 1


def test_a_compressed_write_says_what_it_saved_or_that_it_kept_the_original(
    tmp_path: Path, capsys
) -> None:
    """documents' `compress_pdf` (a `wrote` item), worded as ffmpeg's compress."""
    out = str(tmp_path / "sample-compressed.pdf")
    view = RunView("documents", ui.Style(color=False), base=tmp_path)
    saved = {"kind": "wrote", "message": "compress (ghostscript)", "outputs": [out]}
    view.item({**saved, "before": 10000, "after": 7000}, dry_run=False)
    view.item({**saved, "before": 2227, "after": 3952, "kept": True}, dry_run=False)
    assert capsys.readouterr().out.splitlines() == [
        " │   compress (ghostscript)",
        " │   → sample-compressed.pdf",
        " │   └─ ✓ sample-compressed.pdf",
        " │      30% smaller (9.8 KB → 6.8 KB)",
        " │   compress (ghostscript)",
        " │   → sample-compressed.pdf",
        " │   └─ ⚠ sample-compressed.pdf: couldn't make it smaller — kept it as it was",
        " │      not smaller (2.2 KB → 3.9 KB)",
    ]
    assert view._written == 2
