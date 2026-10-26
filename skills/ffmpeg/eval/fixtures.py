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
    # A long clip for one-file-into-many rows: 10-second parts, a frame every 5 s, and
    # screenshots at 0:05, 0:30 and 1:10 all need more than the 10 s of clip.mp4.
    "clip_long.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=75:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=660:duration=75"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -shortest"
        " {output}"
    ),
    # The folder for folder rows ("convert all the .mov files here ..."), copied whole into each
    # row's own folder. Names are distinct from every single-file fixture, and the folder is a
    # subfolder, so a single-file row (which lists its folder non-recursively) never sees it.
    # Five short clips in three containers, one with no audio (an extract-audio step fails on
    # it while the others finish), and one long 1080p clip.
    "batch/beach.mov": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=8:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=440:duration=8"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "batch/city.mov": (
        "ffmpeg -y"
        " -f lavfi -i testsrc2=duration=6:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=550:duration=6"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "batch/dog.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=7:size=1280x720:rate=30"
        " -f lavfi -i sine=frequency=330:duration=7"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "batch/party.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc2=duration=5:size=1280x720:rate=30"
        " -f lavfi -i sine=frequency=770:duration=5"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "batch/garden.mkv": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=6:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=880:duration=6"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "batch/silent.mov": (
        "ffmpeg -y"
        " -f lavfi -i testsrc2=duration=6:size=1280x720:rate=25"
        " -an -c:v libx264 -crf 23"
        " {output}"
    ),
    "batch/concert.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=60:size=1920x1080:rate=30"
        " -f lavfi -i sine=frequency=500:duration=60"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -shortest"
        " {output}"
    ),
    # One-file folders for folder rows that name a single file: the folder holds only that file,
    # so "this video" has one answer and every file the run leaves can be graded. Same streams as
    # the root fixtures of the same name; one_long/clip.mp4 is the 75 s clip ("every 10 seconds").
    "one_clip/clip.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30"
        " -f lavfi -i sine=frequency=1000:duration=10"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "one_mov/clip1.mov": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=8:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=440:duration=8"
        " -c:v libx264 -crf 23 -c:a aac -shortest"
        " {output}"
    ),
    "one_long/clip.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=75:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=660:duration=75"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -shortest"
        " {output}"
    ),
    # "merge all the clips in this folder into one": numbered so that natural order
    # (clip1, clip2, clip10) differs from text order; matching streams, so the join is plain.
    "clips/clip1.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc=duration=2:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=300:duration=2"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -ar 44100 -shortest"
        " {output}"
    ),
    "clips/clip2.mp4": (
        "ffmpeg -y"
        " -f lavfi -i testsrc2=duration=3:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=500:duration=3"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -ar 44100 -shortest"
        " {output}"
    ),
    "clips/clip10.mp4": (
        "ffmpeg -y"
        " -f lavfi -i smptebars=duration=4:size=1280x720:rate=25"
        " -f lavfi -i sine=frequency=700:duration=4"
        " -c:v libx264 -crf 28 -preset veryfast -c:a aac -ar 44100 -shortest"
        " {output}"
    ),
}

# FIXTURE_EXTENSIONS removed — the extension is part of each key above.
# Kept as empty dict for any callers that still unpack the old two-tuple return.
FIXTURE_EXTENSIONS: dict[str, str] = {}
