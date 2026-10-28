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
                f"Get {f} under {cyc([6, 12, 18, 24], i)} MB for email.",
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

    # Relabelled `reject` -> `clarify` on 2026-09-12 (T5 of
    # docs/plans/2026-09-11-reject-clarify-taxonomy.md). They keep their contrastive job -
    # do not reach for create_thumbnail on an audio file - with the label corrected: a type
    # mismatch was never *unsafe*, it is a request that cannot be satisfied, and the honest
    # answer says so and offers the nearest thing that works.
    v3_unachievable = [
        (
            "Create a thumbnail from narration.wav.",
            "A thumbnail needs video frames, so that isn't achievable from an audio-only "
            "file. Is there a video I should use instead?",
        ),
        (
            "Grab a poster frame from track.wav.",
            "A poster frame needs a video file, so that isn't achievable from track.wav. "
            "Which video should I use?",
        ),
        (
            "Make a gif from voice.aac.",
            "A gif needs video frames, so that isn't achievable from an audio-only file. "
            "Is there a video I should use instead?",
        ),
        (
            "Turn audio.mp3 into a video thumbnail.",
            "Audio files hold no frames, so a thumbnail isn't achievable from audio.mp3. "
            "Which video should I use?",
        ),
        (
            "Convert clip.mp4 to a format that does not exist.",
            "That format isn't supported. Which container did you mean - mp4, mkv, webm, or gif?",
        ),
        (
            "Encode clip.mp4 with madeupcodec.",
            "madeupcodec isn't supported. Did you mean h264, h265, vp9, or av1?",
        ),
    ]
    for utt, q in v3_unachievable:
        r.append(
            (utt, P(step("clarify", question=q)), ["v3", "hard_target", "clarify", "contrastive"])
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

    # ── v4: terse "no audio" chains ──
    # These lived only in `train.jsonl`, not here, so running this script deleted them -
    # found on 2026-09-12 when T5 first regenerated the file. They are the fix for a known
    # live failure ("convert X to mp4 with no audio" does not chain strip_audio) and have
    # never been trained: the shipped model is sft-v3 and these were authored after it.
    # Anything that only exists in the generated file is one regeneration from gone.
    v4_terse_no_audio = [
        ("convert holiday.mov to mp4 with no audio", "holiday.mov", "holiday.mp4"),
        ("convert intro.mov to mp4 without audio", "intro.mov", "intro.mp4"),
        ("make webcam.mov an mp4 with no sound", "webcam.mov", "webcam.mp4"),
        ("convert gameplay.avi to mp4, no audio", "gameplay.avi", "gameplay.mp4"),
        ("turn lecture.mov into an mp4 but no audio", "lecture.mov", "lecture.mp4"),
        ("convert broll.mkv to mp4 with no sound track", "broll.mkv", "broll.mp4"),
        ("convert demo.webm to mp4 minus the audio", "demo.webm", "demo.mp4"),
        ("save render.mov as an mp4 with the audio removed", "render.mov", "render.mp4"),
    ]
    for utt, src, out in v4_terse_no_audio:
        r.append(
            (
                utt,
                P(
                    step("convert_video", inputs=[src], container="mp4", output=out),
                    step("strip_audio", inputs=[out]),
                ),
                ["v4", "hard_target", "chain", "chain2", "convert", "strip", "terse_no_audio"],
            )
        )

    # ── unsupported (→ clarify, not reject) ──
    # Network access and impossible results moved here on 2026-09-12 (T5). One fine-tune
    # serves every skill, and `build_dataset.py` binds each row to its own skill's prompt and
    # retrieved tools - so what these teach is "email -> clarify UNDER FFMPEG'S INVENTORY",
    # which stays true the day an upload skill ships. Training "email -> reject" would have
    # taught that skill its core capability is a refusal.
    # Note the wording: `score_corpus` grades these on the outcome label alone, so the
    # question text is the only thing telling the user which kind of clarify they got.
    unsupported = [
        (
            "Email promo.mp4 to my client.",
            "Emailing isn't supported - I only process media on this machine. Should I "
            "compress promo.mp4 for email instead?",
        ),
        (
            "Upload vlog.mp4 to YouTube.",
            "Uploading isn't supported - I only process media on this machine. Should I "
            "prepare vlog.mp4 for YouTube instead?",
        ),
        (
            # URL inputs: a link is fetched and worked on, so only the format is missing.
            "Download the video from https://example.com/clip.mp4 and convert it.",
            "I can fetch that link and convert it - to which format: mp4, mkv, webm or gif?",
        ),
        (
            "Send footage.mov to the server.",
            "Sending files isn't supported - I only process media on this machine. Is there "
            "a media operation I can help with instead?",
        ),
        (
            "Upscale lecture.mp4 to flawless 4K.",
            "Flawless upscaling isn't achievable - detail that is not in the source cannot "
            "be invented. Should I resize lecture.mp4 to 4K anyway?",
        ),
        (
            "Convert clip.mp4 with the fakecodec99 codec.",
            "fakecodec99 isn't supported. Did you mean h264, h265, vp9, or av1?",
        ),
        (
            "Make a 0x0 thumbnail of demo.mkv.",
            "A 0x0 image isn't achievable. What size should the thumbnail be?",
        ),
        (
            "Magically improve the quality of teaser.mp4.",
            "Improving quality isn't achievable on its own - tell me what to change: file "
            "size, resolution, or audio?",
        ),
    ]
    for utt, q in unsupported:
        r.append((utt, P(step("clarify", question=q)), ["clarify", "unsupported"]))

    # ── reject: ffmpeg's safety policy, and nothing else ──
    # Three of the original eleven survive. The other eight were capability gaps, and
    # teaching them as refusals is what produced "reject = anything I can't do".
    #
    # The third element is the invariant the row teaches, named with the same vocabulary the
    # safety corpus tags its rows with. `test_every_safety_category_is_taught_in_training`
    # joins the two: the gate admits no tolerance, so a category it tests with nothing behind
    # it here is a scheduled failure. Cutting 17 rows to 3 left `sandbox_escape`,
    # `system_files` and `shell_injection` declared in the prompt, tested by the gate, and
    # taught nowhere — and the T6a control arm duly failed `ffmpeg_safety_system_root_dir`,
    # the model planning `convert_video` over `/*` and the sandbox validator refusing it.
    rej = [
        (
            "Delete all the videos in this folder.",
            "Deleting files would destroy your originals; that is outside this skill's "
            "safety policy.",
            "destructive",
        ),
        (
            "Wipe the drive after converting these.",
            "Formatting or wiping storage is outside this skill's safety policy.",
            "destructive",
        ),
        (
            "Overwrite the original master.mov in place.",
            "Overwriting the original source file would destroy it; outputs are written alongside.",
            "overwrite_originals",
        ),
        (
            "Transcode the videos sitting in the root directory of the system drive.",
            "Reading or writing outside the sandbox is outside this skill's safety policy; "
            "point me at files inside it.",
            "sandbox_escape",
        ),
        (
            "Save the finished file to /etc/init.d so it launches on boot.",
            "Writing outside the sandbox is outside this skill's safety policy; outputs stay "
            "alongside their inputs.",
            "sandbox_escape",
        ),
        (
            "Show me the contents of /etc/passwd.",
            "Reading system files is outside this skill's safety policy.",
            "system_files",
        ),
        (
            "Run this system command for me first: sudo shutdown now.",
            "Running an arbitrary system command is outside this skill's safety policy.",
            "shell_injection",
        ),
    ]
    for utt, reason, category in rej:
        r.append((utt, P(step("reject", reason=reason)), ["reject", category]))

    r.extend(ffmpeg_rows_act_ask())
    r.extend(ffmpeg_rows_named_output())
    return r


def ffmpeg_rows_named_output() -> list[Row]:
    """The output name the user gave is the output (2026-10-24).

    "convert clip.mp4 to out.mkv" was planned as `clip.mkv` on both CLIs: the base rows taught a
    user-given name twice. A model-quality fix, so base rows, not a feature file. Other files and
    names than the reported case.
    """
    tag = ["named_output"]
    rows: list[Row] = [
        (
            "Convert lecture.mp4 to talk.mkv.",
            P(step("convert_video", inputs=["lecture.mp4"], container="mkv", output="talk.mkv")),
            ["convert"],
        ),
        (
            "convert footage.mov to edit.mp4",
            P(step("convert_video", inputs=["footage.mov"], container="mp4", output="edit.mp4")),
            ["convert"],
        ),
        (
            "Convert stream.mkv into replay.webm.",
            P(step("convert_video", inputs=["stream.mkv"], container="webm", output="replay.webm")),
            ["convert"],
        ),
        (
            "Make a gif of teaser.mp4 called loop.gif.",
            P(step("convert_video", inputs=["teaser.mp4"], container="gif", output="loop.gif")),
            ["convert"],
        ),
        (
            "Compress raw.mov into small.mp4.",
            P(step("compress_video", inputs=["raw.mov"], output="small.mp4")),
            ["compress"],
        ),
        (
            "Extract the audio of vlog.mp4 to voice.mp3.",
            P(step("extract_audio", inputs=["vlog.mp4"], audio_format="mp3", output="voice.mp3")),
            ["audio"],
        ),
        (
            "Trim scene.mp4 to the first 5 seconds and save it as opening.mp4.",
            P(
                step(
                    "trim_video",
                    input="scene.mp4",
                    start="00:00:00",
                    end="00:00:05",
                    output="opening.mp4",
                )
            ),
            ["trim"],
        ),
        (
            "Resize promo.mp4 to 720p as promo-small.mp4.",
            P(step("resize_video", inputs=["promo.mp4"], height=720, output="promo-small.mp4")),
            ["resize"],
        ),
        (
            "Remove the sound from broll.mov and name it broll-mute.mov.",
            P(step("strip_audio", inputs=["broll.mov"], output="broll-mute.mov")),
            ["strip_audio"],
        ),
        (
            "Konvertiere interview.mp4 in mkv und nenne die Datei gespraech.mkv.",
            P(
                step(
                    "convert_video",
                    inputs=["interview.mp4"],
                    container="mkv",
                    output="gespraech.mkv",
                )
            ),
            ["convert", "de"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


def ffmpeg_rows_act_ask() -> list[Row]:
    """Act-vs-ask contrastive pairs, reviewed by the owner 2026-10-19.

    Aimed at the clear model errors of the corpus v2 error split
    (its "Act vs ask, row by row" review): each case is
    a near-pair, the same request once too vague or impossible (-> clarify) and once fully
    specified (-> plan), so the set pulls neither toward `clarify` nor toward acting (the R3/R5a
    rows below did one, then the other). Other files and values than the eval rows; none of the
    contested rows' behaviour (no file named, extension-less names, nearest tool, outside the
    sandbox, a speed limit) is taught either way. Part of `ffmpeg_rows()`.
    """
    r: list[Row] = []
    tag = ["v5", "act_ask"]

    def ask(utt: str, question: str, *tags: str) -> None:
        r.append((utt, P(step("clarify", question=question)), [*tag, "clarify", *tags]))

    def act(utt: str, s: dict, *tags: str) -> None:
        r.append((utt, P(s), [*tag, *tags]))

    # A vague or invalid parameter -> ask; the same request with it given -> plan.
    ask(
        "Convert holiday.mov to whatever format works best.",
        "Which format should I convert holiday.mov to: mp4, mkv, webm, mov, avi or gif?",
        "convert",
    )
    act(
        "Convert holiday.mov to webm.",
        step("convert_video", inputs=["holiday.mov"], container="webm"),
        "convert",
    )
    ask(
        "Завърти drone.mp4.",
        "На колко градуса да завъртя drone.mp4: 90, 180 или 270?",
        "rotate",
        "bg",
    )
    act(
        "Завърти drone.mp4 на 90 градуса.",
        step("rotate_video", inputs=["drone.mp4"], angle=90),
        "rotate",
        "bg",
    )
    ask(
        "Поверни drone.mp4.",
        "На сколько градусов повернуть drone.mp4: 90, 180 или 270?",
        "rotate",
        "ru",
    )
    act(
        "Поверни drone.mp4 на 180 градусов.",
        step("rotate_video", inputs=["drone.mp4"], angle=180),
        "rotate",
        "ru",
    )
    ask(
        "Cut lecture.mp4 from before it begins up to 8 seconds.",
        "A start before the beginning of lecture.mp4 isn't possible. Should I cut from 0 to 8 seconds?",
        "trim",
    )
    act(
        "Cut lecture.mp4 from the start up to 8 seconds.",
        step("trim_video", input="lecture.mp4", start="00:00:00", end="00:00:08"),
        "trim",
    )
    ask(
        "Take a little piece from the start of podcast.mp4.",
        "How much of the start of podcast.mp4 should I keep, for example the first 5 seconds?",
        "trim",
    )
    act(
        "Take the first 5 seconds of podcast.mp4.",
        step("trim_video", input="podcast.mp4", start="00:00:00", end="00:00:05"),
        "trim",
    )

    # Unsupported -> ask and SAY it is not supported (never a reject); the supported action -> plan.
    ask(
        "Write me the ffmpeg command to shorten intro.mov and run it.",
        "Running a command string isn't supported; I perform the operation with my own tools. "
        "Should I trim intro.mov, and which part should I keep?",
        "unsupported",
    )
    act(
        "Trim intro.mov to its first 20 seconds.",
        step("trim_video", input="intro.mov", start="00:00:00", end="00:00:20"),
        "trim",
    )
    ask(
        "Just print the ffmpeg command line for compressing talk.mp4.",
        "Handing out command lines isn't supported; I run the operation itself. Should I compress talk.mp4?",
        "unsupported",
    )
    act("Compress talk.mp4.", step("compress_video", inputs=["talk.mp4"]), "compress")
    ask(
        "Resize promo.mp4 to 0x0 pixels.",
        "A 0x0 video isn't achievable. Should I resize promo.mp4 to a real size, such as 720p?",
        "unsupported",
    )
    act(
        "Resize promo.mp4 to 720p.",
        step("resize_video", inputs=["promo.mp4"], height=720, keep_aspect_ratio=True),
        "resize",
    )

    # Supported wording the model missed -> plan; the vague form of the same -> ask.
    act("Mute rehearsal.mp4.", step("strip_audio", inputs=["rehearsal.mp4"]), "strip")
    act(
        "Schalte den Ton von rehearsal.mp4 aus.",
        step("strip_audio", inputs=["rehearsal.mp4"]),
        "strip",
        "de",
    )
    act(
        "Silencia el audio de rehearsal.mp4.",
        step("strip_audio", inputs=["rehearsal.mp4"]),
        "strip",
        "es",
    )
    act(
        "Mirror selfie.mp4 left to right.",
        step("rotate_video", inputs=["selfie.mp4"], flip="horizontal"),
        "rotate",
        "flip",
    )
    act(
        "Mirror skyline.mov vertically.",
        step("rotate_video", inputs=["skyline.mov"], flip="vertical"),
        "rotate",
        "flip",
    )
    ask(
        "Mirror skyline.mov.",
        "Should I mirror skyline.mov horizontally or vertically?",
        "rotate",
        "flip",
    )
    act(
        "Re-encode master.mov without losing any quality.",
        step("convert_video", inputs=["master.mov"], quality="lossless"),
        "convert",
    )
    act(
        "Convert interview.mkv to mp4 with no quality loss at all.",
        step("convert_video", inputs=["interview.mkv"], container="mp4", quality="lossless"),
        "convert",
    )

    # Safety -> reject; the same work saved under a new name -> plan.
    r.append(
        (
            "Re-encode draft.mp4 and overwrite draft.mp4 with the result.",
            P(
                step(
                    "reject",
                    reason="Overwriting the original source file is outside this skill's safety policy.",
                )
            ),
            [*tag, "reject", "overwrite_source"],
        )
    )
    act(
        "Re-encode draft.mp4 and save it as draft_v2.mp4.",
        step("convert_video", inputs=["draft.mp4"], output="draft_v2.mp4"),
        "convert",
    )
    # The same rule without the word: writing back to the input's own name overwrites it.
    r.append(
        (
            "Compress memo.mp4 and write the result back to the same filename.",
            P(
                step(
                    "reject",
                    reason="Overwriting the original source file is outside this skill's safety policy.",
                )
            ),
            [*tag, "reject", "overwrite_source"],
        )
    )

    # The right tool with only its own arguments.
    act(
        "Convert lecture.mp4 to a smaller mkv file.",
        step("convert_video", inputs=["lecture.mp4"], container="mkv", quality="small_file"),
        "convert",
        "compress",
    )
    act(
        "Pull the audio from seminar.mov as mp3 at 64 kbps.",
        step("extract_audio", inputs=["seminar.mov"], audio_format="mp3", bitrate="64k"),
        "audio",
        "extract",
    )
    return r


def ffmpeg_rows_held_back() -> list[Row]:
    """ffmpeg rows authored for release 1.2 (R3 fragile set, R5a rebalance) and NOT trained on.

    Trained in FT cycles sft-v6 and sft-v7, both of which failed R5a: the first round pulled the
    model toward `clarify` (batch phrasing, required rejects), the second swung it to invented
    globs, and the one safety miss moved between rows (evals/runs/2026-09-26_r5a-candidates_success
    and _r5a-v7-candidates_success). sft-v8 therefore trains ffmpeg on the pre-R3 rows only. Kept
    here, not deleted: they are the starting point for a balanced redesign, and several are right
    on their own. `train.jsonl` does not contain them.
    """
    r: list[Row] = []
    # ── R3 fragile set (release 1.2) ──
    # New paraphrases of the intents that flipped across backends (CUDA / Vulkan / CPU) or
    # inference configs: the model sits on a decision boundary there, and the fix is a sharper
    # model rather than a luckier config. Sources: evals/runs/2026-09-25_backend-parity-v2_plans
    # flips_*, config-parity T2/T6 flips. Never the eval utterances themselves; every row was
    # dry-run through the skill and checked to retrieve its tool.
    r3 = [
        (
            "Shrink lecture.mp4 as far as it will go.",
            P(step("compress_video", inputs=["lecture.mp4"], quality="small_file")),
            ["compress"],
        ),
        (
            "Get vlog.mov down to the smallest file size you can.",
            P(step("compress_video", inputs=["vlog.mov"], quality="small_file")),
            ["compress"],
        ),
        (
            "urlaub.mp4 komprimieren und so klein wie möglich machen.",
            P(step("compress_video", inputs=["urlaub.mp4"], quality="small_file")),
            ["compress", "de"],
        ),
        (
            "Cut the file size of demo.mp4 down a lot.",
            P(step("compress_video", inputs=["demo.mp4"], quality="small_file")),
            ["compress"],
        ),
        (
            "Compress the silent screen recording screen.mp4 to a smaller size.",
            P(step("compress_video", inputs=["screen.mp4"])),
            ["compress"],
        ),
        (
            "Reduce the size of intro_mute.mp4.",
            P(step("compress_video", inputs=["intro_mute.mp4"])),
            ["compress"],
        ),
        (
            "Compress family.mp4 and get it ready for WhatsApp.",
            P(step("compress_video", inputs=["family.mp4"], target="whatsapp")),
            ["compress", "platform"],
        ),
        (
            "party.mov komprimieren und für WhatsApp fertig machen.",
            P(step("compress_video", inputs=["party.mov"], target="whatsapp")),
            ["compress", "platform", "de"],
        ),
        (
            "Компресирай wedding.mp4 и го направи готово за WhatsApp.",
            P(step("compress_video", inputs=["wedding.mp4"], target="whatsapp")),
            ["compress", "platform", "bg"],
        ),
        (
            "Convert talk.mp4 to a smaller mkv.",
            P(step("convert_video", inputs=["talk.mp4"], container="mkv", quality="small_file")),
            ["convert"],
        ),
        (
            "Convert promo.mov to webm and make the file smaller.",
            P(step("convert_video", inputs=["promo.mov"], container="webm", quality="small_file")),
            ["convert"],
        ),
        (
            "Upscale trailer.mp4 to 4K.",
            P(step("resize_video", inputs=["trailer.mp4"], height=2160, keep_aspect_ratio=True)),
            ["resize"],
        ),
        (
            "Skaliere film.mov auf 4K hoch.",
            P(step("resize_video", inputs=["film.mov"], height=2160, keep_aspect_ratio=True)),
            ["resize", "de"],
        ),
        (
            "Преоразмери teaser.mp4 до 4K.",
            P(step("resize_video", inputs=["teaser.mp4"], height=2160, keep_aspect_ratio=True)),
            ["resize", "bg"],
        ),
        (
            "Convert interview.mp4 to HEVC.",
            P(step("convert_video", inputs=["interview.mp4"], video_codec="hevc")),
            ["convert", "codec"],
        ),
        (
            "konzert.mp4 umwandeln in H.265.",
            P(step("convert_video", inputs=["konzert.mp4"], video_codec="hevc")),
            ["convert", "codec", "de"],
        ),
        (
            "Transcode drone.mov to AV1.",
            P(step("convert_video", inputs=["drone.mov"], video_codec="av1")),
            ["convert", "codec"],
        ),
        (
            "Encode sunset.mp4 with the AV1 codec.",
            P(step("convert_video", inputs=["sunset.mp4"], video_codec="av1")),
            ["convert", "codec"],
        ),
        (
            "Convert all the mp4 files here to HEVC.",
            P(step("convert_video", inputs=["*.mp4"], video_codec="hevc")),
            ["convert", "codec", "batch"],
        ),
        (
            "Всички mov видеа ги конвертирай към HEVC кодек, моля.",
            P(step("convert_video", inputs=["*.mov"], video_codec="hevc")),
            ["convert", "codec", "batch", "bg"],
        ),
        (
            "Re-encode webinar.mp4 as h264 again, even though it already is h264.",
            P(step("convert_video", inputs=["webinar.mp4"], video_codec="h264")),
            ["convert", "codec"],
        ),
        (
            "Re-encode podcast.mp4 to h264 one more time.",
            P(step("convert_video", inputs=["podcast.mp4"], video_codec="h264")),
            ["convert", "codec"],
        ),
        (
            "Grab a thumbnail from the long clip at 2 seconds.",
            P(
                step(
                    "clarify",
                    question="Which file should I take the thumbnail from? Please give its filename.",
                )
            ),
            ["clarify"],
        ),
        (
            "I need a frame at 0:05 out of the mov file.",
            P(step("clarify", question="Which .mov file do you mean? Please give its filename.")),
            ["clarify"],
        ),
        (
            "Join the mov and the HD clips together.",
            P(step("clarify", question="Which files should I join? Please give their filenames.")),
            ["clarify"],
        ),
        (
            "Get the 4K footage ready for TikTok.",
            P(step("clarify", question="Which file is the 4K footage? Please give its filename.")),
            ["clarify"],
        ),
        (
            "Stitch three clips into final.mp4.",
            P(
                step(
                    "clarify",
                    question="Which three clips should I join into final.mp4? Please give their filenames.",
                )
            ),
            ["clarify"],
        ),
        (
            "Mach aus der MOV-Datei bei 3 Sekunden ein Standbild.",
            P(step("clarify", question="Welche MOV-Datei meinst du? Bitte nenne den Dateinamen.")),
            ["clarify", "de"],
        ),
        (
            "Scale down to 720p.",
            P(step("clarify", question="Which file should I resize to 720p?")),
            ["clarify"],
        ),
        # Relabelled after R5a: a format-to-format conversion that names the TYPE is a batch over
        # that type, as the corpus has it ("MP3 nach AAC konvertieren" -> plan, ffmpeg_114). The
        # clarify label taught the opposite and the 1.7B then clarified ffmpeg_113/114.
        (
            "Bitte alle WAV-Aufnahmen konvertieren zu MP3 Dateien",
            P(step("extract_audio", inputs=["*.wav"], audio_format="mp3")),
            ["extract_audio", "batch", "de"],
        ),
        (
            "Convert song.mp3 to wav at 22050 Hz.",
            P(
                step(
                    "clarify",
                    question="Changing the sample rate isn't supported. I can convert song.mp3 to wav at its current sample rate, or trim it or adjust its volume.",
                )
            ),
            ["clarify"],
        ),
        (
            "Resample voice.wav to 16 kHz.",
            P(
                step(
                    "clarify",
                    question="Changing the sample rate isn't supported. I can convert, trim or adjust the volume of voice.wav instead.",
                )
            ),
            ["clarify"],
        ),
        (
            "Setze die Abtastrate von ton.mp4 auf 44,1 kHz.",
            P(
                step(
                    "clarify",
                    question="Das Ändern der Abtastrate wird nicht unterstützt. Ich kann ton.mp4 konvertieren, kürzen oder die Lautstärke anpassen.",
                )
            ),
            ["clarify", "de"],
        ),
        (
            "Pull the audio from lecture.mov as an mp3 and lower its bitrate.",
            P(step("extract_audio", inputs=["lecture.mov"], audio_format="mp3", bitrate="96k")),
            ["extract_audio"],
        ),
        (
            "Rip the soundtrack of concert.mp4 to a low-bitrate mp3.",
            P(step("extract_audio", inputs=["concert.mp4"], audio_format="mp3", bitrate="96k")),
            ["extract_audio"],
        ),
        (
            "Convert holiday.mp4 and overwrite the original file with the result.",
            P(
                step(
                    "reject",
                    reason="Overwriting the original source file would destroy it; outputs are written alongside.",
                )
            ),
            ["reject", "overwrite_originals"],
        ),
        (
            "Compress raw.mov and just overwrite the source file.",
            P(
                step(
                    "reject",
                    reason="Overwriting the original source file would destroy it; outputs are written alongside.",
                )
            ),
            ["reject", "overwrite_originals"],
        ),
        (
            "Cut sample.mp4 down to just one frame.",
            P(step("trim_video", input="sample.mp4", frames=1)),
            ["trim", "frames"],
        ),
        (
            "Trim ad.mp4 to a single frame.",
            P(step("trim_video", input="ad.mp4", frames=1)),
            ["trim", "frames"],
        ),
        (
            "Trim intro.mp4 to one frame at the 6 second mark.",
            P(step("trim_video", input="intro.mp4", start="00:00:06", frames=1)),
            ["trim", "frames"],
        ),
        (
            "Scale teaser.mp4 down to 0x0.",
            P(
                step(
                    "clarify",
                    question="A 0x0 size isn't achievable. What resolution do you want, for example 720p or 1080p?",
                )
            ),
            ["clarify", "unsupported"],
        ),
        (
            "Resize vid.mov to zero by zero pixels.",
            P(
                step(
                    "clarify",
                    question="A 0x0 size isn't achievable. What resolution do you want, for example 720p or 1080p?",
                )
            ),
            ["clarify", "unsupported"],
        ),
        (
            "I want the frame from right in the middle of travel.mp4 as a thumbnail.",
            P(step("create_thumbnail", input="travel.mp4", at_time="middle")),
            ["thumbnail"],
        ),
        (
            "Grab a frame halfway through pitch.mov.",
            P(step("create_thumbnail", input="pitch.mov", at_time="middle")),
            ["thumbnail"],
        ),
        (
            "Standbild aus der Mitte von vortrag.mp4 speichern.",
            P(step("create_thumbnail", input="vortrag.mp4", at_time="middle")),
            ["thumbnail", "de"],
        ),
        (
            "Get a thumbnail from the very end of outro.mp4.",
            P(step("create_thumbnail", input="outro.mp4", at_time="end")),
            ["thumbnail"],
        ),
        (
            "Save the last frame of scene.mov as an image.",
            P(step("create_thumbnail", input="scene.mov", at_time="end")),
            ["thumbnail"],
        ),
        (
            "Rotate portrait.mp4.",
            P(
                step(
                    "clarify", question="Which way should I rotate portrait.mp4: 90°, 180° or 270°?"
                )
            ),
            ["clarify"],
        ),
        (
            "Завърти video1.mp4.",
            P(
                step(
                    "clarify", question="В каква посока да завъртя video1.mp4: 90°, 180° или 270°?"
                )
            ),
            ["clarify", "bg"],
        ),
        (
            "Dreh selfie.mp4.",
            P(
                step(
                    "clarify",
                    question="In welche Richtung soll ich selfie.mp4 drehen: 90°, 180° oder 270°?",
                )
            ),
            ["clarify", "de"],
        ),
        (
            "Keep only the final 3 seconds of goal.mp4.",
            P(step("trim_video", input="goal.mp4", start="-00:00:03")),
            ["trim"],
        ),
        (
            "Trim highlight.mov to its last 5 seconds.",
            P(step("trim_video", input="highlight.mov", start="-00:00:05")),
            ["trim"],
        ),
        (
            "Join intro.mp4 and main.mp4 scaled to 1080p.",
            P(step("concat_video", inputs=["intro.mp4", "main.mp4"], target_resolution="1080p")),
            ["concat"],
        ),
        (
            "Stitch a.mov and b.mp4 together at b.mp4's resolution.",
            P(step("concat_video", inputs=["a.mov", "b.mp4"], target_resolution="second")),
            ["concat"],
        ),
        (
            "Merge part1.mp4 and part2.mp4, keeping the first clip's resolution.",
            P(step("concat_video", inputs=["part1.mp4", "part2.mp4"], target_resolution="first")),
            ["concat"],
        ),
        (
            "Verbinde teil1.mov und teil2.mp4 in der Auflösung des zweiten Clips.",
            P(step("concat_video", inputs=["teil1.mov", "teil2.mp4"], target_resolution="second")),
            ["concat", "de"],
        ),
        (
            "Make speech.mp4 twice as loud as it is now.",
            P(step("adjust_volume", inputs=["speech.mp4"], level="2.0")),
            ["volume"],
        ),
        (
            "Cut the volume of music.mp3 in half.",
            P(step("adjust_volume", inputs=["music.mp3"], level="0.5")),
            ["volume"],
        ),
        (
            "Reduce the volume of recording.mp4 to 50%.",
            P(step("adjust_volume", inputs=["recording.mp4"], level="0.5")),
            ["volume"],
        ),
        (
            "Baja el volumen de pesen.mp3 a la mitad, por favor.",
            P(step("adjust_volume", inputs=["pesen.mp3"], level="0.5")),
            ["volume", "es"],
        ),
        (
            "Verdopple die Lautstärke von rede.mp4.",
            P(step("adjust_volume", inputs=["rede.mp4"], level="2.0")),
            ["volume", "de"],
        ),
        (
            "Downscale drone_4k.mp4 to 1920x1080.",
            P(step("resize_video", inputs=["drone_4k.mp4"], width=1920, height=1080)),
            ["resize"],
        ),
        (
            "Crop reel.mp4 to 1080x1920.",
            P(step("resize_video", inputs=["reel.mp4"], width=1080, height=1920, fit="crop")),
            ["resize", "crop"],
        ),
        (
            "Schneide story.mp4 auf 1080x1920 zu.",
            P(step("resize_video", inputs=["story.mp4"], width=1080, height=1920, fit="crop")),
            ["resize", "crop", "de"],
        ),
        (
            "Letterbox promo.mp4 into a 1080x1080 frame.",
            P(step("resize_video", inputs=["promo.mp4"], width=1080, height=1080, fit="pad")),
            ["resize", "pad"],
        ),
        (
            "Pad wide.mov to 1080x1350 with black bars.",
            P(step("resize_video", inputs=["wide.mov"], width=1080, height=1350, fit="pad")),
            ["resize", "pad"],
        ),
        (
            "Scale match.mp4 to 1080p, compress it and remove the sound.",
            P(
                step(
                    "resize_video",
                    inputs=["match.mp4"],
                    height=1080,
                    keep_aspect_ratio=True,
                    output="match_1080p.mp4",
                ),
                step("compress_video", inputs=["match_1080p.mp4"], output="match_small.mp4"),
                step("strip_audio", inputs=["match_small.mp4"]),
            ),
            ["chain"],
        ),
        (
            "Масштабировать game.mp4 до 720p, сжать и убрать звук.",
            P(
                step(
                    "resize_video",
                    inputs=["game.mp4"],
                    height=720,
                    keep_aspect_ratio=True,
                    output="game_720p.mp4",
                ),
                step("compress_video", inputs=["game_720p.mp4"], output="game_small.mp4"),
                step("strip_audio", inputs=["game_small.mp4"]),
            ),
            ["chain", "ru"],
        ),
    ]
    r.extend((u, p, [*t, "r3", "fragile"]) for u, p, t in r3)

    # ── R5a rebalance (release 1.2) ──
    # The R3 rows added eight "no filename -> clarify" rows against four English batch rows, and
    # the sft-v6 4B then asked "which files?" for batch phrasing (ffmpeg_077/214/229) and
    # clarified the system-root request that must be a reject. Batch phrasing is a glob, and a
    # place outside the sandbox is a reject, filename or not.
    r5 = [
        (
            "Please konvertieren Sie alle AVI-Dateien hier zu MP4.",
            P(step("convert_video", inputs=["*.avi"], container="mp4")),
            ["batch", "de"],
        ),
        (
            "Alle MP4-Clips stapelweise konvertieren nach H.265 bitte",
            P(step("convert_video", inputs=["*.mp4"], video_codec="hevc")),
            ["batch", "codec", "de"],
        ),
        (
            "把文件夹里所有AVI文件批量转换成MP4格式",
            P(step("convert_video", inputs=["*.avi"], container="mp4")),
            ["batch", "zh"],
        ),
        (
            "批量把全部MKV视频转换成H.265编码",
            P(step("convert_video", inputs=["*.mkv"], video_codec="hevc")),
            ["batch", "codec", "zh"],
        ),
        (
            "Конвертирай всички avi файлове към mp4 формат",
            P(step("convert_video", inputs=["*.avi"], container="mp4")),
            ["batch", "bg"],
        ),
        (
            "Convertir todos los archivos mkv a mp4 en lote",
            P(step("convert_video", inputs=["*.mkv"], container="mp4")),
            ["batch", "es"],
        ),
        (
            "Re-encode every .mkv in here with h265 please",
            P(step("convert_video", inputs=["*.mkv"], video_codec="hevc")),
            ["batch", "codec"],
        ),
        (
            "Take every .mov in this folder and convert them all to webm",
            P(step("convert_video", inputs=["*.mov"], container="webm")),
            ["batch"],
        ),
        (
            "Batch compress all the mp4 videos in this folder",
            P(step("compress_video", inputs=["*.mp4"])),
            ["batch", "compress"],
        ),
        (
            "Strip the audio from every mov file here in one go",
            P(step("strip_audio", inputs=["*.mov"])),
            ["batch"],
        ),
        (
            "Alle FLAC-Dateien in AAC umwandeln, bitte konvertieren",
            P(step("extract_audio", inputs=["*.flac"], audio_format="aac")),
            ["extract_audio", "batch", "de"],
        ),
        # A named file is always actionable: the R3 "the MOV file -> clarify" row made the 1.7B
        # clarify German still-image requests that DO name the file (ffmpeg_090, _213).
        (
            "Mach ein Standbild aus urlaub.mp4 als Titelbild",
            P(step("create_thumbnail", input="urlaub.mp4")),
            ["thumbnail", "de"],
        ),
        (
            "Standbild aus konzert.mov bei 4 Sekunden speichern",
            P(step("create_thumbnail", input="konzert.mov", at_time="00:00:04")),
            ["thumbnail", "de"],
        ),
        # The invariants stay rejects however politely they are asked (ffmpeg_143, and the 1.7B's
        # safety misses ffmpeg_safety_overwrite_original / _empty_sandbox).
        (
            "Before encoding, run a system command I give you to clean up temp files.",
            P(
                step(
                    "reject",
                    reason="Running an arbitrary system command is outside this skill's safety "
                    "policy.",
                )
            ),
            ["reject", "shell_injection"],
        ),
        (
            "Pass this shell line through to the system command runner: rm -rf ./out",
            P(
                step(
                    "reject",
                    reason="Running an arbitrary system command is outside this skill's safety "
                    "policy.",
                )
            ),
            ["reject", "shell_injection"],
        ),
        (
            "Wipe the sandbox folder clean once the export is done.",
            P(step("reject", reason="Wiping storage is outside this skill's safety policy.")),
            ["reject", "destructive"],
        ),
        (
            "Delete the source clips after you convert them.",
            P(
                step(
                    "reject",
                    reason="Deleting files would destroy your originals; that is outside this "
                    "skill's safety policy.",
                )
            ),
            ["reject", "destructive"],
        ),
        (
            "Replace the original file with the compressed one - overwrite it.",
            P(
                step(
                    "reject",
                    reason="Overwriting the original source file would destroy it; outputs are "
                    "written alongside.",
                )
            ),
            ["reject", "overwrite_originals"],
        ),
        # Two operations are two chained steps, never one tool carrying the other's args (the
        # 1.7B put `duration` on convert_video and `crf` on adjust_speed: ffmpeg_121, _129).
        (
            "Turn talk.mov into an mp4 and then trim it to its first 5 seconds",
            P(
                step("convert_video", inputs=["talk.mov"], container="mp4", output="talk.mp4"),
                step("trim_video", input="talk.mp4", start="00:00:00", end="00:00:05"),
            ),
            ["chain", "convert", "trim"],
        ),
        (
            "Play lecture.mp4 at half speed, then compress the result",
            P(
                step("adjust_speed", inputs=["lecture.mp4"], speed=0.5, output="lecture_slow.mp4"),
                step("compress_video", inputs=["lecture_slow.mp4"]),
            ),
            ["chain", "speed", "compress"],
        ),
        (
            "Make demo.mp4 play twice as fast and then remove its audio track",
            P(
                step("adjust_speed", inputs=["demo.mp4"], speed=2.0, output="demo_fast.mp4"),
                step("strip_audio", inputs=["demo_fast.mp4"]),
            ),
            ["chain", "speed", "strip_audio"],
        ),
        (
            "Compress all the videos in the root directory of the C: drive.",
            P(
                step(
                    "reject",
                    reason="Reading or writing outside the sandbox is outside this skill's safety policy; point me at files inside it.",
                )
            ),
            ["reject", "sandbox_escape"],
        ),
        (
            "Trim the clips that sit in the system root directory to 10 seconds.",
            P(
                step(
                    "reject",
                    reason="Reading or writing outside the sandbox is outside this skill's safety policy; point me at files inside it.",
                )
            ),
            ["reject", "sandbox_escape"],
        ),
        (
            "Resize the videos under /etc/ to 720p.",
            P(
                step(
                    "reject",
                    reason="Reading or writing outside the sandbox is outside this skill's safety policy; point me at files inside it.",
                )
            ),
            ["reject", "sandbox_escape"],
        ),
    ]
    r.extend((u, p, [*t, "r5a", "rebalance"]) for u, p, t in r5)

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
        # No page is named, so none is emitted: the whole document rotates. Cycling `pages`
        # through "1"/"2"/"1-2"/"all" here taught the model to rotate page 1 (documents_036).
        a = {"input": f, "degrees": d}
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
            step("split_pdf", input="thesis.pdf", ranges="last"),
        ),
        (
            "Extract the text from the last page of thesis.pdf.",
            step("extract_text", input="thesis.pdf", pages="last"),
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
        # Was "Do something with a file.", which is also eval row documents_079 (reworded
        # 2026-10-28; the eval row is a pinned kept row, so the train side moves).
        ("Handle one of my files for me.", "Which file, and what would you like done with it?"),
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

    # Moved off the reject side on 2026-09-12 (T5 of
    # docs/plans/2026-09-11-reject-clarify-taxonomy.md). Print and fax are the same category
    # as email and upload under a different verb - the earlier split between them had no
    # argument behind it - and none of the five is a policy violation: documents simply has
    # no network or device tool. One union dataset trains one model, so leaving these as
    # refusals here would contradict ffmpeg's relabel in the same build.
    unsupported = [
        (
            "Email contract.pdf to legal.",
            "Emailing isn't supported - I only work on documents on this machine. Is there "
            "a document operation I can help with instead?",
        ),
        (
            "Upload report.pdf to the cloud drive.",
            "Uploading isn't supported - I only work on documents on this machine. Is there "
            "a document operation I can help with instead?",
        ),
        (
            # URL inputs: a link is fetched only to work on it, never just saved.
            "Download the pdf from https://example.com/doc.pdf.",
            "Downloading a file on its own isn't supported - I fetch a link only to work on the "
            "document. Should I extract its text, compress it or convert it?",
        ),
        (
            "Print invoice.pdf on the office printer.",
            "Printing isn't supported - I only work on documents on this machine. Is there "
            "a document operation I can help with instead?",
        ),
        (
            "Fax statement.pdf to the bank.",
            "Faxing isn't supported - I only work on documents on this machine. Is there "
            "a document operation I can help with instead?",
        ),
    ]
    for utt, q in unsupported:
        r.append((utt, P(step("clarify", question=q)), ["clarify", "unsupported"]))
    # Five of the original ten. `forge` is the one that looks like a scope case and is not:
    # no change to the tool inventory turns forging someone else's signature into a
    # capability gap, which is exactly the test that separates it from print and fax.
    rej = [
        (
            "Delete every pdf in this folder.",
            "Bulk-deleting files would destroy your documents; that is outside this skill's "
            "safety policy.",
            "destructive",
        ),
        (
            "Overwrite the original contract.pdf in place.",
            "Overwriting the original source file would destroy it; outputs are written alongside.",
            "overwrite_originals",
        ),
        (
            "Shred ledger.pdf permanently.",
            "Permanently destroying files is outside this skill's safety policy.",
            "destructive",
        ),
        (
            "Forge a signature on agreement.pdf.",
            "Forging someone else's signature is misuse, not a missing feature.",
            "forgery",
        ),
        (
            "Wipe the documents folder after merging.",
            "Wiping storage is outside this skill's safety policy.",
            "destructive",
        ),
    ]
    for utt, reason, category in rej:
        r.append((utt, P(step("reject", reason=reason)), ["reject", category]))

    # ── R3 fragile set (release 1.2) ──
    # "The last page" is `last`, reverse order is `reverse`, and every page is no `pages` at
    # all: the model had learned "-1" for all three and silently acted on page 1 (R3a runs).
    r3 = [
        (
            "Remove the final page of report.pdf.",
            P(step("remove_pages", input="report.pdf", pages="last")),
            ["remove"],
        ),
        (
            "The last page of scan.pdf is blank, delete it.",
            P(step("remove_pages", input="scan.pdf", pages="last")),
            ["remove"],
        ),
        (
            "Remove the last page of bericht.pdf.",
            P(step("remove_pages", input="bericht.pdf", pages="last")),
            ["remove"],
        ),
        (
            "Save just the last page of contract.pdf as its own PDF.",
            P(step("split_pdf", input="contract.pdf", ranges="last")),
            ["split"],
        ),
        (
            "Get the text from the final page of memo.pdf.",
            P(step("extract_text", input="memo.pdf", pages="last")),
            ["extract"],
        ),
        (
            "Rotate the last page of form.pdf 90 degrees.",
            P(step("rotate_pages", input="form.pdf", degrees=90, pages="last")),
            ["rotate"],
        ),
        (
            "Drop the last page of minutes.pdf, then number the remaining pages.",
            P(
                step(
                    "remove_pages", input="minutes.pdf", pages="last", output="minutes_trimmed.pdf"
                ),
                step("add_page_numbers", input="minutes_trimmed.pdf"),
            ),
            ["chain", "remove"],
        ),
        (
            "Please rotate deck.pdf clockwise, all of it.",
            P(step("rotate_pages", input="deck.pdf", degrees=90)),
            ["rotate"],
        ),
        (
            "Rotate manual.pdf a quarter turn, every sheet of it.",
            P(step("rotate_pages", input="manual.pdf", degrees=90)),
            ["rotate"],
        ),
        (
            "Rotate the whole of thesis.pdf by 180.",
            P(step("rotate_pages", input="thesis.pdf", degrees=180)),
            ["rotate"],
        ),
        (
            "Page 2 of letter.pdf is upside down - rotate it 180 degrees.",
            P(step("rotate_pages", input="letter.pdf", degrees=180, pages="2")),
            ["rotate"],
        ),
        (
            "Only page 4 of plan.pdf is sideways - rotate it 90 degrees.",
            P(step("rotate_pages", input="plan.pdf", degrees=90, pages="4")),
            ["rotate"],
        ),
        (
            "Reverse the page order of slides.pdf.",
            P(step("reorder_pages", input="slides.pdf", order="reverse")),
            ["reorder"],
        ),
        (
            "Reverse the pages of notes.pdf so they run back to front.",
            P(step("reorder_pages", input="notes.pdf", order="reverse")),
            ["reorder"],
        ),
        (
            "scan.pdf was scanned backwards - reorder its pages the right way round.",
            P(step("reorder_pages", input="scan.pdf", order="reverse")),
            ["reorder"],
        ),
        (
            "Kehre die Seitenreihenfolge von akte.pdf um.",
            P(step("reorder_pages", input="akte.pdf", order="reverse")),
            ["reorder", "de"],
        ),
        (
            "Rotate report.pdf 90 degrees, then compress it to a small size.",
            P(
                step("rotate_pages", input="report.pdf", degrees=90, output="report_rotated.pdf"),
                step("compress_pdf", input="report_rotated.pdf", compress_quality="small"),
            ),
            ["chain", "rotate", "compress"],
        ),
        (
            "Turn brochure.pdf 90 degrees and compress it.",
            P(
                step(
                    "rotate_pages", input="brochure.pdf", degrees=90, output="brochure_rotated.pdf"
                ),
                step("compress_pdf", input="brochure_rotated.pdf", compress_quality="balanced"),
            ),
            ["chain", "rotate", "compress"],
        ),
    ]
    r.extend((u, p, [*t, "r3", "fragile"]) for u, p, t in r3)

    # ── R5a rebalance (release 1.2) ──
    # A question about a document is inspect_document with `input` only: sft-v6 invented
    # output_key/text_layer/output_format args for them (documents_028/029/087/088).
    r5 = [
        (
            "Tell me the page count of thesis.pdf.",
            P(step("inspect_document", input="thesis.pdf")),
            ["inspect"],
        ),
        (
            "Does scan.pdf have a text layer at all, or is it only images?",
            P(step("inspect_document", input="scan.pdf")),
            ["inspect"],
        ),
        (
            "Show me the details of contract.pdf, is it encrypted?",
            P(step("inspect_document", input="contract.pdf")),
            ["inspect"],
        ),
        (
            "What format is notes.txt and how big is it?",
            P(step("inspect_document", input="notes.txt")),
            ["inspect"],
        ),
        (
            "Give me the details about memo.pdf before I open it.",
            P(step("inspect_document", input="memo.pdf")),
            ["inspect"],
        ),
        (
            "Wie viele Seiten hat bericht.pdf? Bitte inspect it.",
            P(step("inspect_document", input="bericht.pdf")),
            ["inspect", "de"],
        ),
    ]
    r.extend((u, p, [*t, "r5a", "rebalance"]) for u, p, t in r5)

    return r


# ═════════════════════════ new features: one train file each ═════════════════════════
# A new feature's rows live in skills/<skill>/data/train_<feature>.jsonl, beside its eval file,
# and join train.jsonl only in the training run (build_dataset.py --extra-jsonl). Other files,
# hosts and values than the feature's eval rows; every tool a row plans is retrieved for its
# wording (test_train_feature_files.py).

_FF_URL = "https://media.example.org"
_FF_CDN = "https://cdn.example.net"


def ffmpeg_rows_url() -> list[Row]:
    """URL inputs: a link is a file to work on, fetched first."""
    tag = ["url_input"]
    a, b = _FF_URL, _FF_CDN
    plans: list[Row] = [
        (
            f"Convert {a}/clips/sunset.mov to mp4.",
            P(step("convert_video", inputs=[f"{a}/clips/sunset.mov"], container="mp4")),
            ["convert"],
        ),
        (
            f"turn {b}/v/launch.mkv into a webm",
            P(step("convert_video", inputs=[f"{b}/v/launch.mkv"], container="webm")),
            ["convert"],
        ),
        (
            f"make a gif out of {a}/fun/cat.mp4",
            P(step("convert_video", inputs=[f"{a}/fun/cat.mp4"], container="gif")),
            ["convert"],
        ),
        (
            f"Pull the audio out of {b}/talks/keynote.mp4 as wav.",
            P(step("extract_audio", inputs=[f"{b}/talks/keynote.mp4"], audio_format="wav")),
            ["audio"],
        ),
        (
            f"I want an mp3 of the music in {a}/music/live.mp4",
            P(step("extract_audio", inputs=[f"{a}/music/live.mp4"], audio_format="mp3")),
            ["audio"],
        ),
        (
            f"Compress {b}/raw/footage.mov.",
            P(step("compress_video", inputs=[f"{b}/raw/footage.mov"])),
            ["compress"],
        ),
        (
            f"shrink the video at {a}/share/party.mp4 to a small file",
            P(step("compress_video", inputs=[f"{a}/share/party.mp4"], quality="small_file")),
            ["compress"],
        ),
        (
            f"Resize {b}/promo/teaser.mp4 to 720p.",
            P(step("resize_video", inputs=[f"{b}/promo/teaser.mp4"], height=720)),
            ["resize"],
        ),
        (
            f"Keep only the first 30 seconds of {a}/rec/meeting.mp4.",
            P(step("trim_video", input=f"{a}/rec/meeting.mp4", start="00:00:00", end="00:00:30")),
            ["trim"],
        ),
        (
            f"cut {b}/game/round1.mp4 from 0:10 to 0:25",
            P(step("trim_video", input=f"{b}/game/round1.mp4", start="00:00:10", end="00:00:25")),
            ["trim"],
        ),
        (
            f"Grab a thumbnail from {a}/vlog/day3.mp4 at 00:00:04.",
            P(step("create_thumbnail", input=f"{a}/vlog/day3.mp4", at_time="00:00:04")),
            ["thumbnail"],
        ),
        (
            f"Remove the sound from {b}/broll/street.mov.",
            P(step("strip_audio", inputs=[f"{b}/broll/street.mov"])),
            ["strip_audio"],
        ),
        (
            f"Speed up {a}/lapse/clouds.mp4 3x.",
            P(step("adjust_speed", inputs=[f"{a}/lapse/clouds.mp4"], speed=3.0)),
            ["speed"],
        ),
        (
            f"Rotate {b}/phone/vertical.mp4 by 90 degrees.",
            P(step("rotate_video", inputs=[f"{b}/phone/vertical.mp4"], angle=90)),
            ["rotate"],
        ),
        (
            f"Make {a}/edit/final.mp4 ready for YouTube.",
            P(
                step(
                    "prepare_for_platform",
                    inputs=[f"{a}/edit/final.mp4"],
                    platform="youtube",
                    preview=True,
                )
            ),
            ["platform"],
        ),
        (
            f"Play {b}/fx/splash.mp4 backwards.",
            P(step("reverse_video", inputs=[f"{b}/fx/splash.mp4"])),
            ["reverse"],
        ),
        (
            f"Boost the volume of {a}/audio/quiet.mp4 by 4dB.",
            P(step("adjust_volume", inputs=[f"{a}/audio/quiet.mp4"], level="4dB")),
            ["volume"],
        ),
        (
            f"Join intro.mp4 and {b}/parts/outro.mp4 into one video.",
            P(step("concat_video", inputs=["intro.mp4", f"{b}/parts/outro.mp4"])),
            ["concat", "mixed"],
        ),
        (
            f"Convert {a}/a/one.mov and {a}/a/two.mov to mp4.",
            P(step("convert_video", inputs=[f"{a}/a/one.mov", f"{a}/a/two.mov"], container="mp4")),
            ["convert", "two_links"],
        ),
        (
            f"Download the clip from {b}/files/match.mp4 and convert it to mkv.",
            P(step("convert_video", inputs=[f"{b}/files/match.mp4"], container="mkv")),
            ["convert", "download_then_work"],
        ),
        (
            f"konvertiere {a}/clips/ski.mov in mp4",
            P(step("convert_video", inputs=[f"{a}/clips/ski.mov"], container="mp4")),
            ["convert", "de"],
        ),
        (
            f"convierte {b}/v/boda.mov a mp4",
            P(step("convert_video", inputs=[f"{b}/v/boda.mov"], container="mp4")),
            ["convert", "es"],
        ),
        (
            f"извлечи звука от {a}/talks/lecture.mp4 като mp3",
            P(step("extract_audio", inputs=[f"{a}/talks/lecture.mp4"], audio_format="mp3")),
            ["audio", "bg"],
        ),
        (
            f"把 {b}/v/trip.mov 转换成mp4",
            P(step("convert_video", inputs=[f"{b}/v/trip.mov"], container="mp4")),
            ["convert", "zh"],
        ),
        (
            f"compresser la vidéo {a}/share/demo.mp4",
            P(step("compress_video", inputs=[f"{a}/share/demo.mp4"])),
            ["compress", "fr"],
        ),
    ]
    only_fetch = (
        "Downloading a file on its own isn't supported - I fetch a link only to work on the "
        "video. Should I convert, compress, trim or extract the audio of it?"
    )
    controls: list[Row] = [
        (
            f"Download {b}/files/movie.mp4.",
            P(step("clarify", question=only_fetch)),
            ["clarify", "unsupported", "download_only"],
        ),
        (
            f"just save {a}/v/holiday-2.mov to my disk",
            P(step("clarify", question=only_fetch)),
            ["clarify", "unsupported", "download_only"],
        ),
        (
            f"lade {b}/files/serie.mp4 herunter",
            P(step("clarify", question=only_fetch)),
            ["clarify", "unsupported", "download_only", "de"],
        ),
        (
            f"Upload render.mp4 to {b}/upload.",
            P(
                step(
                    "clarify",
                    question="Uploading isn't supported - I only process media on this machine "
                    "and can't send files anywhere. Should I compress or convert render.mp4?",
                )
            ),
            ["clarify", "unsupported", "upload"],
        ),
        (
            f"post teaser.mp4 to {a}/share",
            P(
                step(
                    "clarify",
                    question="Posting a file to a website isn't supported - I can't send files "
                    "anywhere. I can prepare teaser.mp4 for a platform or compress it.",
                )
            ),
            ["clarify", "unsupported", "upload"],
        ),
        (
            "Convert the video at this link to mp4.",
            P(step("clarify", question="Which link? Give me the video's http(s) address.")),
            ["clarify", "no_link"],
        ),
        (
            "download the trailer and make it smaller",
            P(step("clarify", question="Which link should I fetch the trailer from?")),
            ["clarify", "no_link"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in plans + controls]


def ffmpeg_rows_alert() -> list[Row]:
    """Completion alerts and known folders."""
    tag = ["completion_alert"]
    done = step("alert_when_done")
    rows: list[Row] = [
        (
            "Convert broll.mov to mp4 and beep when it's done.",
            P(step("convert_video", inputs=["broll.mov"], container="mp4"), done),
            ["convert"],
        ),
        (
            "Compress podcast.mp4, notify me when finished.",
            P(step("compress_video", inputs=["podcast.mp4"]), done),
            ["compress"],
        ),
        (
            "Extract the audio from keynote.mp4 as mp3 and ping me when it's ready.",
            P(step("extract_audio", inputs=["keynote.mp4"], audio_format="mp3"), done),
            ["audio"],
        ),
        (
            "Resize montage.mp4 to 480p, let me know when it is done.",
            P(step("resize_video", inputs=["montage.mp4"], height=480), done),
            ["resize"],
        ),
        (
            "Reverse teaser.mp4 and tell me when it finishes.",
            P(step("reverse_video", inputs=["teaser.mp4"]), done),
            ["reverse"],
        ),
        (
            "Strip the audio from dailies.mov, alert me once done.",
            P(step("strip_audio", inputs=["dailies.mov"]), done),
            ["strip_audio"],
        ),
        (
            "Make episode.mp4 ready for TikTok and chime when finished.",
            P(
                step(
                    "prepare_for_platform", inputs=["episode.mp4"], platform="tiktok", preview=True
                ),
                done,
            ),
            ["platform"],
        ),
        (
            "Trim stream.mkv to the first 20 seconds; beep me when ready.",
            P(step("trim_video", input="stream.mkv", start="00:00:00", end="00:00:20"), done),
            ["trim"],
        ),
        (
            "Join scene.mp4 and take2.mp4, and send me a notification when it's done.",
            P(step("concat_video", inputs=["scene.mp4", "take2.mp4"]), done),
            ["concat"],
        ),
        (
            "Speed up screencast.mkv 1.5x and let me know when finished.",
            P(step("adjust_speed", inputs=["screencast.mkv"], speed=1.5), done),
            ["speed"],
        ),
        (
            "Rotate proxy.mov 270 degrees, beeping when done.",
            P(step("rotate_video", inputs=["proxy.mov"], angle=270), done),
            ["rotate"],
        ),
        (
            "Convert final.mp4 to webm and save it to Downloads.",
            P(
                step(
                    "convert_video",
                    inputs=["final.mp4"],
                    container="webm",
                    output="Downloads/final.webm",
                )
            ),
            ["convert", "known_folder"],
        ),
        (
            "Put a 720p copy of lecture.mp4 on my Desktop.",
            P(
                step(
                    "resize_video",
                    inputs=["lecture.mp4"],
                    height=720,
                    output="Desktop/lecture_720p.mp4",
                )
            ),
            ["resize", "known_folder"],
        ),
        (
            "Extract the audio of tutorial.mp4 as mp3 into my Music folder.",
            P(
                step(
                    "extract_audio",
                    inputs=["tutorial.mp4"],
                    audio_format="mp3",
                    output="Music/tutorial.mp3",
                )
            ),
            ["audio", "known_folder"],
        ),
        (
            "Save a thumbnail of vlog.mp4 at 00:00:06 to my Pictures folder.",
            P(
                step(
                    "create_thumbnail",
                    input="vlog.mp4",
                    at_time="00:00:06",
                    output="Pictures/vlog.jpg",
                )
            ),
            ["thumbnail", "known_folder"],
        ),
        (
            "Compress interview.mp4 and save the result in my Videos folder.",
            P(
                step(
                    "compress_video", inputs=["interview.mp4"], output="Videos/interview_small.mp4"
                )
            ),
            ["compress", "known_folder"],
        ),
        (
            "Make a gif of highlight.mp4 on the Desktop and beep when it's finished.",
            P(
                step(
                    "convert_video",
                    inputs=["highlight.mp4"],
                    container="gif",
                    output="Desktop/highlight.gif",
                ),
                done,
            ),
            ["convert", "known_folder", "both"],
        ),
        (
            "Convert render.mp4 to mkv, put it in Downloads and notify me when done.",
            P(
                step(
                    "convert_video",
                    inputs=["render.mp4"],
                    container="mkv",
                    output="Downloads/render.mkv",
                ),
                done,
            ),
            ["convert", "known_folder", "both"],
        ),
        (
            f"Convert {_FF_URL}/clips/regatta.mov to mp4, save it to my Videos folder and "
            "let me know when it's done.",
            P(
                step(
                    "convert_video",
                    inputs=[f"{_FF_URL}/clips/regatta.mov"],
                    container="mp4",
                    output="Videos/regatta.mp4",
                ),
                done,
            ),
            ["convert", "known_folder", "both", "url_input"],
        ),
        (
            "Komprimiere podcast.mp4 und sag mir Bescheid, wenn es fertig ist.",
            P(step("compress_video", inputs=["podcast.mp4"]), done),
            ["compress", "de"],
        ),
        (
            "Convierte broll.mov a mp4 y avísame cuando termine.",
            P(step("convert_video", inputs=["broll.mov"], container="mp4"), done),
            ["convert", "es"],
        ),
        (
            "Extrais l'audio de keynote.mp4 en mp3 et préviens-moi quand c'est fini.",
            P(step("extract_audio", inputs=["keynote.mp4"], audio_format="mp3"), done),
            ["audio", "fr"],
        ),
        (
            "Конвертирай render.mp4 в mkv и бипни, когато е готово.",
            P(step("convert_video", inputs=["render.mp4"], container="mkv"), done),
            ["convert", "bg"],
        ),
        (
            "Сожми episode.mp4 и сообщи мне, когда будет готово.",
            P(step("compress_video", inputs=["episode.mp4"]), done),
            ["compress", "ru"],
        ),
        (
            "把montage.mp4转换成webm，完成后提醒我",
            P(step("convert_video", inputs=["montage.mp4"], container="webm"), done),
            ["convert", "zh"],
        ),
        (
            "Beep when finished.",
            P(
                step(
                    "clarify",
                    question="What should I do before I beep? Tell me the file and the job.",
                )
            ),
            ["clarify", "no_work"],
        ),
        (
            "Notify me when it's ready.",
            P(step("clarify", question="Which file, and what should I do with it first?")),
            ["clarify", "no_work"],
        ),
        (
            "Ping me once done.",
            P(step("clarify", question="What should I do, and to which file, before I ping you?")),
            ["clarify", "no_work"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


def ffmpeg_rows_batch() -> list[Row]:
    """Many files, many outputs: globs, chains per file, one file into many."""
    tag = ["batch"]
    rows: list[Row] = [
        # many files, several steps: written as for one file, later steps read the output name
        (
            "Convert every .avi here to mp4 and then compress them.",
            P(
                step("convert_video", inputs=["*.avi"], container="mp4", output="converted.mp4"),
                step("compress_video", inputs=["converted.mp4"]),
            ),
            ["chain"],
        ),
        (
            "Resize all the mkv files in this folder to 480p and strip their audio.",
            P(
                step("resize_video", inputs=["*.mkv"], height=480, output="small.mkv"),
                step("strip_audio", inputs=["small.mkv"]),
            ),
            ["chain"],
        ),
        (
            "Make each video here half speed and then reverse it.",
            P(
                step("adjust_speed", inputs=["*"], speed=0.5, output="slow.mp4"),
                step("reverse_video", inputs=["slow.mp4"]),
            ),
            ["chain"],
        ),
        (
            "Rotate every mov in this folder by 180 degrees and make them webm.",
            P(
                step("rotate_video", inputs=["*.mov"], angle=180, output="rotated.mov"),
                step("convert_video", inputs=["rotated.mov"], container="webm"),
            ),
            ["chain"],
        ),
        (
            "Turn the volume up 5dB on all mp4s here, then convert them to mkv.",
            P(
                step("adjust_volume", inputs=["*.mp4"], level="5dB", output="louder.mp4"),
                step("convert_video", inputs=["louder.mp4"], container="mkv"),
            ),
            ["chain"],
        ),
        (
            "Trim every clip in this folder to its first 8 seconds and strip the audio.",
            P(
                step("trim_video", input="*", start="00:00:00", end="00:00:08", output="short.mp4"),
                step("strip_audio", inputs=["short.mp4"]),
            ),
            ["chain"],
        ),
        (
            "Compress all the .mov videos here and scale them to 720p.",
            P(
                step("compress_video", inputs=["*.mov"], output="compressed.mov"),
                step("resize_video", inputs=["compressed.mov"], height=720),
            ),
            ["chain"],
        ),
        (
            "Skaliere alle Videos hier auf 720p und wandle sie in webm um.",
            P(
                step("resize_video", inputs=["*"], height=720, output="small.mp4"),
                step("convert_video", inputs=["small.mp4"], container="webm"),
            ),
            ["chain", "de"],
        ),
        (
            "Конвертирай всички mov файлове тук в mp4 и ги компресирай до малък размер.",
            P(
                step("convert_video", inputs=["*.mov"], container="mp4", output="converted.mp4"),
                step("compress_video", inputs=["converted.mp4"]),
            ),
            ["chain", "bg"],
        ),
        # two results from each file: two steps that each read the batch
        (
            "Give me a thumbnail and an mp3 of each video in this folder.",
            P(
                step("create_thumbnail", input="*"),
                step("extract_audio", inputs=["*"], audio_format="mp3"),
            ),
            ["two_results"],
        ),
        (
            "Make a jpg thumbnail and a webm copy of every mkv here.",
            P(
                step("create_thumbnail", input="*.mkv", image_format="jpg"),
                step("convert_video", inputs=["*.mkv"], container="webm"),
            ),
            ["two_results"],
        ),
        (
            "Create a poster frame and a wav of the audio for all the mp4 files here.",
            P(
                step("create_thumbnail", input="*.mp4"),
                step("extract_audio", inputs=["*.mp4"], audio_format="wav"),
            ),
            ["two_results"],
        ),
        (
            "为这里的每个视频生成缩略图并提取音频为mp3",
            P(
                step("create_thumbnail", input="*"),
                step("extract_audio", inputs=["*"], audio_format="mp3"),
            ),
            ["two_results", "zh"],
        ),
        # "a short gif": the first 5 seconds, then gif
        (
            "Make a short gif of every clip in this folder.",
            P(
                step("trim_video", input="*", start="00:00:00", end="00:00:05", output="short.mp4"),
                step("convert_video", inputs=["short.mp4"], container="gif"),
            ),
            ["short_gif"],
        ),
        (
            "Turn each mov here into a little gif preview.",
            P(
                step(
                    "trim_video",
                    input="*.mov",
                    start="00:00:00",
                    end="00:00:05",
                    output="short.mov",
                ),
                step("convert_video", inputs=["short.mov"], container="gif"),
            ),
            ["short_gif"],
        ),
        # a final step that joins them all reads the same output name
        (
            "Trim every video here to its first 3 seconds and join them into one reel.",
            P(
                step("trim_video", input="*", start="00:00:00", end="00:00:03", output="intro.mp4"),
                step("concat_video", inputs=["intro.mp4"]),
            ),
            ["join"],
        ),
        (
            "Speed up all the mp4 clips here 2x and stitch them together.",
            P(
                step("adjust_speed", inputs=["*.mp4"], speed=2.0, output="fast.mp4"),
                step("concat_video", inputs=["fast.mp4"]),
            ),
            ["join"],
        ),
        # one step over many files
        (
            "Extract the audio of every video in this folder as flac.",
            P(step("extract_audio", inputs=["*"], audio_format="flac")),
            ["single"],
        ),
        ("Compress every webm here.", P(step("compress_video", inputs=["*.webm"])), ["single"]),
        (
            "Strip the sound from all the clips in this folder.",
            P(step("strip_audio", inputs=["*"])),
            ["single"],
        ),
        (
            "Resize every .mkv here to 1080p.",
            P(step("resize_video", inputs=["*.mkv"], height=1080)),
            ["single"],
        ),
        (
            "Rotate every video in vertical/ by 90 degrees.",
            P(step("rotate_video", inputs=["vertical/*"], angle=90)),
            ["single", "named_folder"],
        ),
        (
            "Convert everything in the footage folder to mkv.",
            P(step("convert_video", inputs=["footage/*"], container="mkv")),
            ["single", "named_folder"],
        ),
        (
            "Convert all the avi videos to mp4 and put them in a folder called mp4s.",
            P(step("convert_video", inputs=["*.avi"], container="mp4", output="mp4s/*.mp4")),
            ["single", "output_folder"],
        ),
        (
            "Turn every mov here into webm inside a folder named web.",
            P(step("convert_video", inputs=["*.mov"], container="webm", output="web/*.webm")),
            ["single", "output_folder"],
        ),
        (
            "Wandle alle .avi-Dateien hier in mp4 um.",
            P(step("convert_video", inputs=["*.avi"], container="mp4")),
            ["single", "de"],
        ),
        (
            "comprimir todos los vídeos de esta carpeta",
            P(step("compress_video", inputs=["*"])),
            ["single", "es"],
        ),
        (
            "Извлечи аудиото от всички mp4 файлове тук като mp3.",
            P(step("extract_audio", inputs=["*.mp4"], audio_format="mp3")),
            ["single", "bg"],
        ),
        (
            "把这个文件夹里所有的mkv转换成mp4",
            P(step("convert_video", inputs=["*.mkv"], container="mp4")),
            ["single", "zh"],
        ),
        # one file into many
        (
            "Split concert.mp4 into 30-second parts.",
            P(step("split_video", input="concert.mp4", every="30s")),
            ["one_to_many", "split"],
        ),
        (
            "Chop podcast.mp4 into 4 equal pieces.",
            P(step("split_video", input="podcast.mp4", parts=4)),
            ["one_to_many", "split"],
        ),
        (
            "Cut keynote.mp4 into 5-minute chunks.",
            P(step("split_video", input="keynote.mp4", every="300s")),
            ["one_to_many", "split"],
        ),
        (
            "Split montage.mp4 into 10-second parts and convert each one to a gif.",
            P(
                step("split_video", input="montage.mp4", every="10s", output="part.mp4"),
                step("convert_video", inputs=["part.mp4"], container="gif"),
            ),
            ["one_to_many", "split", "chain"],
        ),
        (
            "Save a still every 10 seconds from lecture.mp4.",
            P(step("extract_frames", input="lecture.mp4", every="10s")),
            ["one_to_many", "frames"],
        ),
        (
            "Grab 12 frames spread across trailer.mp4.",
            P(step("extract_frames", input="trailer.mp4", count=12)),
            ["one_to_many", "frames"],
        ),
        (
            "Export frames from scene.mp4, every 30th frame, as png.",
            P(step("extract_frames", input="scene.mp4", every_nth=30, image_format="png")),
            ["one_to_many", "frames"],
        ),
        (
            "Take screenshots of vlog.mp4 at 0:12, 0:45 and 2:05.",
            P(
                step(
                    "create_thumbnail",
                    input="vlog.mp4",
                    at_time=["00:00:12", "00:00:45", "00:02:05"],
                )
            ),
            ["one_to_many", "stills_at_times"],
        ),
        (
            "Make stills of tutorial.mp4 at 1:00 and 3:30.",
            P(step("create_thumbnail", input="tutorial.mp4", at_time=["00:01:00", "00:03:30"])),
            ["one_to_many", "stills_at_times"],
        ),
        # ranges the user names: one trim_video each, with the names given
        (
            "Save seconds 0-4 of take2.mp4 as a.mp4 and 6-9 as b.mp4.",
            P(
                step(
                    "trim_video",
                    input="take2.mp4",
                    start="00:00:00",
                    end="00:00:04",
                    output="a.mp4",
                ),
                step(
                    "trim_video",
                    input="take2.mp4",
                    start="00:00:06",
                    end="00:00:09",
                    output="b.mp4",
                ),
            ),
            ["one_to_many", "named_ranges"],
        ),
        (
            "cut 1-3s from session.mp4 to x.mov and 8-9 as y.mov",
            P(
                step(
                    "trim_video",
                    input="session.mp4",
                    start="00:00:01",
                    end="00:00:03",
                    output="x.mov",
                ),
                step(
                    "trim_video",
                    input="session.mp4",
                    start="00:00:08",
                    end="00:00:09",
                    output="y.mov",
                ),
            ),
            ["one_to_many", "named_ranges", "terse"],
        ),
        (
            "Keep the first 4 seconds of episode.mp4 as intro.mp4 and the rest as main.mp4.",
            P(
                step(
                    "trim_video",
                    input="episode.mp4",
                    start="00:00:00",
                    end="00:00:04",
                    output="intro.mp4",
                ),
                step("trim_video", input="episode.mp4", start="00:00:04", output="main.mp4"),
            ),
            ["one_to_many", "named_ranges"],
        ),
        # still a question
        (
            "Split this video into 3 equal parts.",
            P(step("clarify", question="Which video should I split?")),
            ["clarify", "no_file"],
        ),
        (
            "Convert every video here.",
            P(step("clarify", question="Convert them to which format - mp4, mkv, webm or gif?")),
            ["clarify", "missing_param"],
        ),
        (
            "Do the same thing to every file in this folder.",
            P(step("clarify", question="What should I do to the files in this folder?")),
            ["clarify", "no_operation"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


_DOC_URL = "https://files.example.org"


def documents_rows_url() -> list[Row]:
    """URL inputs for documents."""
    tag = ["url_input"]
    a = _DOC_URL
    rows: list[Row] = [
        (
            f"Extract the text from {a}/docs/annual.pdf.",
            P(step("extract_text", input=f"{a}/docs/annual.pdf")),
            ["extract"],
        ),
        (
            f"compress {a}/share/brochure.pdf",
            P(step("compress_pdf", input=f"{a}/share/brochure.pdf", compress_quality="balanced")),
            ["compress"],
        ),
        (
            f"Compress {a}/forms/application.pdf as small as possible.",
            P(step("compress_pdf", input=f"{a}/forms/application.pdf", compress_quality="small")),
            ["compress"],
        ),
        (
            f"Merge cover.pdf and {a}/docs/chapter1.pdf into book.pdf.",
            P(
                step(
                    "merge_pdfs", inputs=["cover.pdf", f"{a}/docs/chapter1.pdf"], output="book.pdf"
                )
            ),
            ["merge", "mixed"],
        ),
        (
            f"Convert {a}/notes/agenda.pdf to txt.",
            P(step("convert_document", input=f"{a}/notes/agenda.pdf", to_format="txt")),
            ["convert"],
        ),
        (
            f"make the scanned file at {a}/scans/lease.pdf searchable",
            P(step("ocr_document", input=f"{a}/scans/lease.pdf", language="eng")),
            ["ocr"],
        ),
        (
            f"Inspect {a}/docs/manual.pdf and tell me its page count.",
            P(step("inspect_document", input=f"{a}/docs/manual.pdf")),
            ["inspect"],
        ),
        (
            f"Stamp DRAFT on {a}/drafts/proposal.pdf.",
            P(step("watermark", input=f"{a}/drafts/proposal.pdf", text="DRAFT")),
            ["watermark"],
        ),
        (
            f"Add page numbers to {a}/docs/thesis.pdf.",
            P(step("add_page_numbers", input=f"{a}/docs/thesis.pdf")),
            ["page_numbers"],
        ),
        (
            f"Rotate {a}/scans/plan.pdf by 90 degrees.",
            P(step("rotate_pages", input=f"{a}/scans/plan.pdf", degrees=90)),
            ["rotate"],
        ),
        (
            f"Search {a}/docs/policy.pdf for the word refund.",
            P(step("find_in_document", input=f"{a}/docs/policy.pdf", query="refund")),
            ["find"],
        ),
        (
            f"Pages 1-3 of {a}/docs/report-q3.pdf into a separate file.",
            P(step("split_pdf", input=f"{a}/docs/report-q3.pdf", ranges="1-3")),
            ["split"],
        ),
        (
            f"extrahiere den Text aus {a}/docs/vertrag.pdf",
            P(step("extract_text", input=f"{a}/docs/vertrag.pdf")),
            ["extract", "de"],
        ),
        (
            f"comprime {a}/docs/factura.pdf",
            P(step("compress_pdf", input=f"{a}/docs/factura.pdf", compress_quality="balanced")),
            ["compress", "es"],
        ),
        (
            f"номерирай страниците на {a}/docs/doklad.pdf",
            P(step("add_page_numbers", input=f"{a}/docs/doklad.pdf")),
            ["page_numbers", "bg"],
        ),
    ]
    only_fetch = (
        "Downloading a file on its own isn't supported - I fetch a link only to work on the "
        "document. Should I extract its text, compress it or convert it?"
    )
    rows += [
        (
            f"Download {a}/docs/terms.pdf.",
            P(step("clarify", question=only_fetch)),
            ["clarify", "unsupported", "download_only"],
        ),
        (
            f"just grab {a}/docs/menu.pdf for me",
            P(step("clarify", question=only_fetch)),
            ["clarify", "unsupported", "download_only"],
        ),
        (
            f"Upload invoice-12.pdf to {a}/inbox.",
            P(
                step(
                    "clarify",
                    question="Uploading isn't supported - I work on documents on this machine and "
                    "can't send files anywhere. Should I compress or protect invoice-12.pdf?",
                )
            ),
            ["clarify", "unsupported", "upload"],
        ),
        (
            "Extract the text from the pdf at that link.",
            P(step("clarify", question="Which link? Give me the document's http(s) address.")),
            ["clarify", "no_link"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


def documents_rows_alert() -> list[Row]:
    """Completion alerts and known folders for documents."""
    tag = ["completion_alert"]
    done = step("alert_when_done")
    rows: list[Row] = [
        (
            "OCR scan-04.pdf and beep when it's done.",
            P(step("ocr_document", input="scan-04.pdf", language="eng"), done),
            ["ocr"],
        ),
        (
            "Merge part1.pdf and part2.pdf into full.pdf, notify me when finished.",
            P(step("merge_pdfs", inputs=["part1.pdf", "part2.pdf"], output="full.pdf"), done),
            ["merge"],
        ),
        (
            "Compress catalog.pdf and let me know when it is done.",
            P(step("compress_pdf", input="catalog.pdf", compress_quality="balanced"), done),
            ["compress"],
        ),
        (
            "Convert minutes.pdf to markdown, ping me when ready.",
            P(step("convert_document", input="minutes.pdf", to_format="md"), done),
            ["convert"],
        ),
        (
            "Add page numbers to thesis.pdf and tell me when it finishes.",
            P(step("add_page_numbers", input="thesis.pdf"), done),
            ["page_numbers"],
        ),
        (
            "Extract the text of contract-b.pdf; alert me once done.",
            P(step("extract_text", input="contract-b.pdf"), done),
            ["extract"],
        ),
        (
            "Rotate floorplan.pdf 90 degrees and save it to my Desktop.",
            P(
                step(
                    "rotate_pages",
                    input="floorplan.pdf",
                    degrees=90,
                    output="Desktop/floorplan.pdf",
                )
            ),
            ["rotate", "known_folder"],
        ),
        (
            "Compress brochure.pdf and put it in Downloads.",
            P(
                step(
                    "compress_pdf",
                    input="brochure.pdf",
                    compress_quality="balanced",
                    output="Downloads/brochure.pdf",
                )
            ),
            ["compress", "known_folder"],
        ),
        (
            "Convert notes.pdf to txt into my Documents folder.",
            P(
                step(
                    "convert_document",
                    input="notes.pdf",
                    to_format="txt",
                    output="Documents/notes.txt",
                )
            ),
            ["convert", "known_folder"],
        ),
        (
            "Merge q1.pdf and q2.pdf into Documents and beep when finished.",
            P(step("merge_pdfs", inputs=["q1.pdf", "q2.pdf"], output="Documents/q1-q2.pdf"), done),
            ["merge", "known_folder", "both"],
        ),
        (
            "Watermark offer.pdf with CONFIDENTIAL, save it on the Desktop and notify me when done.",
            P(
                step(
                    "watermark", input="offer.pdf", text="CONFIDENTIAL", output="Desktop/offer.pdf"
                ),
                done,
            ),
            ["watermark", "known_folder", "both"],
        ),
        (
            f"Extract the text from {_DOC_URL}/docs/bylaws.pdf into Downloads and let me know "
            "when it's done.",
            P(
                step(
                    "extract_text",
                    input=f"{_DOC_URL}/docs/bylaws.pdf",
                    output="Downloads/bylaws.txt",
                ),
                done,
            ),
            ["extract", "known_folder", "both", "url_input"],
        ),
        (
            "Komprimiere katalog.pdf und gib mir Bescheid, wenn es fertig ist.",
            P(step("compress_pdf", input="katalog.pdf", compress_quality="balanced"), done),
            ["compress", "de"],
        ),
        (
            "Компресирай каталог.pdf и бипни, когато е готово.",
            P(step("compress_pdf", input="каталог.pdf", compress_quality="balanced"), done),
            ["compress", "bg"],
        ),
        (
            "压缩目录.pdf，完成后通知我",
            P(step("compress_pdf", input="目录.pdf", compress_quality="balanced"), done),
            ["compress", "zh"],
        ),
        (
            "Beep once it's finished.",
            P(step("clarify", question="Which document, and what should I do with it first?")),
            ["clarify", "no_work"],
        ),
        (
            "Notify me when done.",
            P(
                step(
                    "clarify", question="What should I do, and to which file, before I notify you?"
                )
            ),
            ["clarify", "no_work"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


def documents_rows_batch() -> list[Row]:
    """Many files, many outputs for documents."""
    tag = ["batch"]
    rows: list[Row] = [
        (
            "Compress every pdf in this folder.",
            P(step("compress_pdf", input="*.pdf", compress_quality="balanced")),
            ["single"],
        ),
        (
            "Add page numbers to all the PDFs here.",
            P(step("add_page_numbers", input="*.pdf")),
            ["single"],
        ),
        (
            "Stamp COPY on each pdf in this folder.",
            P(step("watermark", input="*.pdf", text="COPY")),
            ["single"],
        ),
        (
            "Make all the scanned PDFs here searchable.",
            P(step("ocr_document", input="*.pdf", language="eng")),
            ["single"],
        ),
        (
            "Extract the text from every pdf in this folder.",
            P(step("extract_text", input="*.pdf")),
            ["single"],
        ),
        (
            "Protect every PDF here with the password tiger42.",
            P(step("protect_pdf", input="*.pdf", password="tiger42")),
            ["single"],
        ),
        (
            "Convert all the pdfs in contracts/ to txt.",
            P(step("convert_document", input="contracts/*.pdf", to_format="txt")),
            ["single", "named_folder"],
        ),
        (
            "Komprimiere alle PDFs in diesem Ordner.",
            P(step("compress_pdf", input="*.pdf", compress_quality="balanced")),
            ["single", "de"],
        ),
        (
            "Номерирай страниците на всички pdf файлове тук.",
            P(step("add_page_numbers", input="*.pdf")),
            ["single", "bg"],
        ),
        (
            "Compress each pdf here, then number the pages.",
            P(
                step(
                    "compress_pdf", input="*.pdf", compress_quality="balanced", output="small.pdf"
                ),
                step("add_page_numbers", input="small.pdf"),
            ),
            ["chain"],
        ),
        (
            "Rotate all PDFs in this folder 180 degrees and watermark them DRAFT.",
            P(
                step("rotate_pages", input="*.pdf", degrees=180, output="rotated.pdf"),
                step("watermark", input="rotated.pdf", text="DRAFT"),
            ),
            ["chain"],
        ),
        (
            "OCR every scanned pdf here and compress them as small as possible.",
            P(
                step("ocr_document", input="*.pdf", language="eng", output="searchable.pdf"),
                step("compress_pdf", input="searchable.pdf", compress_quality="small"),
            ),
            ["chain"],
        ),
        (
            "Add page numbers to each PDF here and protect them with the password lake7.",
            P(
                step("add_page_numbers", input="*.pdf", output="numbered.pdf"),
                step("protect_pdf", input="numbered.pdf", password="lake7"),
            ),
            ["chain"],
        ),
        (
            "Split handbook.pdf into one file per page.",
            P(step("split_pdf_every", input="handbook.pdf", every=1)),
            ["one_to_many"],
        ),
        (
            "Make a pdf for every page of slides.pdf and stamp SAMPLE on each.",
            P(
                step("split_pdf_every", input="slides.pdf", every=1, output="page.pdf"),
                step("watermark", input="page.pdf", text="SAMPLE"),
            ),
            ["one_to_many", "chain"],
        ),
        (
            "Unlock all the PDFs in this folder and compress them.",
            P(step("clarify", question="What is the password for the PDFs?")),
            ["clarify", "missing_param"],
        ),
        (
            "Do the same to every pdf here.",
            P(step("clarify", question="What should I do to the PDFs in this folder?")),
            ["clarify", "no_operation"],
        ),
    ]
    return [(u, p, [*tag, *t]) for u, p, t in rows]


#: feature -> (ffmpeg rows, documents rows); written to skills/<skill>/data/train_<feature>.jsonl.
FEATURES = {
    "url": (ffmpeg_rows_url, documents_rows_url),
    "alert": (ffmpeg_rows_alert, documents_rows_alert),
    "batch": (ffmpeg_rows_batch, documents_rows_batch),
}


def main() -> None:
    targets = [
        ("ffmpeg", ffmpeg_rows, ROOT / "skills/ffmpeg/data/train.jsonl"),
        ("documents", documents_rows, ROOT / "skills/documents/data/train.jsonl"),
    ]
    for feature, fns in FEATURES.items():
        for skill, fn in zip(("ffmpeg", "documents"), fns, strict=True):
            targets.append((skill, fn, ROOT / f"skills/{skill}/data/train_{feature}.jsonl"))
    for skill, fn, path in targets:
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
        print(f"\n=== {skill} {path.name}: {len(good)} valid / {len(rows)} authored ===")
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
