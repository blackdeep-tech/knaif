"""lavfi-based fixture definitions for the ffmpeg v2 eval corpus.

Each key is the exact filename the fixture produces (and what corpus utterances
reference).  The value is a full ffmpeg command with `{output}` as the output
path placeholder.  Extension is embedded in the key — FIXTURE_EXTENSIONS is not
needed.  Execution and caching live in `evalsuite fixtures regen --skill ffmpeg`.
No subprocess calls here — pure data.
"""

from __future__ import annotations

FIXTURES: dict[str, str] = {
    "clip.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30"
        " -f lavfi -i sine=frequency=1000:duration=10"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    # Counter clip: 10fps × 10s = 100 frames, testsrc shows built-in 0–99 counter.
    # CBR at 1200k forces ~1.6 MB regardless of how well the content compresses.
    # Use this fixture only for size-constrained tests (e.g. "compress to under 1 MB").
    "clip_ctr.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=10:size=1920x1080:rate=10"
        " -f lavfi -i sine=frequency=1000:duration=10"
        " -c:v libx264 -b:v 1200k -minrate:v 1200k -maxrate:v 1200k -bufsize:v 600k"
        " -x264-params nal-hrd=cbr"
        " -c:a aac -b:a 128k -shortest"
        " {output}"
    ),
    "clip_no_audio.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=10:size=1280x720:rate=25"
        " -an -c:v libx264 -crf 23"
        " {output}"
    ),
    "clip_4k.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=5:size=3840x2160:rate=30"
        " -f lavfi -i sine=frequency=1000:duration=5"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "clip.mov": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=8:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=440:duration=8"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    # Second 1080p/30fps clip for concat tests that require matching stream parameters.
    "clip2.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc2=duration=8:size=1920x1080:rate=30"
        " -f lavfi -i sine=frequency=500:duration=8"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "audio.mp3": (
        "ffmpeg -y -f lavfi -i sine=frequency=440:duration=10" " -c:a libmp3lame -q:a 2 {output}"
    ),
}

# FIXTURE_EXTENSIONS removed — the extension is part of each key above.
# Kept as empty dict for any callers that still unpack the old two-tuple return.
FIXTURE_EXTENSIONS: dict[str, str] = {}
