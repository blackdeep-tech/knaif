"""Integration tests for `evalsuite fixtures regen --skill ffmpeg`.

These tests mock subprocess so ffmpeg doesn't need to be on PATH.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from knaif.evalsuite.cli import main


def _run(args: list[str]) -> None:
    """Run cli main() with the given argv, raising SystemExit on failure."""
    import sys

    old_argv = sys.argv
    sys.argv = ["knaif.evalsuite"] + args
    try:
        main()
    finally:
        sys.argv = old_argv


def _completed(returncode: int = 0, stderr: str = "") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stderr = stderr
    return m


# ── fixtures regen ────────────────────────────────────────────────────────


def test_fixtures_regen_creates_output_files(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    with patch("subprocess.run", return_value=_completed()) as mock_run:
        _run(["fixtures", "regen", "--skill", "ffmpeg", "--sandbox", str(sandbox)])

    fixture_dir = sandbox / "fixtures" / "ffmpeg"
    assert fixture_dir.exists()
    # subprocess.run called once per fixture
    from skills.ffmpeg.eval.fixtures import FIXTURES

    assert mock_run.call_count == len(FIXTURES)


def test_fixtures_regen_skips_cached_fixture(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    fixture_dir = sandbox / "fixtures" / "ffmpeg"
    fixture_dir.mkdir(parents=True)

    from skills.ffmpeg.eval.fixtures import FIXTURES

    cache_path = fixture_dir / ".cache.json"

    # Pre-populate cache with first fixture's SHA
    first_name = next(iter(FIXTURES))
    first_cmd = FIXTURES[first_name].replace("{output}", str(fixture_dir / first_name))

    import hashlib

    sha = hashlib.sha256(first_cmd.encode()).hexdigest()
    cache_path.write_text(json.dumps({first_name: sha}), encoding="utf-8")
    (fixture_dir / first_name).write_bytes(b"fake")  # exists

    with patch("subprocess.run", return_value=_completed()) as mock_run:
        _run(["fixtures", "regen", "--skill", "ffmpeg", "--sandbox", str(sandbox)])

    # Only the non-cached fixtures should be regenerated
    assert mock_run.call_count == len(FIXTURES) - 1


def test_fixtures_regen_force_reruns_all(tmp_path: Path):
    sandbox = tmp_path / "sandbox"
    fixture_dir = sandbox / "fixtures" / "ffmpeg"
    fixture_dir.mkdir(parents=True)

    from skills.ffmpeg.eval.fixtures import FIXTURES

    cache_path = fixture_dir / ".cache.json"

    # Pre-populate cache for all fixtures
    cache: dict[str, str] = {}
    for name, cmd in FIXTURES.items():
        full_cmd = cmd.replace("{output}", str(fixture_dir / name))
        import hashlib

        cache[name] = hashlib.sha256(full_cmd.encode()).hexdigest()
        (fixture_dir / name).write_bytes(b"fake")
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    with patch("subprocess.run", return_value=_completed()) as mock_run:
        _run(["fixtures", "regen", "--skill", "ffmpeg", "--sandbox", str(sandbox), "--force"])

    assert mock_run.call_count == len(FIXTURES)


def test_fixtures_regen_failed_ffmpeg_prints_warning(tmp_path: Path, capsys):
    sandbox = tmp_path / "sandbox"
    with patch("subprocess.run", return_value=_completed(returncode=1, stderr="codec not found")):
        _run(["fixtures", "regen", "--skill", "ffmpeg", "--sandbox", str(sandbox)])

    captured = capsys.readouterr()
    assert "Warning" in captured.out or "warning" in captured.out.lower()


def test_fixtures_regen_missing_ffmpeg_prints_warning(tmp_path: Path, capsys):
    sandbox = tmp_path / "sandbox"
    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        _run(["fixtures", "regen", "--skill", "ffmpeg", "--sandbox", str(sandbox)])

    captured = capsys.readouterr()
    assert "ffmpeg" in captured.out.lower()
