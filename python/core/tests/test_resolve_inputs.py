"""Unit tests for knaif.steps.ResolveInputs.

Tests are standalone (no skill dir, no ffmpeg). Behaviour must be
case-for-case identical to ffmpeg's cmd_resolve_inputs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knaif.steps import ResolveInputs
from knaif.steps._resolve_inputs import _assert_in_sandbox

# ── helpers ───────────────────────────────────────────────────────────────────


def _ctx(sandbox: Path | None = None, root: Path | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.sandbox = sandbox
    ctx.root = root or Path.cwd()
    return ctx


def _step() -> ResolveInputs:
    return ResolveInputs()


# ── _assert_in_sandbox ────────────────────────────────────────────────────────


def test_assert_in_sandbox_passes_when_inside(tmp_path):
    child = tmp_path / "a.mp4"
    _assert_in_sandbox(child, tmp_path)  # no exception


def test_assert_in_sandbox_passes_when_no_sandbox(tmp_path):
    _assert_in_sandbox(tmp_path / "anything", None)  # no exception


def test_assert_in_sandbox_raises_when_outside(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.mp4"
    with pytest.raises(ValueError, match="outside the sandbox"):
        _assert_in_sandbox(outside, sandbox)


# ── ResolveInputs.handle ──────────────────────────────────────────────────────


def test_resolve_existing_file(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"")
    result = _step().handle({"paths": [str(f)]}, _ctx())
    assert result == {"count": 1, "files": [str(f)]}


def test_resolve_missing_file_passes_through(tmp_path):
    ghost = str(tmp_path / "ghost.mp4")
    result = _step().handle({"paths": [ghost]}, _ctx())
    assert result == {"count": 1, "files": [ghost]}


def test_resolve_single_string_path(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"")
    result = _step().handle({"paths": str(f)}, _ctx())
    assert result["count"] == 1


def test_resolve_directory_lists_files(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"")
    result = _step().handle({"paths": [str(tmp_path)]}, _ctx())
    assert result["count"] == 2
    assert all(p.endswith(".mp4") for p in result["files"])


def test_resolve_directory_with_extension_filter(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.jpg").write_bytes(b"")
    result = _step().handle({"paths": [str(tmp_path)], "extensions": ["mp4"]}, _ctx())
    assert result["count"] == 1
    assert result["files"][0].endswith(".mp4")


def test_resolve_glob_pattern(tmp_path):
    (tmp_path / "clip1.mp4").write_bytes(b"")
    (tmp_path / "clip2.mp4").write_bytes(b"")
    (tmp_path / "image.jpg").write_bytes(b"")
    pattern = str(tmp_path / "*.mp4")
    result = _step().handle({"paths": [pattern]}, _ctx())
    assert result["count"] == 2


def test_relative_path_resolved_against_sandbox(tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    f = sandbox / "clip.mp4"
    f.write_bytes(b"")
    result = _step().handle({"paths": ["clip.mp4"]}, _ctx(sandbox=sandbox))
    assert result["count"] == 1
    assert result["files"][0] == str(f)


def test_relative_path_resolved_against_root_when_no_sandbox(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"")
    result = _step().handle({"paths": ["clip.mp4"]}, _ctx(root=tmp_path))
    assert result["count"] == 1


def test_sandbox_escape_raises(tmp_path):
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"")
    with pytest.raises(ValueError, match="outside the sandbox"):
        _step().handle({"paths": [str(outside)]}, _ctx(sandbox=sandbox))


def test_empty_paths_returns_zero(tmp_path):
    result = _step().handle({"paths": []}, _ctx())
    assert result == {"count": 0, "files": []}


def test_result_dict_shape():
    result = _step().handle({"paths": []}, _ctx())
    assert set(result.keys()) == {"count", "files"}
    assert isinstance(result["count"], int)
    assert isinstance(result["files"], list)


# ── discovery contract ({count, files} accepted as list-or-dict) ──────────────


def test_result_dict_accepted_as_list_or_dict(tmp_path):
    """Downstream consumers must handle both bare list and {count,files} dict."""
    f = tmp_path / "a.mp4"
    f.write_bytes(b"")
    result = _step().handle({"paths": [str(f)]}, _ctx())

    # Consumer pattern (mirrors inspect_media lines 937-938)
    files = result
    if isinstance(files, dict) and "files" in files:
        files = files["files"]
    assert isinstance(files, list)
    assert str(f) in files
