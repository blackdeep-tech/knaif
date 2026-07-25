"""Regenerate balanced, diverse train.jsonl for ffmpeg + documents (fine-tuning Task 5).

Every emitted plan is checked twice: structurally against the live skill validator
(validate_plan) AND against canonical enum/profile VALUES (validate_plan only checks
arg keys, not values — e.g. an invalid platform would slip through and teach a wrong
mapping). Compound rows use literal intermediate filenames (never $var). Rows are kept
DISTINCT (files + ops) from the held-out Task-3 hard stratum. ZH/CJK excluded until the
CJK retrieval fix lands. Target ~350 rows/skill, weighted toward phrasing diversity.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Repo root: discovered from this file's location (scripts/ sits at the root),
# overridable with KNAIF_ROOT for out-of-tree runs.
ROOT = Path(os.environ.get("KNAIF_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "python" / "core"))

from knaif import CommandAgent  # noqa: E402

Row = tuple[str, dict, list[str]]


def P(*steps: dict) -> dict:
    return {"plan": list(steps)}


def step(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


def cyc(seq, i):
    return seq[i % len(seq)]


# ── canonical allowed values (validate_plan does NOT check these) ──
FF_PLATFORM = {"whatsapp", "email", "web", "youtube", "instagram_reels", "tiktok", "archive"}
FF_QUALITY = {
    "small_file",
    "balanced",
    "visually_good",
    "high_quality",
    "lossless",
    "best_possible",
}
FF_CONTAINER = {"mp4", "mkv", "webm", "gif", "mov", "avi"}
FF_AUDIO = {"mp3", "wav", "aac", "flac", "m4a", "ogg"}
FF_FIT = {"crop", "pad", "contain"}
FF_FLIP = {"horizontal", "vertical"}
FF_ANGLE = {90, 180, 270}
DOC_TOFORMAT = {"pdf", "txt", "md", "png", "jpg"}
DOC_CQUALITY = {"small", "balanced", "high"}
DOC_POSITION = {
    "top-left",
    "top-center",
    "top-right",
    "center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
}

VALUE_CHECKS = {
    "platform": FF_PLATFORM,
    "quality": FF_QUALITY,
    "container": FF_CONTAINER,
    "audio_format": FF_AUDIO,
    "fit": FF_FIT,
    "flip": FF_FLIP,
    "angle": FF_ANGLE,
    "to_format": DOC_TOFORMAT,
    "compress_quality": DOC_CQUALITY,
    "position": DOC_POSITION,
}


def value_errors(plan: dict) -> list[str]:
    errs = []
    for s in plan["plan"]:
        for k, v in s["args"].items():
            if k in VALUE_CHECKS and v not in VALUE_CHECKS[k]:
                errs.append(f"{s['tool']}.{k}={v!r} not in allowed")
    return errs


# ═════════════════════════ ffmpeg ═════════════════════════

FF = [
    "video.mp4",
    "movie.mov",
    "lecture.mp4",
    "webinar.mp4",
    "demo.mkv",
    "trailer.mp4",
    "gameplay.mp4",
    "vlog.mp4",
    "interview.mp4",
    "recording.mov",
    "footage.mov",
    "reel.mp4",
    "promo.mp4",
    "tutorial.mp4",
    "session.mp4",
    "scene.mp4",
    "take2.mp4",
    "raw.mov",
    "export.mp4",
    "broll.mov",
    "highlight.mp4",
    "cutdown.mp4",
    "master.mov",
    "episode.mp4",
    "stream.mkv",
    "podcast.mp4",
    "ad.mp4",
    "teaser.mp4",
    "montage.mp4",
    "screencast.mkv",
    "keynote.mp4",
    "render.mp4",
    "proxy.mov",
    "final.mp4",
    "dailies.mov",
]


def ffmpeg_rows() -> list[Row]:
    r: list[Row] = []

    # prepare_for_platform
    plats = [
        ("YouTube", "youtube"),
        ("Instagram Reels", "instagram_reels"),
        ("TikTok", "tiktok"),
        ("WhatsApp", "whatsapp"),
        ("the web", "web"),
        ("email", "email"),
        ("long-term archive", "archive"),
    ]
    pt = [
        "Make {f} ready for {label}.",
        "Prepare {f} for {label}.",
        "Get {f} set up for {label}.",
        "I need {f} suitable for {label}.",
        "Optimize {f} for {label}.",
        "{f} → {label}, please.",
    ]
    for i in range(26):
        f = cyc(FF, i)
        label, p = cyc(plats, i)
        a = {"inputs": [f], "platform": p, "preview": True}
        if i % 3 == 0:
            a["quality"] = cyc(["visually_good", "high_quality", "small_file"], i)
        r.append(
            (
                cyc(pt, i).format(f=f, label=label),
                P(step("prepare_for_platform", **a)),
                ["platform"],
            )
        )

    # compress_video
    ct = [
        "Compress {f}.",
        "Make {f} smaller.",
        "Shrink {f}.",
        "Reduce the size of {f}.",
        "Can you compress {f}?",
        "Squeeze {f} down.",
        "{f} is too big, compress it.",
        "Make {f} take up less space.",
    ]
    for i in range(34):
        f = cyc(FF, i + 1)
        a = {"inputs": [f]}
        if i % 4 == 1:
            a["target_size_mb"] = cyc([5, 8, 10, 15, 20, 25], i)
        elif i % 4 == 2:
            a["quality"] = cyc(["small_file", "balanced"], i)
        r.append((cyc(ct, i).format(f=f), P(step("compress_video", **a)), ["compress"]))
    r.append(
        (
            "Compress interview.mp4 for email under 20 MB.",
            P(
                step(
                    "compress_video",
                    inputs=["interview.mp4"],
                    target="email",
                    target_size_mb=20,
                    quality="best_possible",
                    preview=True,
                )
            ),
            ["compress", "email"],
        )
    )
    for i in range(7):
        f = cyc(FF[15:], i)
        r.append(
            (
                f"Get {f} under {cyc([6,12,18,24],i)} MB for email.",
                P(
                    step(
                        "compress_video",
                        inputs=[f],
                        target="email",
                        target_size_mb=cyc([6, 12, 18, 24], i),
                    )
                ),
                ["compress", "email"],
            )
        )

    # convert_video
    convt = [
        "Convert {f} to {c}.",
        "Turn {f} into a {c} file.",
        "Save {f} as {c}.",
        "Change {f} to {c} format.",
        "Re-encode {f} as {c}.",
        "{f} → {c}.",
    ]
    cont = ["mp4", "mkv", "webm", "mov", "avi"]
    for i in range(30):
        f = cyc(FF, i * 2)
        c = cyc(cont, i)
        r.append(
            (
                cyc(convt, i).format(f=f, c=c),
                P(step("convert_video", inputs=[f], container=c)),
                ["convert"],
            )
        )
    for i in range(4):
        ext = cyc(["mp4", "mov", "mkv", "avi"], i)
        tgt = cyc(["mkv", "mp4", "webm", "mp4"], i)
        r.append(
            (
                f"Bulk-convert every .{ext} in this folder to {tgt}.",
                P(step("convert_video", inputs=[f"*.{ext}"], container=tgt)),
                ["convert", "batch"],
            )
        )
    for i in range(4):
        f = cyc(["vlog.mp4", "promo.mp4", "teaser.mp4", "ad.mp4"], i)
        r.append(
            (
                f"Turn {f} into a gif.",
                P(step("convert_video", inputs=[f], container="gif")),
                ["convert", "gif"],
            )
        )

    # resize_video
    rt = [
        "Resize {f} to {h}p.",
        "Scale {f} down to {h}p.",
        "Make {f} {h}p.",
        "Downscale {f} to {h}p.",
        "Set {f} to {h}p, keep the aspect ratio.",
    ]
    for i in range(28):
        f = cyc(FF, i + 3)
        h = cyc([2160, 1440, 1080, 720, 480], i)
        r.append(
            (
                cyc(rt, i).format(f=f, h=h),
                P(step("resize_video", inputs=[f], height=h, keep_aspect_ratio=True)),
                ["resize"],
            )
        )
    for i in range(8):
        f = cyc(["reel.mp4", "tutorial.mp4", "promo.mp4", "teaser.mp4"], i)
        asp, word = cyc([("9:16", "a 9:16 portrait"), ("1:1", "square"), ("4:5", "a 4:5 post")], i)
        r.append(
            (
                f"Crop {f} to {word}.",
                P(step("resize_video", inputs=[f], fit="crop", aspect=asp)),
                ["resize", "crop"],
            )
        )

    # trim_video (single input)
    tt = [
        "Trim {f} to the first {n} seconds.",
        "Cut {f} down to {n}s.",
        "Keep only the first {n} seconds of {f}.",
        "Take the first {n} seconds of {f}.",
    ]
    for i in range(20):
        f = cyc(FF, i + 5)
        n = cyc([5, 10, 15, 20, 30, 45, 60], i)
        end = f"00:00:{n:02d}" if n < 60 else "00:01:00"
        r.append(
            (
                cyc(tt, i).format(f=f, n=n),
                P(step("trim_video", input=f, start="00:00:00", end=end)),
                ["trim"],
            )
        )
    for i in range(6):
        f = cyc(FF[10:], i)
        a, b = cyc([(10, 25), (30, 60), (5, 12), (0, 8)], i)
        r.append(
            (
                f"Cut {f} from {a}s to {b}s.",
                P(step("trim_video", input=f, start=f"00:00:{a:02d}", end=f"00:00:{b:02d}")),
                ["trim"],
            )
        )

    # extract_audio
    et = [
        "Extract the audio from {f} as {fmt}.",
        "Pull the audio out of {f} to {fmt}.",
        "Rip the soundtrack from {f} as {fmt}.",
        "Get the audio from {f} in {fmt}.",
        "Save {f}'s audio as {fmt}.",
    ]
    for i in range(26):
        f = cyc(FF, i * 2 + 1)
        fmt = cyc(["mp3", "wav", "aac", "flac"], i)
        r.append(
            (
                cyc(et, i).format(f=f, fmt=fmt),
                P(step("extract_audio", inputs=[f], audio_format=fmt)),
                ["audio", "extract"],
            )
        )

    # create_thumbnail (single input)
    tht = [
        "Grab a thumbnail from {f} at {t}.",
        "Take a screenshot of {f} at {t}.",
        "Capture a still from {f} at {t}.",
        "Get a frame of {f} at {t}.",
    ]
    for i in range(18):
        f = cyc(FF, i + 7)
        t = cyc(["00:00:02", "00:00:05", "00:00:10", "00:00:30", "00:01:00", "00:02:15"], i)
        a = {"input": f, "at_time": t}
        if i % 2:
            a["image_format"] = cyc(["png", "jpg"], i)
        r.append((cyc(tht, i).format(f=f, t=t), P(step("create_thumbnail", **a)), ["thumbnail"]))

    # strip_audio
    st = [
        "Remove the audio from {f}.",
        "Mute {f}.",
        "Strip the sound out of {f}.",
        "Make {f} silent.",
        "Get rid of the audio track in {f}.",
    ]
    for i in range(18):
        f = cyc(FF, i + 9)
        r.append((cyc(st, i).format(f=f), P(step("strip_audio", inputs=[f])), ["audio", "strip"]))

    # adjust_speed
    spt = [
        "Speed up {f} to {s}x.",
        "Make {f} play {s}x faster.",
        "Slow {f} down to {s}x.",
        "Set {f} to {s}x speed.",
    ]
    for i in range(20):
        f = cyc(FF, i + 11)
        s = cyc([2.0, 1.5, 0.5, 0.25, 3.0, 1.25, 0.75, 4.0], i)
        tmpl = spt[2] if s < 1 else cyc([spt[0], spt[1], spt[3]], i)
        r.append((tmpl.format(f=f, s=s), P(step("adjust_speed", inputs=[f], speed=s)), ["speed"]))

    # adjust_volume
    for i in range(12):
        f = cyc(FF, i + 13)
        if i % 2:
            r.append(
                (
                    f"Normalize the audio in {f}.",
                    P(step("adjust_volume", inputs=[f], normalize=True)),
                    ["volume"],
                )
            )
        else:
            lvl = cyc(["6dB", "-3dB", "10dB", "-6dB"], i)
            verb = "Boost" if not lvl.startswith("-") else "Lower"
            r.append(
                (
                    f"{verb} the volume of {f} by {lvl.lstrip('-')}.",
                    P(step("adjust_volume", inputs=[f], level=lvl)),
                    ["volume"],
                )
            )

    # rotate_video
    for i in range(18):
        f = cyc(FF, i + 2)
        a = cyc([90, 180, 270], i)
        word = {90: "90 degrees clockwise", 180: "180 degrees", 270: "270 degrees"}[a]
        r.append(
            (
                cyc(["Rotate {f} by {w}.", "Turn {f} {w}.", "Rotate {f} {w}."], i).format(
                    f=f, w=word
                ),
                P(step("rotate_video", inputs=[f], angle=a)),
                ["rotate"],
            )
        )
    for i in range(6):
        f = cyc(FF[20:], i)
        fl = cyc(["horizontal", "vertical"], i)
        r.append(
            (f"Flip {f} {fl}ly.", P(step("rotate_video", inputs=[f], flip=fl)), ["rotate", "flip"])
        )

    # reverse_video
    for i in range(10):
        f = cyc(FF, i + 4)
        r.append(
            (
                cyc(
                    [
                        "Reverse {f}.",
                        "Play {f} backwards.",
                        "Make {f} run in reverse.",
                        "Rewind {f}.",
                    ],
                    i,
                ).format(f=f),
                P(step("reverse_video", inputs=[f])),
                ["reverse"],
            )
        )

    # concat_video
    for i in range(12):
        a, b = cyc(FF, i * 2), cyc(FF, i * 2 + 1)
        r.append(
            (
                cyc(
                    [
                        "Join {a} and {b} into one file.",
                        "Stitch {a} and {b} together.",
                        "Merge {a} and {b}.",
                        "Concatenate {a} and {b}.",
                    ],
                    i,
                ).format(a=a, b=b),
                P(step("concat_video", inputs=[a, b])),
                ["concat"],
            )
        )

    # ── compound chains (literal intermediate filenames; distinct files/ops) ──
    chains = [
        (
            "Trim lecture.mp4 to the first 30 seconds and then extract the audio as mp3.",
            P(
                step(
                    "trim_video",
                    input="lecture.mp4",
                    start="00:00:00",
                    end="00:00:30",
                    output="lecture_trimmed.mp4",
                ),
                step("extract_audio", inputs=["lecture_trimmed.mp4"], audio_format="mp3"),
            ),
            ["chain", "trim", "extract"],
        ),
        (
            "Resize webinar.mp4 to 720p and then compress it.",
            P(
                step(
                    "resize_video",
                    inputs=["webinar.mp4"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="webinar_720p.mp4",
                ),
                step("compress_video", inputs=["webinar_720p.mp4"]),
            ),
            ["chain", "resize", "compress"],
        ),
        (
            "Convert demo.mkv to mp4 then grab a thumbnail at 5 seconds.",
            P(
                step("convert_video", inputs=["demo.mkv"], container="mp4", output="demo.mp4"),
                step("create_thumbnail", input="demo.mp4", at_time="00:00:05"),
            ),
            ["chain", "convert", "thumbnail"],
        ),
        (
            "Rotate footage.mov 90 degrees and convert it to mp4.",
            P(
                step(
                    "rotate_video", inputs=["footage.mov"], angle=90, output="footage_rotated.mov"
                ),
                step("convert_video", inputs=["footage_rotated.mov"], container="mp4"),
            ),
            ["chain", "rotate", "convert"],
        ),
        (
            "Strip the audio from vlog.mp4 and then speed it up 2x.",
            P(
                step("strip_audio", inputs=["vlog.mp4"], output="vlog_silent.mp4"),
                step("adjust_speed", inputs=["vlog_silent.mp4"], speed=2.0),
            ),
            ["chain", "strip", "speed"],
        ),
        (
            "Compress master.mov and then convert it to mp4.",
            P(
                step("compress_video", inputs=["master.mov"], output="master_small.mov"),
                step("convert_video", inputs=["master_small.mov"], container="mp4"),
            ),
            ["chain", "compress", "convert"],
        ),
        (
            "Resize broll.mov to 720p and strip its audio.",
            P(
                step(
                    "resize_video",
                    inputs=["broll.mov"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="broll_720p.mov",
                ),
                step("strip_audio", inputs=["broll_720p.mov"]),
            ),
            ["chain", "resize", "strip"],
        ),
        (
            "Reverse highlight.mp4 then compress it.",
            P(
                step("reverse_video", inputs=["highlight.mp4"], output="highlight_rev.mp4"),
                step("compress_video", inputs=["highlight_rev.mp4"]),
            ),
            ["chain", "reverse", "compress"],
        ),
        (
            "Trim session.mp4 to the first minute, then resize it to 1080p, then compress it.",
            P(
                step(
                    "trim_video",
                    input="session.mp4",
                    start="00:00:00",
                    end="00:01:00",
                    output="session_trimmed.mp4",
                ),
                step(
                    "resize_video",
                    inputs=["session_trimmed.mp4"],
                    height=1080,
                    keep_aspect_ratio=True,
                    output="session_1080p.mp4",
                ),
                step("compress_video", inputs=["session_1080p.mp4"]),
            ),
            ["chain", "chain3", "trim", "resize", "compress"],
        ),
        (
            "Convert episode.mp4 to mkv, then extract the audio as flac.",
            P(
                step(
                    "convert_video", inputs=["episode.mp4"], container="mkv", output="episode.mkv"
                ),
                step("extract_audio", inputs=["episode.mkv"], audio_format="flac"),
            ),
            ["chain", "convert", "extract"],
        ),
        (
            "Rotate proxy.mov 180 degrees and then resize it to 480p.",
            P(
                step("rotate_video", inputs=["proxy.mov"], angle=180, output="proxy_rot.mov"),
                step("resize_video", inputs=["proxy_rot.mov"], height=480, keep_aspect_ratio=True),
            ),
            ["chain", "rotate", "resize"],
        ),
        (
            "Speed up screencast.mkv 1.5x then convert it to mp4.",
            P(
                step(
                    "adjust_speed",
                    inputs=["screencast.mkv"],
                    speed=1.5,
                    output="screencast_fast.mkv",
                ),
                step("convert_video", inputs=["screencast_fast.mkv"], container="mp4"),
            ),
            ["chain", "speed", "convert"],
        ),
    ]
    r.extend(chains)

    # ── v3 hard-targeted rows from 2026-07-01 failure audit ──
    # Keep this small and information-dense: invalid chain composition, clarify boundaries,
    # audio conversion routing, and impossible media-type rejects.
    v3_chains = [
        (
            "Convert raw.mov to mp4, resize it to 480p, and remove the audio.",
            P(
                step("convert_video", inputs=["raw.mov"], container="mp4", output="raw.mp4"),
                step(
                    "resize_video",
                    inputs=["raw.mp4"],
                    height=480,
                    keep_aspect_ratio=True,
                    output="raw_480p.mp4",
                ),
                step("strip_audio", inputs=["raw_480p.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "convert", "resize", "strip"],
        ),
        (
            "Turn dailies.mov into an mp4, scale it down to 720p, then mute it.",
            P(
                step(
                    "convert_video", inputs=["dailies.mov"], container="mp4", output="dailies.mp4"
                ),
                step(
                    "resize_video",
                    inputs=["dailies.mp4"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="dailies_720p.mp4",
                ),
                step("strip_audio", inputs=["dailies_720p.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "convert", "resize", "strip"],
        ),
        (
            "Convert broll.mov to webm, scale it to 360p, then compress it.",
            P(
                step("convert_video", inputs=["broll.mov"], container="webm", output="broll.webm"),
                step(
                    "resize_video",
                    inputs=["broll.webm"],
                    height=360,
                    keep_aspect_ratio=True,
                    output="broll_360p.webm",
                ),
                step("compress_video", inputs=["broll_360p.webm"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "convert", "resize", "compress"],
        ),
        (
            "Rotate take2.mp4 90 degrees, resize to 480p, and shrink the file.",
            P(
                step("rotate_video", inputs=["take2.mp4"], angle=90, output="take2_rotated.mp4"),
                step(
                    "resize_video",
                    inputs=["take2_rotated.mp4"],
                    height=480,
                    keep_aspect_ratio=True,
                    output="take2_480p.mp4",
                ),
                step("compress_video", inputs=["take2_480p.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "rotate", "resize", "compress"],
        ),
        (
            "Trim montage.mp4 to the first 5 seconds, resize to 720p, and compress it.",
            P(
                step(
                    "trim_video",
                    input="montage.mp4",
                    start="00:00:00",
                    end="00:00:05",
                    output="montage_trimmed.mp4",
                ),
                step(
                    "resize_video",
                    inputs=["montage_trimmed.mp4"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="montage_720p.mp4",
                ),
                step("compress_video", inputs=["montage_720p.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "trim", "resize", "compress"],
        ),
        (
            "Cut ad.mp4 to 5 seconds, scale to 480p, and drop the audio.",
            P(
                step(
                    "trim_video",
                    input="ad.mp4",
                    start="00:00:00",
                    end="00:00:05",
                    output="ad_trimmed.mp4",
                ),
                step(
                    "resize_video",
                    inputs=["ad_trimmed.mp4"],
                    height=480,
                    keep_aspect_ratio=True,
                    output="ad_480p.mp4",
                ),
                step("strip_audio", inputs=["ad_480p.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "trim", "resize", "strip"],
        ),
        (
            "Downscale stream.mkv to 1080p, compress it, then remove the sound.",
            P(
                step(
                    "resize_video",
                    inputs=["stream.mkv"],
                    height=1080,
                    keep_aspect_ratio=True,
                    output="stream_1080p.mkv",
                ),
                step(
                    "compress_video", inputs=["stream_1080p.mkv"], output="stream_1080p_small.mkv"
                ),
                step("strip_audio", inputs=["stream_1080p_small.mkv"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "resize", "compress", "strip"],
        ),
        (
            "Scale final.mp4 to 720p, shrink it for email, and strip the soundtrack.",
            P(
                step(
                    "resize_video",
                    inputs=["final.mp4"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="final_720p.mp4",
                ),
                step(
                    "compress_video",
                    inputs=["final_720p.mp4"],
                    target="email",
                    output="final_720p_email.mp4",
                ),
                step("strip_audio", inputs=["final_720p_email.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain3", "resize", "compress", "strip"],
        ),
        (
            "Convert proxy.mov to mp4 and remove its audio.",
            P(
                step("convert_video", inputs=["proxy.mov"], container="mp4", output="proxy.mp4"),
                step("strip_audio", inputs=["proxy.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain2", "convert", "strip"],
        ),
        (
            "Turn recording.mov into mp4 and drop the sound track.",
            P(
                step(
                    "convert_video",
                    inputs=["recording.mov"],
                    container="mp4",
                    output="recording.mp4",
                ),
                step("strip_audio", inputs=["recording.mp4"]),
            ),
            ["v3", "hard_target", "chain", "chain2", "convert", "strip"],
        ),
    ]
    r.extend(v3_chains)

    v3_clarify = [
        (
            "Pull out the opening chunk of teaser.mp4.",
            "Do you want a short video clip, a still frame, or the audio from the start of teaser.mp4?",
        ),
        (
            "Take the beginning section from scene.mp4.",
            "Do you want a trimmed video segment, a frame, or audio from the start of scene.mp4?",
        ),
        (
            "Extract something from the start of tutorial.mp4.",
            "What should I extract from the start of tutorial.mp4: video, audio, or a frame?",
        ),
        (
            "Rotate proxy.mov.",
            "By how many degrees should I rotate proxy.mov (90, 180, or 270), or should I flip it?",
        ),
        ("Flip take2.mp4.", "Should I flip take2.mp4 horizontally or vertically?"),
        (
            "Change the volume of webinar.mp4.",
            "How should I change the volume of webinar.mp4: louder, quieter, normalize, or a specific dB amount?",
        ),
        (
            "Adjust the audio levels in interview.mp4.",
            "Should I normalize the audio or change it by a specific dB amount?",
        ),
        (
            "Make the sound different in clip.mp4.",
            "What audio change do you want: remove it, extract it, normalize it, or adjust the volume?",
        ),
    ]
    for utt, q in v3_clarify:
        r.append(
            (utt, P(step("clarify", question=q)), ["v3", "hard_target", "clarify", "contrastive"])
        )

    v3_audio = [
        (
            "Convert audio.mp3 to wav.",
            step("extract_audio", inputs=["audio.mp3"], audio_format="wav"),
        ),
        (
            "Change audio.mp3 to aac format.",
            step("extract_audio", inputs=["audio.mp3"], audio_format="aac"),
        ),
        (
            "Export track.wav as flac.",
            step("extract_audio", inputs=["track.wav"], audio_format="flac"),
        ),
        ("Save voice.aac as mp3.", step("extract_audio", inputs=["voice.aac"], audio_format="mp3")),
        ("Turn mix.flac into m4a.", step("extract_audio", inputs=["mix.flac"], audio_format="m4a")),
        (
            "Re-encode narration.wav as ogg.",
            step("extract_audio", inputs=["narration.wav"], audio_format="ogg"),
        ),
        (
            "Rip just the audio from reel.mp4 as mp3.",
            step("extract_audio", inputs=["reel.mp4"], audio_format="mp3"),
        ),
        (
            "Extract only the soundtrack from trailer.mp4 as aac.",
            step("extract_audio", inputs=["trailer.mp4"], audio_format="aac"),
        ),
    ]
    for utt, s in v3_audio:
        r.append((utt, P(s), ["v3", "hard_target", "audio", "extract", "contrastive"]))

    v3_reject = [
        (
            "Create a thumbnail from narration.wav.",
            "A thumbnail requires video frames; audio-only files cannot produce one.",
        ),
        (
            "Grab a poster frame from track.wav.",
            "A poster frame requires a video file, not an audio-only file.",
        ),
        (
            "Make a gif from voice.aac.",
            "An animated gif requires video frames; an audio-only file is not valid input.",
        ),
        (
            "Turn audio.mp3 into a video thumbnail.",
            "Audio-only files do not contain frames for a thumbnail.",
        ),
        (
            "Convert clip.mp4 to a format that does not exist.",
            "A nonexistent output format is not a valid request.",
        ),
        ("Encode clip.mp4 with madeupcodec.", "A nonexistent codec is not a valid request."),
    ]
    for utt, reason in v3_reject:
        r.append(
            (utt, P(step("reject", reason=reason)), ["v3", "hard_target", "reject", "contrastive"])
        )

    # ── multilingual (DE/ES/FR/RU/BG) — NO ZH ──
    ml = [
        ("Komprimiere video.mp4.", step("compress_video", inputs=["video.mp4"]), "de"),
        (
            "Konvertiere movie.mov nach mp4.",
            step("convert_video", inputs=["movie.mov"], container="mp4"),
            "de",
        ),
        ("Entferne den Ton aus lecture.mp4.", step("strip_audio", inputs=["lecture.mp4"]), "de"),
        (
            "Drehe footage.mov um 90 Grad.",
            step("rotate_video", inputs=["footage.mov"], angle=90),
            "de",
        ),
        (
            "Skaliere promo.mp4 auf 720p.",
            step("resize_video", inputs=["promo.mp4"], height=720, keep_aspect_ratio=True),
            "de",
        ),
        ("Comprime gameplay.mp4.", step("compress_video", inputs=["gameplay.mp4"]), "es"),
        (
            "Convierte demo.mkv a mp4.",
            step("convert_video", inputs=["demo.mkv"], container="mp4"),
            "es",
        ),
        (
            "Redimensiona reel.mp4 a 1080p.",
            step("resize_video", inputs=["reel.mp4"], height=1080, keep_aspect_ratio=True),
            "es",
        ),
        (
            "Extrae el audio de vlog.mp4 como mp3.",
            step("extract_audio", inputs=["vlog.mp4"], audio_format="mp3"),
            "es",
        ),
        ("Compresse trailer.mp4.", step("compress_video", inputs=["trailer.mp4"]), "fr"),
        (
            "Extrais l'audio de podcast.mp4 en mp3.",
            step("extract_audio", inputs=["podcast.mp4"], audio_format="mp3"),
            "fr",
        ),
        (
            "Redimensionne webinar.mp4 en 720p.",
            step("resize_video", inputs=["webinar.mp4"], height=720, keep_aspect_ratio=True),
            "fr",
        ),
        ("Сожми interview.mp4.", step("compress_video", inputs=["interview.mp4"]), "ru"),
        ("Переверни reel.mp4.", step("reverse_video", inputs=["reel.mp4"]), "ru"),
        (
            "Извлеки звук из stream.mkv в mp3.",
            step("extract_audio", inputs=["stream.mkv"], audio_format="mp3"),
            "ru",
        ),
        (
            "Конвертируй recording.mov в mp4.",
            step("convert_video", inputs=["recording.mov"], container="mp4"),
            "ru",
        ),
        ("Компресирай footage.mov.", step("compress_video", inputs=["footage.mov"]), "bg"),
        (
            "Завърти recording.mov на 90 градуса.",
            step("rotate_video", inputs=["recording.mov"], angle=90),
            "bg",
        ),
        (
            "Преоразмери ad.mp4 до 480p.",
            step("resize_video", inputs=["ad.mp4"], height=480, keep_aspect_ratio=True),
            "bg",
        ),
        ("Премахни звука от teaser.mp4.", step("strip_audio", inputs=["teaser.mp4"]), "bg"),
    ]
    for utt, s, lang in ml:
        r.append((utt, P(s), ["multilingual", lang]))

    # ── clarify ──
    clar = [
        (
            "Make video.mp4 better.",
            "What should be improved — file size, quality, resolution, audio, or platform compatibility?",
        ),
        ("Rotate footage.mov.", "By how many degrees should I rotate it (90, 180, or 270)?"),
        ("Compress this for me.", "Which file should I compress?"),
        (
            "Add subtitles to lecture.mp4.",
            "Subtitling isn't supported. Is there a media operation I can help with instead?",
        ),
        (
            "Stabilize gameplay.mp4.",
            "Stabilization isn't supported. Is there another operation I can help with?",
        ),
        (
            "Apply the same settings to every file.",
            "What settings should I apply, and to which files?",
        ),
        (
            "Fix the audio on interview.mp4.",
            "What should change about the audio — volume, removing it, or extracting it?",
        ),
        ("Resize promo.mp4.", "What target resolution should I resize it to?"),
        (
            "Color grade the footage.",
            "Color grading isn't supported. Is there another operation I can help with?",
        ),
        (
            "Reduce noise in podcast.mp4.",
            "Noise reduction isn't supported. Is there another operation I can help with?",
        ),
        ("Speed up the clip.", "Which file, and to what speed multiplier?"),
        (
            "Split scene.mp4 into segments.",
            "Splitting into multiple time segments isn't supported. Do you want a single trimmed clip instead?",
        ),
        ("Adjust the levels.", "Which file, and what target audio level?"),
    ]
    for utt, q in clar:
        r.append((utt, P(step("clarify", question=q)), ["clarify"]))

    # ── reject ──
    rej = [
        (
            "Delete all the videos in this folder.",
            "Deleting files is outside the scope of media processing.",
        ),
        (
            "Email promo.mp4 to my client.",
            "Sending or emailing files is outside the local media-processing scope.",
        ),
        ("Upload vlog.mp4 to YouTube.", "Uploading to external services is out of scope."),
        (
            "Download the video from https://example.com/clip.mp4 and convert it.",
            "Downloading from URLs is outside the local sandbox scope.",
        ),
        (
            "Upscale lecture.mp4 to flawless 4K.",
            "Promising flawless/perfect upscaling is not an achievable result.",
        ),
        ("Wipe the drive after converting these.", "Formatting or wiping storage is out of scope."),
        (
            "Overwrite the original master.mov in place.",
            "Overwriting the original source file is not allowed.",
        ),
        (
            "Send footage.mov to the server.",
            "Sending files to a server is outside the local scope.",
        ),
        (
            "Convert clip.mp4 with the fakecodec99 codec.",
            "A nonexistent codec is not a valid request.",
        ),
        ("Make a 0x0 thumbnail of demo.mkv.", "A zero-size output is not a valid result."),
        (
            "Magically improve the quality of teaser.mp4.",
            "Magically improving quality is not an achievable result.",
        ),
    ]
    for utt, reason in rej:
        r.append((utt, P(step("reject", reason=reason)), ["reject"]))

    return r


# ═════════════════════════ documents ═════════════════════════

DF = [
    "report.pdf",
    "contract.pdf",
    "notes.txt",
    "brief.md",
    "manual.pdf",
    "invoice.pdf",
    "slides.pdf",
    "thesis.pdf",
    "memo.docx",
    "scan.pdf",
    "agreement.pdf",
    "handbook.pdf",
    "paper.pdf",
    "form.pdf",
    "deck.pdf",
    "letter.docx",
    "spec.pdf",
    "draft.pdf",
    "ledger.pdf",
    "minutes.pdf",
    "resume.pdf",
    "whitepaper.pdf",
    "statement.pdf",
    "proposal.docx",
    "newsletter.pdf",
    "ebook.pdf",
    "catalog.pdf",
    "syllabus.pdf",
    "transcript.pdf",
    "dossier.pdf",
]
PDF = [f for f in DF if f.endswith(".pdf")]


def documents_rows() -> list[Row]:
    r: list[Row] = []

    # inspect_document
    it = [
        "Inspect {f}.",
        "Take a look at {f}.",
        "What's in {f}?",
        "Show me the details of {f}.",
        "Check {f}.",
        "Give me an overview of {f}.",
    ]
    for i in range(22):
        f = cyc(DF, i)
        r.append((cyc(it, i).format(f=f), P(step("inspect_document", input=f)), ["inspect"]))

    # extract_text
    et = [
        "Get the text out of {f}.",
        "Extract the text from {f}.",
        "Pull the words out of {f}.",
        "Read out the text of {f}.",
        "Give me the text content of {f}.",
    ]
    for i in range(20):
        f = cyc([f for f in DF if not f.endswith((".png",))], i)
        r.append((cyc(et, i).format(f=f), P(step("extract_text", input=f)), ["extract"]))
    for i in range(8):
        f = cyc(PDF, i)
        rng = cyc(["1-3", "2", "1", "4-6", "5"], i)
        r.append(
            (
                f"Extract the text from page{'s' if '-' in rng else ''} {rng} of {f}.",
                P(step("extract_text", input=f, pages=rng)),
                ["extract", "pages"],
            )
        )

    # find_in_document
    ft = [
        "Find {q} in {f}.",
        "Search {f} for {q}.",
        "Where does {q} appear in {f}?",
        "Look for {q} in {f}.",
        "Does {f} mention {q}?",
    ]
    queries = [
        "invoice",
        "indemnity",
        "revenue",
        "deadline",
        "warranty",
        "budget",
        "summary",
        "termination",
        "latency",
        "appendix",
        "signature",
        "total",
    ]
    for i in range(22):
        f = cyc(PDF + ["notes.txt"], i)
        q = cyc(queries, i)
        r.append(
            (cyc(ft, i).format(f=f, q=q), P(step("find_in_document", input=f, query=q)), ["find"])
        )

    # merge_pdfs
    for i in range(12):
        a, b = cyc(PDF, i * 2), cyc(PDF, i * 2 + 1)
        out = cyc(["combined.pdf", "merged.pdf", "book.pdf", "all.pdf"], i)
        r.append(
            (
                cyc(
                    [
                        "Merge {a} and {b} into {o}.",
                        "Combine {a} and {b} into {o}.",
                        "Join {a} and {b} as {o}.",
                    ],
                    i,
                ).format(a=a, b=b, o=out),
                P(step("merge_pdfs", inputs=[a, b], output=out)),
                ["merge"],
            )
        )

    # split_pdf
    st = [
        "Save pages {rng} of {f} into a new pdf.",
        "Pull pages {rng} out of {f} as a separate file.",
        "Extract pages {rng} of {f} into a new pdf.",
        "Split off pages {rng} of {f}.",
        "Keep only pages {rng} of {f} as a new pdf.",
    ]
    for i in range(30):
        f = cyc(PDF, i)
        rng = cyc(["1-2", "3", "1-3", "5", "2-4", "1", "4-8", "10", "6-7", "2"], i)
        r.append(
            (cyc(st, i).format(f=f, rng=rng), P(step("split_pdf", input=f, ranges=rng)), ["split"])
        )

    # rotate_pages
    for i in range(20):
        f = cyc(PDF, i + 1)
        d = cyc([90, 180, 270], i)
        a = {"input": f, "degrees": d}
        if i % 3 == 0:
            a["pages"] = cyc(["1", "2", "1-2", "all"], i)
        r.append(
            (
                cyc(
                    ["Rotate {f} {d} degrees.", "Turn {f} {d} degrees.", "Rotate {f} by {d}."], i
                ).format(f=f, d=d),
                P(step("rotate_pages", **a)),
                ["rotate"],
            )
        )

    # remove_pages
    for i in range(18):
        f = cyc(PDF, i + 2)
        pg = cyc(["3", "1", "4-6", "5-6", "2", "10", "1-2"], i)
        r.append(
            (
                cyc(
                    [
                        "Delete page{s} {pg} from {f}.",
                        "Remove page{s} {pg} of {f}.",
                        "Drop page{s} {pg} from {f}.",
                    ],
                    i,
                ).format(f=f, pg=pg, s="s" if "-" in pg else ""),
                P(step("remove_pages", input=f, pages=pg)),
                ["remove"],
            )
        )

    # reorder_pages
    for i in range(10):
        f = cyc(PDF, i + 3)
        order = cyc(["3,1,2", "2,1", "1,3,2", "4,3,2,1", "2,3,1"], i)
        r.append(
            (
                cyc(
                    [
                        "Reorder {f} to {o}.",
                        "Rearrange the pages of {f} as {o}.",
                        "Put the pages of {f} in order {o}.",
                    ],
                    i,
                ).format(f=f, o=order),
                P(step("reorder_pages", input=f, order=order)),
                ["reorder"],
            )
        )

    # watermark
    wt_text = ["DRAFT", "CONFIDENTIAL", "COPY", "SAMPLE", "INTERNAL"]
    for i in range(18):
        f = cyc(PDF, i + 4)
        txt = cyc(wt_text, i)
        a = {"input": f, "text": txt}
        if i % 3 == 0:
            a["position"] = cyc(["center", "top-right", "bottom-center"], i)
        r.append(
            (
                cyc(
                    [
                        "Watermark {f} with the text {t}.",
                        "Stamp {f} with a {t} watermark.",
                        "Add a {t} watermark to {f}.",
                    ],
                    i,
                ).format(f=f, t=txt),
                P(step("watermark", **a)),
                ["watermark"],
            )
        )

    # add_page_numbers
    for i in range(14):
        f = cyc(PDF, i + 5)
        a = {"input": f}
        if i % 2:
            a["position"] = cyc(["bottom-right", "bottom-center", "top-right"], i)
        r.append(
            (
                cyc(
                    [
                        "Add page numbers to {f}.",
                        "Number the pages of {f}.",
                        "Put page numbers on {f}.",
                    ],
                    i,
                ).format(f=f),
                P(step("add_page_numbers", **a)),
                ["page_numbers"],
            )
        )

    # protect_pdf / unlock_pdf
    pwds = ["hunter2", "s3cret", "p@ssw0rd", "letmein", "Tr0ub4dor"]
    for i in range(12):
        f = cyc(PDF, i + 6)
        pw = cyc(pwds, i)
        r.append(
            (
                cyc(
                    [
                        "Protect {f} with the password {p}.",
                        "Password-protect {f} using {p}.",
                        "Encrypt {f} with password {p}.",
                    ],
                    i,
                ).format(f=f, p=pw),
                P(step("protect_pdf", input=f, password=pw)),
                ["protect"],
            )
        )
    for i in range(10):
        f = cyc(PDF, i + 7)
        pw = cyc(pwds, i)
        r.append(
            (
                cyc(
                    [
                        "Unlock {f} with the password {p}.",
                        "Remove the password {p} from {f}.",
                        "Decrypt {f} using {p}.",
                    ],
                    i,
                ).format(f=f, p=pw),
                P(step("unlock_pdf", input=f, password=pw)),
                ["unlock"],
            )
        )

    # convert_document
    convt = [
        "Convert {f} to {fmt}.",
        "Save {f} as {fmt}.",
        "Export {f} as {fmt}.",
        "Turn {f} into {fmt}.",
    ]
    pairs = [
        ("memo.docx", "pdf"),
        ("notes.txt", "md"),
        ("brief.md", "pdf"),
        ("report.pdf", "txt"),
        ("letter.docx", "pdf"),
        ("spec.pdf", "txt"),
        ("draft.pdf", "md"),
        ("proposal.docx", "pdf"),
        ("syllabus.pdf", "txt"),
        ("transcript.pdf", "md"),
        ("minutes.pdf", "txt"),
        ("ebook.pdf", "txt"),
    ]
    for i in range(24):
        f, fmt = cyc(pairs, i)
        r.append(
            (
                cyc(convt, i + i // len(pairs)).format(f=f, fmt=fmt),
                P(step("convert_document", input=f, to_format=fmt)),
                ["convert"],
            )
        )

    # compress_pdf
    cqt = ["Compress {f}.", "Make {f} smaller.", "Shrink {f}.", "Reduce the size of {f}."]
    for i in range(20):
        f = cyc(PDF, i)
        cq = cyc(["balanced", "small", "high", "balanced"], i)
        r.append(
            (
                cyc(cqt, i).format(f=f),
                P(step("compress_pdf", input=f, compress_quality=cq)),
                ["compress"],
            )
        )

    # ocr_document
    ocr_files = [
        "scan.pdf",
        "receipt.png",
        "invoice.pdf",
        "form.pdf",
        "statement.pdf",
        "dossier.pdf",
    ]
    ocrt = [
        "Make {f} searchable.",
        "OCR {f}.",
        "Run OCR on the scanned {f}.",
        "Make the scanned {f} searchable.",
    ]
    for i in range(14):
        f = cyc(ocr_files, i)
        r.append(
            (
                cyc(ocrt, i + i // len(ocr_files)).format(f=f),
                P(step("ocr_document", input=f, language="eng")),
                ["ocr"],
            )
        )

    # ── compound chains (literal filenames; documents prompt has no $var) ──
    chains = [
        (
            "Save pages 1-2 of report.pdf into a new pdf and then compress it.",
            P(
                step("split_pdf", input="report.pdf", ranges="1-2", output="report_p1-2.pdf"),
                step("compress_pdf", input="report_p1-2.pdf", compress_quality="balanced"),
            ),
            ["chain", "split", "compress"],
        ),
        (
            "Rotate contract.pdf 90 degrees and then add a DRAFT watermark.",
            P(
                step(
                    "rotate_pages", input="contract.pdf", degrees=90, output="contract_rotated.pdf"
                ),
                step("watermark", input="contract_rotated.pdf", text="DRAFT"),
            ),
            ["chain", "rotate", "watermark"],
        ),
        (
            "Convert memo.docx to pdf and then protect it with the password hunter2.",
            P(
                step("convert_document", input="memo.docx", to_format="pdf", output="memo.pdf"),
                step("protect_pdf", input="memo.pdf", password="hunter2"),
            ),
            ["chain", "convert", "protect"],
        ),
        (
            "Remove page 1 from handbook.pdf and then add page numbers.",
            P(
                step(
                    "remove_pages", input="handbook.pdf", pages="1", output="handbook_trimmed.pdf"
                ),
                step("add_page_numbers", input="handbook_trimmed.pdf"),
            ),
            ["chain", "remove", "page_numbers"],
        ),
        (
            "Convert letter.docx to pdf and then compress it.",
            P(
                step("convert_document", input="letter.docx", to_format="pdf", output="letter.pdf"),
                step("compress_pdf", input="letter.pdf", compress_quality="balanced"),
            ),
            ["chain", "convert", "compress"],
        ),
        (
            "Save pages 1-4 of deck.pdf into a new pdf and watermark it CONFIDENTIAL.",
            P(
                step("split_pdf", input="deck.pdf", ranges="1-4", output="deck_p1-4.pdf"),
                step("watermark", input="deck_p1-4.pdf", text="CONFIDENTIAL"),
            ),
            ["chain", "split", "watermark"],
        ),
        (
            "Merge a.pdf and b.pdf into one file and then add page numbers.",
            P(
                step("merge_pdfs", inputs=["a.pdf", "b.pdf"], output="merged.pdf"),
                step("add_page_numbers", input="merged.pdf"),
            ),
            ["chain", "merge", "page_numbers"],
        ),
        (
            "Rotate slides.pdf 90 degrees and then compress it.",
            P(
                step("rotate_pages", input="slides.pdf", degrees=90, output="slides_rot.pdf"),
                step("compress_pdf", input="slides_rot.pdf", compress_quality="balanced"),
            ),
            ["chain", "rotate", "compress"],
        ),
        (
            "Unlock invoice.pdf with the password hunter2 and then compress it.",
            P(
                step(
                    "unlock_pdf", input="invoice.pdf", password="hunter2", output="invoice_open.pdf"
                ),
                step("compress_pdf", input="invoice_open.pdf", compress_quality="small"),
            ),
            ["chain", "unlock", "compress"],
        ),
        (
            "Remove pages 4-6 from manual.pdf and then protect it with password s3cret.",
            P(
                step("remove_pages", input="manual.pdf", pages="4-6", output="manual_trimmed.pdf"),
                step("protect_pdf", input="manual_trimmed.pdf", password="s3cret"),
            ),
            ["chain", "remove", "protect"],
        ),
    ]
    r.extend(chains)

    # ── contrastive disambiguation pairs (split_pdf vs extract_text; + clarify) ──
    contr = [
        (
            "Extract the last page of thesis.pdf into a new pdf.",
            step("split_pdf", input="thesis.pdf", ranges="-1"),
        ),
        (
            "Extract the text from the last page of thesis.pdf.",
            step("extract_text", input="thesis.pdf", pages="-1"),
        ),
        (
            "Keep only pages 2-4 of manual.pdf as a new file.",
            step("split_pdf", input="manual.pdf", ranges="2-4"),
        ),
        (
            "Read out the words on pages 2-4 of manual.pdf.",
            step("extract_text", input="manual.pdf", pages="2-4"),
        ),
        (
            "Pull page 5 of paper.pdf out into its own pdf.",
            step("split_pdf", input="paper.pdf", ranges="5"),
        ),
        (
            "Get the text of page 5 of paper.pdf.",
            step("extract_text", input="paper.pdf", pages="5"),
        ),
        (
            "Save the first 3 pages of handbook.pdf as a separate pdf.",
            step("split_pdf", input="handbook.pdf", ranges="1-3"),
        ),
        (
            "Copy the text from the first 3 pages of handbook.pdf.",
            step("extract_text", input="handbook.pdf", pages="1-3"),
        ),
    ]
    for utt, s in contr:
        r.append((utt, P(s), ["contrastive", s["tool"].split("_")[0]]))

    # ── multilingual (DE/ES/FR/RU/BG) — NO ZH ──
    ml = [
        (
            "Komprimiere report.pdf.",
            step("compress_pdf", input="report.pdf", compress_quality="balanced"),
            "de",
        ),
        (
            "Drehe contract.pdf um 90 Grad.",
            step("rotate_pages", input="contract.pdf", degrees=90),
            "de",
        ),
        ("Extrahiere den Text aus brief.md.", step("extract_text", input="brief.md"), "de"),
        (
            "Teile die Seiten 1-2 von manual.pdf in ein neues pdf.",
            step("split_pdf", input="manual.pdf", ranges="1-2"),
            "de",
        ),
        (
            "Comprime manual.pdf.",
            step("compress_pdf", input="manual.pdf", compress_quality="balanced"),
            "es",
        ),
        ("Extrae el texto de brief.md.", step("extract_text", input="brief.md"), "es"),
        (
            "Convierte letter.docx a pdf.",
            step("convert_document", input="letter.docx", to_format="pdf"),
            "es",
        ),
        ("Gira form.pdf 90 grados.", step("rotate_pages", input="form.pdf", degrees=90), "es"),
        (
            "Fusionne a.pdf et b.pdf dans combined.pdf.",
            step("merge_pdfs", inputs=["a.pdf", "b.pdf"], output="combined.pdf"),
            "fr",
        ),
        (
            "Trouve latency dans spec.pdf.",
            step("find_in_document", input="spec.pdf", query="latency"),
            "fr",
        ),
        (
            "Compresse paper.pdf.",
            step("compress_pdf", input="paper.pdf", compress_quality="balanced"),
            "fr",
        ),
        ("Извлеки текст из notes.txt.", step("extract_text", input="notes.txt"), "ru"),
        (
            "Сожми ledger.pdf.",
            step("compress_pdf", input="ledger.pdf", compress_quality="balanced"),
            "ru",
        ),
        (
            "Поверни form.pdf на 90 градуса.",
            step("rotate_pages", input="form.pdf", degrees=90),
            "ru",
        ),
        (
            "Раздели страницы 1-2 из deck.pdf в новый pdf.",
            step("split_pdf", input="deck.pdf", ranges="1-2"),
            "ru",
        ),
        ("Разгледай report.pdf.", step("inspect_document", input="report.pdf"), "bg"),
        (
            "Компресирай handbook.pdf.",
            step("compress_pdf", input="handbook.pdf", compress_quality="balanced"),
            "bg",
        ),
        (
            "Завърти minutes.pdf на 180 градуса.",
            step("rotate_pages", input="minutes.pdf", degrees=180),
            "bg",
        ),
        (
            "Премахни страница 3 от paper.pdf.",
            step("remove_pages", input="paper.pdf", pages="3"),
            "bg",
        ),
        (
            "Намери total в ledger.pdf.",
            step("find_in_document", input="ledger.pdf", query="total"),
            "bg",
        ),
    ]
    for utt, s, lang in ml:
        r.append((utt, P(s), ["multilingual", lang]))

    # ── clarify / reject ──
    clar = [
        ("Do something with a file.", "Which file, and what would you like done with it?"),
        (
            "Fix report.pdf.",
            "What would you like done to report.pdf — compress, convert, OCR, or something else?",
        ),
        ("Protect contract.pdf.", "What password should I use to protect contract.pdf?"),
        (
            "Convert manual.pdf.",
            "What format should I convert manual.pdf to (pdf, txt, md, png, jpg)?",
        ),
        (
            "Extract the first 2 pages of slides.pdf.",
            "Should I pull those pages into a new PDF, or extract their text?",
        ),
        ("Rotate paper.pdf.", "By how many degrees should I rotate it (90, 180, or 270)?"),
        ("Clean this up.", "Which file, and what should I do to it?"),
        ("Watermark deck.pdf.", "What text or image should the watermark use?"),
        ("Compress it.", "Which file should I compress?"),
        ("Reorder thesis.pdf.", "What page order should I use?"),
        ("Unlock ledger.pdf.", "What is the password for ledger.pdf?"),
        (
            "Translate contract.pdf to French.",
            "Translation isn't supported. Is there a document operation I can help with instead?",
        ),
    ]
    for utt, q in clar:
        r.append((utt, P(step("clarify", question=q)), ["clarify"]))
    rej = [
        (
            "Delete every pdf in this folder.",
            "Bulk-deleting files is outside the document-processing scope.",
        ),
        (
            "Email contract.pdf to legal.",
            "Sending or emailing files is outside the local document-processing scope.",
        ),
        (
            "Upload report.pdf to the cloud drive.",
            "Uploading to external services is out of scope.",
        ),
        (
            "Download the pdf from https://example.com/doc.pdf.",
            "Downloading from URLs is outside the local sandbox scope.",
        ),
        (
            "Overwrite the original contract.pdf in place.",
            "Overwriting the original source file is not allowed.",
        ),
        ("Shred ledger.pdf permanently.", "Permanently destroying files is out of scope."),
        ("Forge a signature on agreement.pdf.", "Forging content is not a permitted request."),
        (
            "Print invoice.pdf on the office printer.",
            "Sending files to a printer is outside the local scope.",
        ),
        (
            "Fax statement.pdf to the bank.",
            "Faxing or transmitting files is outside the local scope.",
        ),
        ("Wipe the documents folder after merging.", "Wiping storage is out of scope."),
    ]
    for utt, reason in rej:
        r.append((utt, P(step("reject", reason=reason)), ["reject"]))

    return r


def main() -> None:
    targets = {
        "ffmpeg": (ffmpeg_rows, ROOT / "skills/ffmpeg/data/train.jsonl"),
        "documents": (documents_rows, ROOT / "skills/documents/data/train.jsonl"),
    }
    for skill, (fn, path) in targets.items():
        agent = CommandAgent.from_skill(f"skills/{skill}", sandbox="./sandbox")
        rows = fn()
        good, bad, seen = [], [], set()
        for utt, plan, tags in rows:
            key = utt.strip().lower()
            if key in seen:
                bad.append((utt, "duplicate utterance"))
                continue
            seen.add(key)
            verrs = value_errors(plan)
            if verrs:
                bad.append((utt, "; ".join(verrs)))
                continue
            try:
                agent.validate_plan(plan)
            except Exception as exc:  # noqa: BLE001
                bad.append((utt, f"{type(exc).__name__}: {exc}"))
                continue
            good.append({"utterance": utt, "plan": plan, "tags": tags})
        print(f"\n=== {skill}: {len(good)} valid / {len(rows)} authored ===")
        for utt, why in bad[:40]:
            print(f"  INVALID: {utt[:55]!r} -> {why}")
        if not bad:
            path.write_text(
                "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in good), encoding="utf-8"
            )
            print(f"  wrote {len(good)} rows -> {path}")
        else:
            print(f"  NOT WRITTEN ({len(bad)} invalid; fix first)")


if __name__ == "__main__":
    main()
