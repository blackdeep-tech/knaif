"""Media checks of the local-agents comparison (`scripts/agent_vs_knaif/local_compare.py`).

These grade the harder requests (a frame as a GIF, a 45° rotation, a timed cut, a removed middle,
a whole folder) on real files, so they need ffmpeg and live with the ffmpeg skill's tests, where
the binary is a declared dependency. Plan: docs/plans/2026-11-25-local-agents-comparison.md.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
HERE = REPO / "scripts" / "agent_vs_knaif"


def _load(name: str):
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HERE))


@pytest.fixture(scope="module")
def lc():
    return _load("local_compare")


needs_ffmpeg = pytest.mark.skipif(
    not (__import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)


def _ff(*args):
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def src_clip(tmp_path_factory):
    """6 s, 30 fps, moving test pattern with a frame counter, and a tone."""
    if not (__import__("shutil").which("ffmpeg")):
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path_factory.mktemp("src") / "clip.mp4"
    _ff(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p),
    )  # fmt: skip
    return p


def _work(tmp_path, src):
    d = tmp_path / "run"
    d.mkdir()
    (d / "clip.mp4").write_bytes(src.read_bytes())
    return d


@needs_ffmpeg
def test_rotation_check_tells_the_direction(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {"rotated": {"degrees": 45, "direction": "ccw"}, "dur_approx": 6.0}
    _ff("-i", str(d / "clip.mp4"), "-vf", "rotate=-PI/4", str(d / "ccw.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"
    (d / "ccw.mp4").unlink()
    _ff("-i", str(d / "clip.mp4"), "-vf", "rotate=PI/4", str(d / "cw.mp4"))
    g = lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")
    assert g["result"] == "FAIL", g


@needs_ffmpeg
def test_rotation_check_quarter_turn_tells_the_direction(lc, tmp_path, src_clip):
    """90° turns change the frame's shape (16:9 to 9:16); ccw is transpose=2, cw transpose=1."""
    d = _work(tmp_path, src_clip)
    exp = {"rotated": {"degrees": 90, "direction": "ccw"}, "dur_approx": 6.0}
    _ff("-i", str(d / "clip.mp4"), "-vf", "transpose=2", str(d / "ccw.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"
    (d / "ccw.mp4").unlink()
    _ff("-i", str(d / "clip.mp4"), "-vf", "transpose=1", str(d / "cw.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"
    (d / "cw.mp4").unlink()
    _ff("-i", str(d / "clip.mp4"), "-c", "copy", str(d / "same.mp4"))  # not turned at all
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"


@needs_ffmpeg
def test_rotation_check_accepts_an_expanded_canvas(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {"rotated": {"degrees": 45, "direction": "ccw"}}
    vf = "rotate=-PI/4:ow=rotw(-PI/4):oh=roth(-PI/4)"
    _ff("-i", str(d / "clip.mp4"), "-vf", vf, str(d / "ccw_big.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"


@needs_ffmpeg
def test_timed_cut_checks_name_length_and_start(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {"name": "cut.mp4", "dur_approx": 1.75, "dur_tol": 0.25, "starts_at": 2.5}
    _ff("-ss", "2.5", "-i", str(d / "clip.mp4"), "-t", "1.75", str(d / "cut.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"
    _ff("-i", str(d / "clip.mp4"), "-t", "1.75", str(d / "cut.mp4"))  # from 0: wrong start
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"


@needs_ffmpeg
def test_a_video_request_answered_with_sound_only_says_so(lc, tmp_path, src_clip):
    """OpenCode wrote an audio-only `cut.mp4` (2026-11-27): the grade names the missing video
    instead of reporting frame similarities of 0."""
    d = _work(tmp_path, src_clip)
    exp = {"name": "cut.mp4", "dur_approx": 1.75, "dur_tol": 0.25, "starts_at": 2.5}
    _ff("-ss", "2.5", "-i", str(d / "clip.mp4"), "-t", "1.75", "-vn", str(d / "cut.mp4"))
    g = lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")
    assert g["result"] == "FAIL"
    assert g["failed"] == ["no video stream"]


@needs_ffmpeg
def test_timed_cut_needs_the_requested_name(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {"name": "cut.mp4", "dur_approx": 1.75, "dur_tol": 0.25}
    _ff("-ss", "2.5", "-i", str(d / "clip.mp4"), "-t", "1.75", str(d / "clip_cut.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "no-output"


@needs_ffmpeg
def test_removed_middle_is_checked_on_the_content(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {
        "dur_approx": 4.0,
        "dur_tol": 0.3,
        "has_audio": True,
        "content_at": {"out_t": 2.3, "src_t": 4.3, "not_src_t": 2.3},
    }
    s = str(d / "clip.mp4")
    good = (
        "[0:v]trim=0:2,setpts=PTS-STARTPTS[a];[0:v]trim=4:6,setpts=PTS-STARTPTS[b];"
        "[0:a]atrim=0:2,asetpts=PTS-STARTPTS[x];[0:a]atrim=4:6,asetpts=PTS-STARTPTS[y];"
        "[a][x][b][y]concat=n=2:v=1:a=1[v][o]"
    )
    _ff("-i", s, "-filter_complex", good, "-map", "[v]", "-map", "[o]", str(d / "kept.mp4"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"
    (d / "kept.mp4").unlink()
    _ff("-i", s, "-t", "4", str(d / "first4.mp4"))  # right length, wrong content
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"


@needs_ffmpeg
def test_frame_gif_is_one_frame_from_the_start(lc, tmp_path, src_clip):
    d = _work(tmp_path, src_clip)
    exp = {"vcodec": "gif", "frames": 1, "frame_like": {"src_index": 3}}
    s = str(d / "clip.mp4")
    _ff("-i", s, "-vf", r"select=eq(n\,3)", "-frames:v", "1", str(d / "frame4.gif"))
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "PASS"
    (d / "frame4.gif").unlink()
    _ff("-i", s, "-t", "1", str(d / "anim.gif"))  # many frames
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"
    (d / "anim.gif").unlink()
    _ff("-ss", "4", "-i", s, "-frames:v", "1", str(d / "late.gif"))  # one frame, wrong place
    assert lc.grade_video(exp, d, "clip.mp4", d / "clip.mp4")["result"] == "FAIL"


@needs_ffmpeg
def test_batch_video_needs_every_file_and_every_original(lc, tmp_path, src_clip):
    d = tmp_path / "run"
    d.mkdir()
    for name in ("beach.mov", "garden.mkv", "dog.mp4"):
        _ff("-i", str(src_clip), "-t", "1", str(d / name))
    inputs = lc.snapshot_inputs(d)
    spec = {"to": "mp4", "from_ext": [".mov", ".mkv"]}
    for stem in ("beach", "garden"):
        _ff("-i", str(d / f"{stem}{'.mov' if stem == 'beach' else '.mkv'}"), str(d / f"{stem}.mp4"))
    g = lc.grade_batch_video(spec, d, inputs)
    assert g["result"] == "PASS", g
    (d / "garden.mp4").unlink()
    assert lc.grade_batch_video(spec, d, inputs)["result"] == "FAIL"
    _ff("-i", str(d / "garden.mkv"), str(d / "garden_converted.mp4"))
    (d / "dog.mp4").unlink()  # an original gone
    g = lc.grade_batch_video(spec, d, inputs)
    assert g["result"] == "FAIL"
    assert g["source_intact"] is False
