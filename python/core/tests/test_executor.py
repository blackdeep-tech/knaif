"""Tests for knaif.executor."""

from __future__ import annotations

import sys

from knaif.executor import (
    cmd_delete_files,
    cmd_find_files,
    cmd_list_files,
    cmd_move_files,
)
from knaif.handler_api import HandlerContext


def _ctx(sandbox, tmp_path, *, dry_run: bool = True, confirmed: bool = False) -> HandlerContext:
    return HandlerContext(
        root=tmp_path,
        sandbox=sandbox,
        dry_run=dry_run,
        confirmed=confirmed,
        skill_dir=tmp_path,
    )


# ── cmd_list_files ────────────────────────────────────────────────────────────


def test_list_files_counts_all(sandbox, tmp_path):
    result = cmd_list_files({"path": str(sandbox / "reports")}, _ctx(sandbox, tmp_path))
    assert result["count"] == 2
    names = [p.split("\\")[-1].split("/")[-1] for p in result["files"]]
    assert "jan_report.txt" in names
    assert "feb_report.txt" in names


def test_list_files_pattern_filters(sandbox, tmp_path):
    result = cmd_list_files(
        {"path": str(sandbox / "reports"), "pattern": "jan*"},
        _ctx(sandbox, tmp_path),
    )
    assert result["count"] == 1


def test_list_files_nonexistent_dir_returns_zero(sandbox, tmp_path):
    result = cmd_list_files({"path": str(sandbox / "nonexistent")}, _ctx(sandbox, tmp_path))
    assert result["count"] == 0


def test_list_files_not_recursive(sandbox, tmp_path):
    result = cmd_list_files({"path": str(sandbox)}, _ctx(sandbox, tmp_path))
    assert result["count"] == 0


# ── cmd_find_files ────────────────────────────────────────────────────────────


def test_find_files_txt(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "pattern": "*.txt"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 2


def test_find_files_log(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "pattern": "*.log"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 1


def test_find_files_tmp(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "pattern": "*.tmp"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 1


def test_find_files_no_match(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "pattern": "*.pdf"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 0


# ── cmd_delete_files ──────────────────────────────────────────────────────────


def test_delete_files_dry_run_does_not_delete(sandbox, tmp_path):
    result = cmd_delete_files(
        {"path": str(sandbox / "tmp"), "pattern": "*.tmp"},
        _ctx(sandbox, tmp_path, dry_run=True),
    )
    assert result["mode"] == "dry_run"
    assert result["would_delete_count"] == 1
    assert (sandbox / "tmp" / "cache.tmp").exists()


def test_delete_files_execute_removes_file(sandbox, tmp_path):
    result = cmd_delete_files(
        {"path": str(sandbox / "tmp"), "pattern": "*.tmp"},
        _ctx(sandbox, tmp_path, dry_run=False),
    )
    assert result["mode"] == "execute"
    assert result["deleted_count"] == 1
    assert result["errors"] == []
    assert not (sandbox / "tmp" / "cache.tmp").exists()


def test_delete_files_recursive(sandbox, tmp_path):
    result = cmd_delete_files(
        {"path": str(sandbox), "pattern": "*.txt", "recursive": True},
        _ctx(sandbox, tmp_path, dry_run=True),
    )
    assert result["would_delete_count"] == 2


def test_delete_files_no_match(sandbox, tmp_path):
    result = cmd_delete_files(
        {"path": str(sandbox / "tmp"), "pattern": "*.pdf"},
        _ctx(sandbox, tmp_path, dry_run=True),
    )
    assert result["would_delete_count"] == 0


# ── cmd_move_files ────────────────────────────────────────────────────────────


def test_move_files_dry_run(sandbox, tmp_path):
    result = cmd_move_files(
        {
            "src": str(sandbox / "tmp"),
            "dst": str(sandbox / "reports"),
            "pattern": "*.log",
        },
        _ctx(sandbox, tmp_path, dry_run=True),
    )
    assert result["mode"] == "dry_run"
    assert result["would_move_count"] == 1
    assert (sandbox / "tmp" / "old.log").exists()


def test_move_files_execute(sandbox, tmp_path):
    result = cmd_move_files(
        {
            "src": str(sandbox / "tmp"),
            "dst": str(sandbox / "reports"),
            "pattern": "*.log",
        },
        _ctx(sandbox, tmp_path, dry_run=False),
    )
    assert result["mode"] == "execute"
    assert result["moved_count"] == 1
    assert result["errors"] == []
    assert not (sandbox / "tmp" / "old.log").exists()
    assert (sandbox / "reports" / "old.log").exists()


def test_move_files_creates_dst(sandbox, tmp_path):
    new_dir = sandbox / "archive"
    assert not new_dir.exists()
    cmd_move_files(
        {
            "src": str(sandbox / "tmp"),
            "dst": str(new_dir),
            "pattern": "*.log",
        },
        _ctx(sandbox, tmp_path, dry_run=False),
    )
    assert new_dir.exists()


# ── file_type filtering ───────────────────────────────────────────────────────


def test_list_files_file_type_text(sandbox, tmp_path):
    result = cmd_list_files(
        {"path": str(sandbox / "reports"), "file_type": "text"}, _ctx(sandbox, tmp_path)
    )
    assert result["count"] == 2


def test_find_files_file_type_text(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "file_type": "text"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 2


def test_find_files_file_type_log(sandbox, tmp_path):
    result = cmd_find_files({"path": str(sandbox), "file_type": "log"}, _ctx(sandbox, tmp_path))
    assert result["count"] == 1


def test_find_files_file_type_executable(sandbox, tmp_path):
    result = cmd_find_files(
        {"path": str(sandbox), "file_type": "executable"}, _ctx(sandbox, tmp_path)
    )
    names = {p.split("\\")[-1].split("/")[-1] for p in result["files"]}
    if sys.platform == "win32":
        assert "app.exe" in names
    else:
        assert "script.sh" in names


def test_delete_files_file_type_dry_run(sandbox, tmp_path):
    result = cmd_delete_files(
        {"path": str(sandbox / "tmp"), "file_type": "log"},
        _ctx(sandbox, tmp_path, dry_run=True),
    )
    assert result["would_delete_count"] == 1


# Core control tools (clarify / reject / done / wait_for_confirmation) are defined as
# Step classes in core_tools.py and unit-tested in tests/test_core_tools.py. The
# executor's CORE_HANDLERS now derives from those Steps, so there are no duplicate
# cmd_* functions to test here.


def test_execute_plan_with_done_does_not_raise(sandbox, tmp_path):
    from pathlib import Path

    from knaif import CommandAgent

    skill_dir = Path("skills") / "io"
    agent = CommandAgent.from_skill(skill_dir, sandbox=sandbox, root=tmp_path)
    results = agent.execute_plan({"plan": [{"tool": "done", "args": {}}]})
    assert results[0]["result"] == {"status": "done"}
