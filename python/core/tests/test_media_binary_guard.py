"""The core suite's ffmpeg/ffprobe guard is itself under test.

``_no_media_binaries`` (conftest) is autouse and invisible at the call site, so
without these two tests it could be deleted or narrowed and the suite would go
on passing — on any machine that has ffmpeg installed. That is precisely the
failure mode it exists to remove, so the guard gets a test of its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("binary", ["ffprobe", "ffmpeg"])
def test_media_binaries_are_refused(binary: str):
    """A core test reaching for a media binary fails loudly, installed or not."""
    with pytest.raises(RuntimeError, match=binary):
        subprocess.run([binary, "-version"], capture_output=True)


def test_absolute_paths_do_not_slip_past(tmp_path: Path):
    """The guard matches on the program's stem, so a full path is caught too."""
    with pytest.raises(RuntimeError, match="ffprobe"):
        subprocess.run([str(tmp_path / "bin" / "ffprobe.exe"), "-version"])


def test_other_binaries_still_run():
    """Only ffmpeg/ffprobe are guarded — the rest of subprocess is untouched."""
    result = subprocess.run(
        ["python", "-c", "print('ok')"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "ok"
