"""The fs toolkit's write tools: stage an output in the run scratch, then commit it.

`stage_output` hands out a path in `ctx.work_dir` with the final file's name (so a tool that
reads the format from the extension still sees it). `commit_output` checks the staged file and
moves it to its final place; a failure leaves nothing in the user's folder. `ensure_parent`
makes a destination folder, and `plan_output_path` is the one free-name rule, its suffix and
numbering decided by the calling skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.handler_api import HandlerContext
from toolkits.fs.python.write import (
    CommitOutput,
    EnsureParent,
    PlanOutputPath,
    StageCopy,
    StageOutput,
    UserSidePath,
)


def _ctx(tmp_path: Path, *, dry_run: bool = False) -> HandlerContext:
    sandbox, work = tmp_path / "sb", tmp_path / "work"
    sandbox.mkdir(exist_ok=True)
    if not dry_run:
        work.mkdir(exist_ok=True)
    return HandlerContext(
        root=sandbox,
        dry_run=dry_run,
        confirmed=True,
        skill_dir=tmp_path,
        sandbox=sandbox,
        work_dir=work,
    )


# ── stage_output ───────────────────────────────────────────────────────────────────────────


def test_a_staged_path_is_in_scratch_and_keeps_the_final_name(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    staged = Path(StageOutput().handle({"path": str(ctx.sandbox / "out.mkv")}, ctx)["staged"])
    assert ctx.work_dir in staged.parents
    assert staged.name == "out.mkv"
    assert staged.parent.is_dir()


def test_two_stages_of_one_name_never_share_a_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    a = StageOutput().handle({"path": str(ctx.sandbox / "a" / "x.mp4")}, ctx)["staged"]
    b = StageOutput().handle({"path": str(ctx.sandbox / "b" / "x.mp4")}, ctx)["staged"]
    assert a != b


def test_a_dry_run_stage_creates_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, dry_run=True)
    staged = StageOutput().handle({"path": "out.mp4"}, ctx)["staged"]
    assert Path(staged).name == "out.mp4"
    assert not ctx.work_dir.exists()


# ── stage_copy ─────────────────────────────────────────────────────────────────────────────


def test_a_staged_copy_of_a_scratch_file_commits_like_any_staged_file(tmp_path: Path) -> None:
    """A chain intermediate a compress could not shrink is delivered as it was (2026-10-28)."""
    ctx = _ctx(tmp_path)
    source = ctx.work_dir / "clip.mp4"
    source.write_bytes(b"frames")
    staged = Path(
        StageCopy().handle({"source": str(source), "path": "clip_compressed.mp4"}, ctx)["staged"]
    )
    assert ctx.work_dir in staged.parents
    assert staged.name == "clip_compressed.mp4"
    assert staged.read_bytes() == b"frames"
    assert source.read_bytes() == b"frames", "a copy: another step may still read the source"


def test_a_staged_copy_only_copies_from_the_scratch(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    users = ctx.sandbox / "clip.mp4"
    users.write_bytes(b"frames")
    with pytest.raises(ValueError, match="run scratch"):
        StageCopy().handle({"source": str(users), "path": "out.mp4"}, ctx)


# ── commit_output ──────────────────────────────────────────────────────────────────────────


def test_commit_moves_the_staged_file_into_place(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    staged = Path(StageOutput().handle({"path": "videos/out.mp4"}, ctx)["staged"])
    staged.write_bytes(b"frames")
    out = CommitOutput().handle({"staged": str(staged), "path": "videos/out.mp4"}, ctx)
    final = ctx.sandbox / "videos" / "out.mp4"
    assert Path(out["path"]) == final.resolve()
    assert final.read_bytes() == b"frames"
    assert not staged.exists()


def test_commit_replaces_a_file_the_user_named(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.sandbox / "out.mp4").write_bytes(b"old")
    staged = Path(StageOutput().handle({"path": "out.mp4"}, ctx)["staged"])
    staged.write_bytes(b"new")
    CommitOutput().handle({"staged": str(staged), "path": "out.mp4"}, ctx)
    assert (ctx.sandbox / "out.mp4").read_bytes() == b"new"


@pytest.mark.parametrize("content", [None, b""], ids=["missing", "empty"])
def test_an_empty_or_missing_output_is_refused_and_nothing_lands(tmp_path, content) -> None:
    ctx = _ctx(tmp_path)
    staged = Path(StageOutput().handle({"path": "out.mp4"}, ctx)["staged"])
    if content is not None:
        staged.write_bytes(content)
    with pytest.raises(ValueError, match="out.mp4"):
        CommitOutput().handle({"staged": str(staged), "path": "out.mp4"}, ctx)
    assert not (ctx.sandbox / "out.mp4").exists()
    assert not staged.exists()


def test_a_final_output_can_never_land_in_scratch(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    staged = Path(StageOutput().handle({"path": "out.mp4"}, ctx)["staged"])
    staged.write_bytes(b"x")
    with pytest.raises(ValueError, match="scratch"):
        CommitOutput().handle({"staged": str(staged), "path": str(ctx.work_dir / "o.mp4")}, ctx)


def test_a_final_output_outside_the_sandbox_is_refused(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    staged = Path(StageOutput().handle({"path": "out.mp4"}, ctx)["staged"])
    staged.write_bytes(b"x")
    with pytest.raises(ValueError, match="outside the sandbox"):
        CommitOutput().handle({"staged": str(staged), "path": str(tmp_path / "elsewhere.mp4")}, ctx)


def test_a_dry_run_commit_touches_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, dry_run=True)
    out = CommitOutput().handle({"staged": str(ctx.work_dir / "x.mp4"), "path": "out.mp4"}, ctx)
    assert out["mode"] == "dry_run"
    assert not (ctx.sandbox / "out.mp4").exists()


# ── ensure_parent ──────────────────────────────────────────────────────────────────────────


def test_ensure_parent_makes_the_destination_folder(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    EnsureParent().handle({"path": "exports/hevc/clip.mkv"}, ctx)
    assert (ctx.sandbox / "exports" / "hevc").is_dir()


def test_ensure_parent_creates_the_resolved_parent_only(tmp_path: Path) -> None:
    """`../escaped/../sb/out.mp4` resolves inside the sandbox; walking the raw spelling would
    create `../escaped` on POSIX first (the rule ffmpeg's run_batch already followed)."""
    ctx = _ctx(tmp_path)
    EnsureParent().handle({"path": "../escaped/../sb/new/out.mp4"}, ctx)
    assert (ctx.sandbox / "new").is_dir()
    assert not (tmp_path / "escaped").exists()


def test_ensure_parent_refuses_a_path_outside_the_sandbox(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the sandbox"):
        EnsureParent().handle({"path": str(tmp_path / "x" / "y.mp4")}, _ctx(tmp_path))


def test_ensure_parent_in_a_dry_run_creates_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, dry_run=True)
    EnsureParent().handle({"path": "exports/clip.mkv"}, ctx)
    assert not (ctx.sandbox / "exports").exists()


# ── plan_output_path ───────────────────────────────────────────────────────────────────────


def test_a_free_name_is_kept_when_the_skill_allows_it(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    got = PlanOutputPath().handle({"path": "notes.md", "keep_requested": True}, ctx)["path"]
    assert Path(got).name == "notes.md"


def test_documents_style_numbering(tmp_path: Path) -> None:
    """documents: the name itself if free, else `<stem>-1`, `-2`, …"""
    ctx = _ctx(tmp_path)
    (ctx.sandbox / "notes.md").write_bytes(b"x")
    (ctx.sandbox / "notes-1.md").write_bytes(b"x")
    args = {"path": "notes.md", "keep_requested": True, "separator": "-", "first_number": 1}
    assert Path(PlanOutputPath().handle(args, ctx)["path"]).name == "notes-2.md"


def test_ffmpeg_style_numbering_never_returns_the_requested_name(tmp_path: Path) -> None:
    """ffmpeg's collision repair: `<stem>_converted`, then `_converted_2`, … never the name."""
    ctx = _ctx(tmp_path)
    args = {
        "path": "clip.mp4",
        "suffix": "_converted",
        "separator": "_",
        "first_number": 2,
        "taken": [str(ctx.sandbox / "clip_converted.mp4")],
    }
    assert Path(PlanOutputPath().handle(args, ctx)["path"]).name == "clip_converted_2.mp4"


# ── user_side_path ─────────────────────────────────────────────────────────────────────────


def test_a_scratch_path_maps_back_to_its_place_in_the_users_folder(tmp_path: Path) -> None:
    """Routing keeps an intermediate's place (`docs/a.pdf` -> `<work>/docs/a.pdf`), so a default
    output derived from it goes where the intermediate would have been, never into scratch."""
    ctx = _ctx(tmp_path)
    got = UserSidePath().handle({"path": str(ctx.work_dir / "docs" / "a.pdf")}, ctx)["path"]
    assert Path(got) == (ctx.sandbox / "docs" / "a.pdf").resolve()


def test_a_path_outside_the_scratch_is_returned_as_it_is(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    got = UserSidePath().handle({"path": str(ctx.sandbox / "a.pdf")}, ctx)["path"]
    assert Path(got) == (ctx.sandbox / "a.pdf").resolve()
