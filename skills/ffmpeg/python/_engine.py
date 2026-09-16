"""ffmpeg skill — engine module (see handlers.py / SPEC.md)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from knaif.handler_api import HandlerContext

# Bundle root — this module lives in the bundle's `python/` package, so declarative data
# (vocab.yaml, profiles/) sits one level up at the bundle top, shared with other runtimes.
_BUNDLE_DIR = Path(__file__).resolve().parent.parent

# Declarative vocabulary / lookup tables (shared-with-Rust data; see vocab.yaml).
_VOCAB_PATH = _BUNDLE_DIR / "vocab.yaml"
with _VOCAB_PATH.open(encoding="utf-8") as _vfh:
    _VOCAB: dict[str, Any] = yaml.safe_load(_vfh) or {}


def _coerce_inputs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ValueError("'inputs' must be a string or list of strings.")


#: Suffix walked when an explicit output would overwrite its own input. The user asked for
#: a *copy*, so "_converted" says what happened; the numbered variants exist because the
#: first candidate can itself be taken.
_COLLISION_SUFFIX = "_converted"


def next_free_output(requested: Path, taken: set[Path]) -> Path:
    """First free ``<stem>_converted[_N]<.ext>``. **Never returns *requested* itself.**

    *taken* holds every path the caller has committed to — each input of the plan and each
    output the plan declares — and the filesystem is consulted on top of it.

    Always advancing is the contract, not an implementation detail. The caller only reaches
    here once a self-overwrite is established, and a chained intermediate that does not exist
    on disk yet collides exactly as hard as one that does: an earlier draft checked only
    ``exists()`` and so handed the colliding path straight back for
    ``trim -> clip_trimmed.mp4`` feeding ``convert -> clip_trimmed.mp4``, leaving the ``-y``
    truncation in place while telling the user it had been renamed. It also made the result
    depend on whether the plan had been run before.

    **Every rendered ffmpeg command carries ``-y``**, so the replacement must be free too:
    otherwise the fix destroys a file that is already there, another input of the same plan,
    or a later step's output. The walk is deterministic, so two runs of the same plan on the
    same tree land on the same name.
    """

    def is_free(candidate: Path) -> bool:
        return candidate not in taken and not candidate.exists()

    stem, ext = requested.stem, requested.suffix
    candidate = requested.with_name(f"{stem}{_COLLISION_SUFFIX}{ext}")
    n = 2
    while not is_free(candidate):
        candidate = requested.with_name(f"{stem}{_COLLISION_SUFFIX}_{n}{ext}")
        n += 1
    return candidate


def _assert_in_sandbox(p: Path, sandbox: Path | None) -> None:
    """Raise ValueError if *p* (resolved) is not inside *sandbox* (resolved).

    When *sandbox* is ``None`` (open / CLI mode) the check is skipped entirely.
    """
    if sandbox is None:
        return
    try:
        p.resolve().relative_to(sandbox.resolve())
    except ValueError:
        raise ValueError(
            f"Path {str(p)!r} is outside the sandbox {str(sandbox.resolve())!r}"
        ) from None


# ─────────────────────────────────────────────────────────────────────────────
# Probe normalisation.
# ─────────────────────────────────────────────────────────────────────────────


def _parse_fps(rate_str: str | None) -> float | None:
    """Parse a fraction fps string like '30000/1001' or '25/1' to a float.

    Returns None for absent, unparsable, or zero-denominator values.
    """
    if not rate_str:
        return None
    try:
        parts = rate_str.split("/")
        if len(parts) == 2:
            num, den = float(parts[0]), float(parts[1])
            return num / den if den != 0 else None
        return float(rate_str)
    except (ValueError, ZeroDivisionError):
        return None


def _summarise_probe(file: Path, probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = v_stream.get("width") if v_stream else None
    height = v_stream.get("height") if v_stream else None
    duration = fmt.get("duration")
    try:
        duration_f: float | None = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_f = None
    fps: float | None = None
    if v_stream:
        fps = _parse_fps(v_stream.get("avg_frame_rate")) or _parse_fps(v_stream.get("r_frame_rate"))
    return {
        "file": str(file),
        "container": fmt.get("format_name", "").split(",")[0],
        "duration": duration_f,
        "size_bytes": int(fmt.get("size", 0)) if fmt.get("size") else None,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": v_stream.get("codec_name") if v_stream else None,
        "audio_codec": a_stream.get("codec_name") if a_stream else None,
        "has_audio": a_stream is not None,
    }


_AUDIO_EXTS: dict[str, str] = dict(_VOCAB["audio_ext_codec"])


def _dummy_probe(file: Path) -> dict[str, Any]:
    """Return a placeholder probe used in dry-run when the file doesn't exist.

    An audio-only extension (mp3, wav, …) must probe as audio-only — otherwise
    audio operations render as video (.mp4 / -c:v copy / aac) in dry-run, which
    is how the success eval builds commands before fixtures are copied in.
    """
    suffix = file.suffix.lstrip(".").lower() or "mp4"
    if suffix in _AUDIO_EXTS:
        return {
            "file": str(file),
            "container": suffix,
            "duration": 60.0,
            "size_bytes": None,
            "width": None,
            "height": None,
            "fps": None,
            "video_codec": None,
            "audio_codec": _AUDIO_EXTS[suffix],
            "has_audio": True,
        }
    return {
        "file": str(file),
        "container": suffix,
        "duration": 60.0,
        "size_bytes": None,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Profile loading.
# ─────────────────────────────────────────────────────────────────────────────


def _profiles_root(ctx: HandlerContext) -> Path:
    return ctx.skill_dir / "profiles"


# ─────────────────────────────────────────────────────────────────────────────
# Enum normalization — turn near-miss model values into valid profiles instead
# of crashing the deterministic layer. Keeps the small-model failure surface as
# honest clarifies/plans rather than raw exceptions. See
# docs/audits/2026-05-29-project-audit.md and the eval FAIL analysis.
# ─────────────────────────────────────────────────────────────────────────────

_PACKAGE_PROFILES = _BUNDLE_DIR / "profiles"

# Common synonyms the model emits for a platform that maps to a real profile.
_PLATFORM_ALIASES = dict(_VOCAB["platform_aliases"])

# Matches raw CRF specs the model passes as a quality value: crf18, crf=26,
# "crf 20", crf-23, crf_18.
_CRF_RE = re.compile(r"^\s*crf\s*[=:_\- ]?\s*(\d{1,2})\s*$", re.IGNORECASE)


def _normalize_platform(platform: Any) -> Any:
    if not isinstance(platform, str):
        return platform
    key = platform.strip().lower()
    return _PLATFORM_ALIASES.get(key, key)


def _valid_platforms(profiles_root: Path | None = None) -> set[str]:
    root = (profiles_root or _PACKAGE_PROFILES) / "platforms"
    return {p.stem for p in root.glob("*.yaml")}


def _crf_to_profile_name(crf: int) -> str:
    """Map a raw CRF value to the nearest named quality profile.

    Mirrors the CRF mapping in prompt.yaml so the deterministic layer stays
    consistent with the model's instructions even when the model leaks a raw
    CRF value instead of a profile name.
    """
    if crf <= 20:
        return "high_quality"
    if crf <= 24:
        return "visually_good"
    if crf <= 27:
        return "balanced"
    return "small_file"


def _platform_clarify(raw_platform: Any) -> list[dict[str, Any]] | None:
    """Return a one-step clarify plan if *raw_platform* is unrecognized, else None.

    Lets an expander degrade gracefully instead of emitting a workflow that
    crashes in ``cmd_load_platform_profile`` with an unhandled exception.
    """
    if _normalize_platform(raw_platform) in _valid_platforms():
        return None
    supported = ", ".join(sorted(_valid_platforms()))
    return [
        {
            "tool": "clarify",
            "args": {
                "question": (
                    f"I don't have a platform profile for {raw_platform!r}. "
                    f"Supported platforms: {supported}. Which would you like?"
                )
            },
        }
    ]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Scale parsing.
# ─────────────────────────────────────────────────────────────────────────────

_SCALE_PRESETS: dict[str, str] = dict(_VOCAB["scale_presets"])

_WxH_RE = re.compile(r"^(\d+)[x:](\d+)$")


def _parse_scale(scale: Any) -> str | None:
    """Resolve a scale shorthand or WxH/W:H literal to a 'W:H' string.

    Returns None when *scale* is None. Raises ValueError for unrecognised values.

    Takes ``Any``, not ``str``, because the model leaks non-strings into this slot (the 4B
    emits ``scale: 2`` for "4K thumbnail" requests). ``create_thumbnail.scale`` is typed in
    tools.yaml so normalize_plan coerces those before they arrive; stringifying here too
    keeps a direct ``execute_plan`` call — which skips nothing but is not obliged to
    normalize — from crashing on ``.strip()``. A bare number has no defensible reading as a
    scale, so it takes the normal unrecognised-value path rather than being coerced into a
    2-pixel thumbnail that would still satisfy a "has a scale filter" check.
    """
    if scale is None:
        return None
    text = str(scale)
    key = text.strip().lower()
    if key in _SCALE_PRESETS:
        return _SCALE_PRESETS[key]
    m = _WxH_RE.match(text.strip())
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    raise ValueError(
        f"Unrecognised scale value {scale!r}. "
        f"Use a preset (4k, 1080p, 720p, 480p) or WxH / W:H format."
    )


_ASPECT_RE = re.compile(r"^(\d+)[:/](\d+)$")


def _geometry_vf(
    width: int | None,
    height: int | None,
    fit: str | None,
    aspect: str | None,
) -> str | None:
    """Return the -vf filter chain for a geometry operation, or None.

    1. aspect (no both-dims) → pure center-crop to that aspect ratio.
    2. Both dims → fit resolves: unset/crop=cover, pad=letterbox, stretch=force.
    3. Single dim → proportional scale.
    4. Nothing → None.
    """
    if aspect and not (width and height):
        m = _ASPECT_RE.match(str(aspect).strip())
        if not m:
            raise ValueError(f"Invalid aspect value {aspect!r}. Expected 'aw:ah'.")
        aw, ah = m.group(1), m.group(2)
        # Rounded DOWN to even, because libx264 with `-pix_fmt yuv420p` refuses odd
        # dimensions outright: a 9:16 crop of a 1280x720 source computes
        # min(1280, 720*9/16) = 405, and ffmpeg answers "width not divisible by 2
        # (405x720)", writes a 0-byte file and exits non-zero. This was invisible for
        # months because the `success` verifier grades these rows on command *text*
        # (`filter:crop` present), so the broken artifact scored 1.0 on both runtimes (N4).
        return f"crop=trunc(min(iw\\,ih*{aw}/{ah})/2)*2:trunc(min(ih\\,iw*{ah}/{aw})/2)*2"

    if width and height:
        effective_fit = fit or "crop"
        if effective_fit == "stretch":
            return f"scale={width}:{height}"
        if effective_fit == "pad":
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            )
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

    if width:
        return f"scale=min({width}\\,iw):-2"
    if height:
        return f"scale=-2:{height}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Recipe + command rendering.
# ─────────────────────────────────────────────────────────────────────────────


_OUTPUT_SUFFIX_BY_MODE: dict[str, str] = dict(_VOCAB["output_suffix_by_mode"])


# Every extension the skill can read. Passed to `resolve_inputs` so a bare `*` glob means
# "all my media" rather than "every file in the sandbox" - unfiltered, it handed ffmpeg the
# .txt and .json sitting beside the clips and ffmpeg died on the first one.
_MEDIA_EXTENSIONS: list[str] = list(_VOCAB["media_extensions"])

_IMAGE_EXTENSIONS = set(_VOCAB["image_extensions"])


def _image_format_from_output(output: str | None, default: str) -> str:
    """Return image format inferred from the output filename extension, or default.

    Only infers when the caller has not explicitly set an image_format (i.e.
    default is still the hard-coded fallback "jpg").  An explicit image_format
    arg is passed as default and always wins.
    """
    if output and default == "jpg":
        ext = Path(output).suffix.lstrip(".").lower()
        if ext in _IMAGE_EXTENSIONS:
            return ext  # "jpg", "png", "webp", etc. — kept as-is
    return default


def _coerce_dimension(value: Any) -> Any:
    """Coerce a width/height value to int when the model emitted it as a string.

    Small models often send dimensions as strings ("480") or with a trailing
    resolution suffix ("480p").  A string dimension reaches ``_build_one_recipe``
    and crashes the ``probe["width"] > max_w`` comparison (int > str).  Plain
    ints pass through; unparseable values are left for downstream handling.
    """
    if isinstance(value, str):
        s = value.strip().lower()
        if s.endswith("p"):
            s = s[:-1]
        if s.isdigit():
            return int(s)
    return value


_BITRATE_RE = re.compile(r"^\s*(\d{1,4})\s*k(?:b(?:ps)?)?\s*$", re.IGNORECASE)


def _bitrate_from_quality(quality: Any) -> str | None:
    """Return an ffmpeg bitrate (e.g. '56k') when a quality value is bitrate-shaped.

    Small models sometimes pass a bitrate into the audio `quality` slot
    ('56kbps', '128k').  Profile names ('high_quality') and other values return
    None and are ignored by the caller.
    """
    if not isinstance(quality, str):
        return None
    m = _BITRATE_RE.match(quality)
    return f"{m.group(1)}k" if m else None


def _coerce_bitrate(value: Any) -> str | None:
    """Return a normalized ffmpeg bitrate ('128k') iff *value* is bitrate-shaped.

    Guards the explicit `bitrate` arg: small models leak a non-numeric word
    ('lower', 'low', 'high') into it, which renders as `-b:a lower` and crashes
    ffmpeg.  Anything not matching ``_BITRATE_RE`` returns None so the caller
    drops the bad value and lets the encoder pick its default.
    """
    if not isinstance(value, str):
        return None
    m = _BITRATE_RE.match(value)
    return f"{m.group(1)}k" if m else None


def _audio_format_from_output(output: str | None) -> str | None:
    """Return the audio format implied by an output filename extension, or None.

    Lets extract_audio infer the target codec from an explicit output name
    ('export as audio.flac' → flac) instead of defaulting to mp3 and writing an
    mp3 stream into a .flac container, which crashes ffmpeg.
    """
    if not output:
        return None
    ext = Path(output).suffix.lstrip(".").lower()
    return ext if ext in _AUDIO_EXTS else None


# Direction words the model leaks into the volume `level` slot. A bare word like
# "louder" renders as `-af volume=louder` and crashes ffmpeg; map it to a
# concrete value (≈ baseline conventions: louder ~ +6dB, quieter ~ half).
_VOLUME_LOUDER = set(_VOCAB["volume_louder"])
_VOLUME_QUIETER = set(_VOCAB["volume_quieter"])
_VOLUME_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:db)?$", re.IGNORECASE)
# Unicode minus / en-dash / em-dash → ASCII hyphen (models emit '−6dB' with U+2212).
_DASH_TRANSLATION = {0x2212: "-", 0x2013: "-", 0x2014: "-"}


def _coerce_volume_level(value: Any) -> str:
    """Return a valid ffmpeg ``volume=`` value, coercing model-leaked NL words.

    Numeric/dB levels pass through (after normalizing a Unicode minus); known
    direction words map to a concrete gain/attenuation; anything unrecognized
    falls back to a no-op (1.0) rather than crashing the binary.
    """
    if value is None:
        return "1.0"
    s = str(value).translate(_DASH_TRANSLATION).strip()
    compact = s.replace(" ", "")
    if _VOLUME_NUMERIC_RE.match(compact):
        return compact
    key = s.lower()
    if key in _VOLUME_LOUDER:
        return "6dB"
    if key in _VOLUME_QUIETER:
        return "0.5"
    return "1.0"


def _quality_from_crf(crf: Any, fallback: Any) -> Any:
    """Resolve a quality value from a `crf` arg, tolerating non-numeric input.

    Numeric crf → "crf N" (handled downstream by _CRF_RE).  Small models also
    drop a quality WORD ("balanced") or a "crf 28" string into the crf slot;
    pass those through verbatim (load_quality_profile resolves both) instead of
    crashing on int().
    """
    if crf is None:
        return fallback
    try:
        return f"crf {int(crf)}"
    except (ValueError, TypeError):
        s = str(crf).strip()
        return s or fallback


_VIDEO_CONTAINERS = set(_VOCAB["video_containers"])


def _container_from_output(output: str | None) -> str | None:
    """Return the container implied by an output filename's extension, or None.

    Used by convert_video to keep the encoded container consistent with an
    explicit output name (e.g. output='clip.webm' → webm) when the caller did
    not pass an explicit ``container``.
    """
    if not output:
        return None
    ext = Path(output).suffix.lstrip(".").lower()
    return ext if ext in _VIDEO_CONTAINERS else None


# Symbolic instants a user can name but a model cannot compute: the duration is only known
# after probing, so this layer is the only one that can resolve them. Left unresolved they
# reached ffmpeg as `-ss last_frame` ("Invalid duration"); the alternative the model has is
# to guess a number, and on the control arm it guessed 00:00:00 and returned the FIRST frame
# for a request that said the last.
_AT_TIME_END_TOKENS = frozenset({"last_frame", "last", "end", "final_frame", "final"})
_AT_TIME_START_TOKENS = frozenset({"first", "first_frame", "start", "beginning"})
_AT_TIME_MIDDLE_TOKENS = frozenset({"middle", "midpoint", "halfway", "mid", "centre", "center"})

# Step back from the very end: seeking exactly to the duration lands past the last frame and
# writes nothing.
_LAST_FRAME_EPSILON = 0.1


def _resolve_at_time(value: Any, *, duration: float | None) -> Any:
    """Resolve a symbolic instant against the clip duration.

    Returns the value unchanged when it is already a time, and ``None`` when the token is not
    one this skill knows - callers must refuse it rather than invent an instant for it.
    """
    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip().lower()
    if _timestamp_seconds(value) is not None:
        return value
    if text in _AT_TIME_START_TOKENS:
        return "0"
    if text in _AT_TIME_MIDDLE_TOKENS:
        if duration is None or duration <= 0:
            return None
        return _format_seconds(duration / 2.0)
    if text in _AT_TIME_END_TOKENS:
        if duration is None or duration <= 0:
            return None
        return _format_seconds(max(0.0, duration - _LAST_FRAME_EPSILON))
    return None


def _format_seconds(seconds: float) -> str:
    """Render seconds for an ffmpeg flag, identically on both runtimes.

    `:g` was wrong twice over: it rounds to six significant digits where the Rust port does
    not (`6.172839` became `6.17284`), and it switches to scientific notation for small
    values (`-1e-06`), which ffmpeg cannot parse as a time at all. Fixed decimal, trailing
    zeros trimmed, matches `format_seconds` in `engine.rs` character for character.
    """
    value = float(seconds)
    if value.is_integer():
        return str(int(value))
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _timestamp_seconds(value: Any) -> float | None:
    """Seconds for a timestamp written as ``HH:MM:SS[.ms]``, ``MM:SS``, or a bare number.

    Comparing the strings would not do: ``"0"`` and ``"00:00:00"`` are the same instant and
    the model writes both.

    Two spellings ffmpeg itself accepts have to be read here too, because the callers that
    compare instants (``_normalize_trim``'s reversed-range guard) are blind to anything this
    returns ``None`` for:

    * a **unit suffix** — ``5s``, ``-2s``, ``500ms``. ``-2s`` is how "the last 2 seconds"
      arrives, and it used to fall through to ffmpeg as ``-ss -2s``.
    * a **leading sign on a clock string**. Parsing componentwise loses it, because
      ``float("-00") * 60`` is ``-0.0``: ``-00:00:02`` read as **+2.0**, so the guard fired
      on a number of the wrong sign rather than not at all.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:].strip()
    if not text:
        return None
    for unit, scale in (("ms", 0.001), ("s", 1.0)):
        if text.endswith(unit) and ":" not in text:
            body = text[: -len(unit)].strip()
            try:
                seconds = float(body) * scale
            except ValueError:
                return None
            return -seconds if negative else seconds
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if any(p < 0 for p in parts):
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return -total if negative else total


def _normalize_trim(*, start: Any, duration: Any, end: Any, frames: Any) -> dict[str, Any]:
    """Resolve a trim request into exactly one of: a frame count, a duration, or an end.

    **An empty range becomes one frame.** ``ffmpeg_161`` asks for a one-frame video and the
    model emits ``-ss 00:00:00 -to 00:00:00``; ffmpeg then exits 0 having written a file with
    nothing in it, which the corpus scored as a pass because the container was right. A user
    who names a single instant wants the frame at that instant - there is no other reading of
    it, and no reading at all under which producing an empty file is the answer.

    Resolving it here rather than teaching the model the new `frames` argument is deliberate:
    a product fix that only works after a fine-tune is not a product fix.
    """
    if frames is not None:
        # An explicit count wins outright; preflight has already refused it alongside a range.
        return {"start": start, "duration": None, "end": None, "frames": frames}

    start_s = _timestamp_seconds(start)
    end_s = _timestamp_seconds(end)
    duration_s = _timestamp_seconds(duration)

    # An absent `start` means zero, so "-to 00:00:00" with no start is the same empty range
    # and produced the same empty file. A *reversed* range (end < start) lands here too: the
    # engine cannot clarify, so its only choices are one frame or a file with nothing in it.
    # A NEGATIVE start is ffmpeg's from-end offset, not a reversed range: "trim to the last
    # 2 seconds" arrives as start=-2s (end=0s or absent) and means "start two seconds before
    # the end, run to the end". It renders as `-sseof -2` with no `-to`. Reading it as a
    # reversed range would collapse a 2-second request to a single frame - which is exactly
    # what happened while the sign was being lost in parsing.
    if start_s is not None and start_s < 0:
        # A supplied bound that could not be read is NOT the same as an absent one. Treating
        # `end="banana"` as "to the end of the clip" silently answers a different request,
        # and `duration="0"` is an empty range the guard below still owns.
        for label, raw, seconds in (("end", end, end_s), ("duration", duration, duration_s)):
            if raw is not None and seconds is None:
                raise ValueError(
                    f"Unrecognised {label} {raw!r}. Use a timestamp (00:00:05), a number of "
                    "seconds, or a value with a unit (5s, 500ms)."
                )
        if duration_s is not None and duration_s <= 0:
            return {
                "start": start,
                "start_from_end": None,
                "duration": None,
                "end": None,
                "frames": 1,
            }
        # `end` is a second offset from the end when negative (-10 -> -5 is a 5s window);
        # a positive `end` alongside a from-end start is contradictory, so leave both alone
        # and let the ordinary path render what was asked.
        if end_s is None or end_s <= 0:
            length = None
            if duration_s is not None and duration_s > 0:
                length = duration
            elif end_s is not None and end_s < 0:
                span = end_s - start_s
                length = _format_seconds(span) if span > 0 else None
            return {
                "start": None,
                "start_from_end": start_s,
                "duration": length,
                "end": None,
                "frames": None,
            }

    effective_start = 0.0 if start_s is None else start_s
    empty_range = (end is not None and end_s is not None and end_s <= effective_start) or (
        duration is not None and duration_s is not None and duration_s <= 0
    )
    if empty_range:
        return {"start": start, "duration": None, "end": None, "frames": 1}

    return {"start": start, "duration": duration, "end": end, "frames": None}


def _output_extension(mode: str, options: dict[str, Any]) -> str:
    if mode == "extract_audio":
        return options.get("audio_format", "mp3")
    if mode == "thumbnail":
        return options.get("image_format", "jpg")
    return options.get("container", "mp4")


def _destination_name(input_path: Path, ext: str) -> str:
    """The file name an input takes inside a destination directory."""
    return f"{input_path.stem}.{ext}"


def _batch_suffixes(input_path: Path, out_stem: str) -> list[str]:
    """The parts of an input's name that can tell two colliding outputs apart, best first.

    Two batch shapes collide, and they are distinguished by different things:

    * **One literal filename for many inputs** — six fixture videos extracted to ``audio.mp3``.
      The source *stems* differ and the extensions mostly do not, so the stem is the answer.
    * **A destination directory** — ``clip.mp4`` and ``clip.mov`` into ``converted/`` both take
      the output stem ``clip``, and only the extension is left.

    Using the extension for both is what produced ``audio_mp4_5.mp3``: five of six inputs were
    `.mp4`, so the suffix distinguished nothing and a counter did all the work.
    """
    stem, ext = input_path.stem, input_path.suffix.lstrip(".").lower()
    suffixes: list[str] = []
    if stem and stem != out_stem:
        suffixes.append(stem)
    if ext:
        suffixes.append(ext)
    # Both, for the case where each alone is ambiguous: `a/clip.mov` and `b/clip.mov` into
    # `audio.mp3` share a stem AND an extension with each other but not with the output.
    if stem and ext and stem != out_stem:
        suffixes.append(f"{stem}_{ext}")
    return suffixes


def disambiguate_outputs(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every recipe in a batch its own output path.

    A destination directory collapses the source extension, so `clip.mp4` and `clip.mov` both
    render `converted/clip.mp4` — and every command carries `-y`, so the second conversion
    silently destroyed the first. Whatever part of the source name actually differs is what
    gets restored (see `_batch_suffixes`); a counter is the fallback for a genuine repeat, and
    only a genuine repeat, because a counter tells the user nothing.

    Doing it here rather than per file is deliberate: only the batch knows whether there is a
    clash at all, and a lone `clip.mp4 -> converted/` should stay `clip.mp4`, not become
    `clip_mp4.mkv` because some other input might have existed.
    """
    seen: set[str] = set()
    for recipe in recipes:
        out = recipe.get("output")
        if not out:
            continue
        if out not in seen:
            seen.add(out)
            continue
        path = Path(out)
        suffixes = _batch_suffixes(Path(recipe.get("input", "")), path.stem)
        tried = [path.with_name(f"{path.stem}_{s}{path.suffix}") for s in suffixes]
        candidate = next((c for c in tried if str(c) not in seen), None)
        if candidate is None:
            # Nothing in the source name is left to say. Count off the most specific candidate
            # so the walk still terminates.
            base = tried[-1] if tried else path
            candidate, n = base, 1
            while str(candidate) in seen:
                n += 1
                candidate = base.with_name(f"{base.stem}_{n}{base.suffix}")
        recipe["output"] = str(candidate)
        seen.add(str(candidate))
    return recipes


#: Characters a filename may not contain on Windows. ``*`` and ``?`` are deliberately absent:
#: they are this skill's own output grammar (``videos/*.mp4``), expanded in
#: :func:`_resolve_output_target`, and stripping them would break a documented feature to fix
#: an unrelated bug. ``/`` and ``\`` are absent because they are structure, not characters.
_ILLEGAL_IN_FILENAME = '<>:"|'
_ILLEGAL_REPLACEMENT = "-"

#: A leading drive letter is the one legitimate colon in a path (``C:/out/clip.mp4``), and CLI
#: mode has no sandbox to confine writes to, so absolute outputs are real there.
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:(?=[\\/])")


def _legal_output_path(raw: str) -> str:
    """Make a model-supplied ``output`` a string the filesystem will actually accept.

    The model writes filenames out of the utterance, and `ffmpeg_268` named one after the time
    range it was given: ``clip_trimmed_00:00:00.mp4``. Colons are legal on Linux and illegal on
    Windows, so ffmpeg refused to open it — *Error opening output files: Invalid argument* — and
    the chain died at step 1 with nothing written. Both runtimes produce it, so this is the skill
    trusting model output as a filename rather than a native port defect.

    **Unconditional, not per-platform.** Sanitising only on Windows would make the same plan
    render different names on different machines, which breaks L3 parity across runners and makes
    an eval result depend on where it ran. A colon in a filename is a bad idea everywhere.
    """
    drive = _DRIVE_PREFIX.match(raw)
    head, tail = (raw[: drive.end()], raw[drive.end() :]) if drive else ("", raw)
    return head + "".join(_ILLEGAL_REPLACEMENT if c in _ILLEGAL_IN_FILENAME else c for c in tail)


def _resolve_output_target(
    raw_output: str, *, input_path: Path, mode: str, options: dict[str, Any]
) -> Path:
    """Read an `output` that names a DESTINATION rather than one file.

    A batch writes one file per input, so these two spellings are per-file requests and were
    being passed to ffmpeg verbatim:

    * ``videos/*.mp4`` - "same name, over there". The ``*`` reached ffmpeg as a literal
      character in the filename.
    * ``videos_hevc`` - a destination directory. ffmpeg cannot choose a muxer for an
      extensionless path without ``-f`` and failed with "Invalid argument".

    A genuine filename (``renamed.mp4``) is returned untouched, so the single-file case is
    unchanged.
    """
    # Before anything reads it as a path: the model supplied this string, and it is not
    # guaranteed to be a legal filename. See `_legal_output_path`.
    requested, raw_output = raw_output, _legal_output_path(raw_output)
    out = Path(raw_output)
    ext = _output_extension(mode, options)

    # A wildcard anywhere but the final component cannot be expanded - `out*/clip.mp4` names
    # no directory this skill can pick, and creating a literal `out*` is not the answer.
    if any("*" in part for part in out.parts[:-1]):
        raise ValueError(
            f"Unrecognised output {requested!r}. A '*' may only stand for the file name, "
            "as in 'videos/*.mp4'."
        )

    if "*" in out.name:
        # The `*` stands for the input's stem, and the text around it is kept:
        # `prefix_*.mp4` -> `prefix_clip.mp4`. Replacing the whole name dropped the prefix.
        stem_ext = out.suffix.lstrip(".")
        if "*" in stem_ext:  # `*.*` - the extension is not a pattern this skill can read
            raise ValueError(
                f"Unrecognised output {requested!r}. A '*' may only stand for the file name."
            )
        pattern = out.name[: -(len(stem_ext) + 1)] if stem_ext else out.name
        name = pattern.replace("*", input_path.stem)
        return out.with_name(f"{name}.{stem_ext or ext}")

    # `.mp4` is a dotfile, i.e. a concrete file name that pathlib reports as suffix-less.
    # Only a name with no dot at all is a destination directory.
    if not out.suffix and not out.name.startswith("."):
        return out / _destination_name(input_path, ext)

    return out


def _derive_output_path(input_path: Path, mode: str, options: dict[str, Any]) -> Path:
    suffix_template = _OUTPUT_SUFFIX_BY_MODE.get(mode, "_out")
    platform = options.get("platform") or ""
    suffix = suffix_template.format(platform=platform)
    if mode == "extract_audio":
        fmt = options.get("audio_format", "mp3")
        return input_path.with_name(f"{input_path.stem}{suffix}.{fmt}")
    if mode == "thumbnail":
        fmt = options.get("image_format", "jpg")
        return input_path.with_name(f"{input_path.stem}{suffix}.{fmt}")
    container = options.get("container", "mp4")
    return input_path.with_name(f"{input_path.stem}{suffix}.{container}")


def _build_one_recipe(
    probe: dict[str, Any],
    platform_profile: dict[str, Any] | None,
    quality_profile: dict[str, Any] | None,
    options: dict[str, Any],
    sandbox: Path | None = None,
) -> dict[str, Any]:
    mode = options.get("mode", "platform")
    input_path = Path(probe["file"])

    container = options.get("container") or (platform_profile or {}).get("container", "mp4")
    if mode == "reverse" and not options.get("container"):
        container = probe.get("container") or input_path.suffix.lstrip(".") or container
    video_encoder = options.get("video_encoder") or (platform_profile or {}).get(
        "video_encoder", "libx264"
    )
    pixel_format = (platform_profile or {}).get("pixel_format", "yuv420p")
    _audio_default = {"webm": "libopus", "ogg": "libvorbis"}.get(container, "aac")
    audio_codec = options.get("audio_codec") or (platform_profile or {}).get(
        "audio_codec", _audio_default
    )
    max_w = options.get("width") or (platform_profile or {}).get("max_width")
    max_h = options.get("height") or (platform_profile or {}).get("max_height")
    faststart = (platform_profile or {}).get("faststart", container == "mp4")
    crf = (quality_profile or {}).get("video_crf")
    preset = (quality_profile or {}).get("encoder_preset")
    audio_bitrate = options.get("audio_bitrate") or (quality_profile or {}).get(
        "audio_bitrate", (platform_profile or {}).get("max_audio_bitrate", "128k")
    )

    # Audio-only inputs (no video stream): an audio operation must produce an
    # audio file in the input's format, not a video container with a re-encoded
    # aac track.
    #
    # `adjust_speed` joined `adjust_volume` here after `ffmpeg_226` ("pull mp3 from clip.mp4
    # and apply 0.8x tempo") rendered `-vf setpts ... -c:v libx264 ... -c:a aac clip_speed.mp4`
    # against a file with no video stream: a filter and an encoder for a stream that is not
    # there, and the user's mp3 handed back as an mp4. **ffmpeg exited 0**, so only the
    # artifact-level `audio_codec` criterion caught it — the failure mode this whole class of
    # bug has, and the reason the rule belongs to the operation rather than to one tool.
    _AUDIO_APPLICABLE_MODES = ("adjust_volume", "adjust_speed")
    audio_only = (
        mode in _AUDIO_APPLICABLE_MODES and not probe.get("video_codec") and not probe.get("width")
    )
    if audio_only:
        container = (
            options.get("container")
            or probe.get("container")
            or input_path.suffix.lstrip(".")
            or container
        )
        audio_codec = _audio_encoder_for(container)
        # A lossless codec ignores a bitrate target and should not carry one (same rule as
        # the container-mandated path below).
        if audio_codec in ("flac", "pcm_s16le", "alac"):
            audio_bitrate = None

    if container == "gif":
        video_encoder = ""
        pixel_format = ""
        audio_codec = ""
        faststart = False

    output_options = dict(options)
    output_options["container"] = container
    raw_output = options.get("output_path")
    if raw_output:
        out = _resolve_output_target(
            raw_output, input_path=input_path, mode=mode, options=output_options
        )
        if not out.is_absolute():
            out = input_path.parent / out
        output_path = out
    else:
        output_path = _derive_output_path(input_path, mode, output_options)

    # Explicit/user-supplied output paths must stay inside the sandbox when one
    # is set. No-op in open/CLI mode (sandbox=None), where the user writes
    # wherever they ask.
    _assert_in_sandbox(output_path, sandbox)

    operations: list[str] = []
    if mode in ("platform", "compress", "convert", "resize", "trim", "batch"):
        if max_w and probe.get("width") and probe["width"] > max_w:
            operations.append(f"downscale_to_fit_{max_w}x{max_h}")
        elif max_h and probe.get("height") and probe["height"] > max_h:
            operations.append(f"downscale_to_fit_{max_w}x{max_h}")
        if container != probe.get("container"):
            operations.append(f"convert_container_to_{container}")
        if probe.get("video_codec") != _codec_from_encoder(video_encoder):
            operations.append(f"convert_video_to_{_codec_from_encoder(video_encoder)}")
        if probe.get("has_audio") and probe.get("audio_codec") != audio_codec:
            operations.append(f"ensure_{audio_codec}_audio")
        operations.append(f"ensure_{pixel_format}")
        if faststart:
            operations.append("enable_faststart")

    remux = bool(options.get("remux"))
    copy_audio = remux or bool(options.get("copy_audio"))

    # A container that accepts only a restricted set of VIDEO codecs cannot stream-copy an
    # incompatible source. `-c copy` into webm from an h264 source makes ffmpeg exit 1 and
    # leave a truncated file behind — a failure the user sees as "knaif produced a broken
    # file", not as an unsupported request.
    #
    # Unlike the audio guard below, this one MUST override a remux rather than skip it: the
    # remux is precisely how the bad command gets built. "A remux copies everything verbatim
    # by design" holds for mkv/mov, which accept h264; it does not hold for webm.
    #
    # Dropping the remux is not enough on its own — the encoder would fall back to `copy` and
    # rebuild the same command — so the container's default encoder has to be named here.
    #
    # ogg carries the same restriction (theora/vp8 only) and is deliberately absent: vocab.yaml
    # has no theora entry to fall back to and the corpus has no ogg rows, so listing it would be
    # an untested guess. Closing that one means adding the encoder first.
    _COMPATIBLE_VIDEO: dict[str, set[str]] = {"webm": {"vp8", "vp9", "av1"}}
    _CONTAINER_VIDEO_ENCODER: dict[str, str] = {"webm": "libvpx-vp9"}
    if remux:
        src_video = probe.get("video_codec")
        compatible = _COMPATIBLE_VIDEO.get(container)
        if src_video and compatible and src_video not in compatible:
            remux = False
            copy_audio = False
            video_encoder = options.get("video_encoder") or _CONTAINER_VIDEO_ENCODER[container]

    # When stream-copy was requested but the source audio codec is incompatible
    # with the target container, fall back to the container's default encoder.
    # webm only accepts opus/vorbis; ogg only accepts vorbis/opus/flac.
    # A remux is never overridden here (it copies everything verbatim by design).
    _COMPATIBLE_AUDIO: dict[str, set[str]] = {
        "webm": {"opus", "vorbis"},
        "ogg": {"vorbis", "opus", "flac"},
    }
    if copy_audio and not remux:
        src_audio = probe.get("audio_codec")
        compatible = _COMPATIBLE_AUDIO.get(container)
        if src_audio and compatible and src_audio not in compatible:
            copy_audio = False
            # audio_codec already holds the container default (libopus/libvorbis)

    # A single-codec audio container (mp3/aac/flac/wav/m4a/opus) can hold only its
    # one codec, so stream-copying a mismatched source stream into it fails silently
    # and produces no file. When the input is audio-only and the target is such a
    # container, re-encode to the container's codec instead of remuxing. Multi-codec
    # containers (mp4/mkv/mov) still remux verbatim by design — see ffmpeg_002/044.
    _SINGLE_CODEC_AUDIO_CONTAINER: dict[str, str] = {
        "mp3": "mp3",
        "aac": "aac",
        "flac": "flac",
        "wav": "pcm_s16le",
        "m4a": "aac",
        "opus": "opus",
    }
    input_is_audio_only = not probe.get("video_codec") and not probe.get("width")
    mandated_codec = _SINGLE_CODEC_AUDIO_CONTAINER.get(container)
    drop_video = False
    if (
        copy_audio
        and input_is_audio_only
        and mandated_codec is not None
        and probe.get("audio_codec") != mandated_codec
    ):
        remux = False
        copy_audio = False
        audio_codec = _audio_encoder_for(container)
        drop_video = True
        # Lossless codecs ignore (and shouldn't carry) a bitrate target.
        if audio_codec in ("flac", "pcm_s16le", "alac"):
            audio_bitrate = None

    recipe: dict[str, Any] = {
        "mode": mode,
        "input": str(input_path),
        "output": str(output_path),
        "operations": operations,
        "video": {
            "encoder": "copy" if remux else video_encoder,
            "crf": None if remux else crf,
            "preset": None if remux else preset,
            "max_width": max_w,
            "max_height": max_h,
            "pixel_format": None if remux else pixel_format,
        },
        "audio": {
            "codec": "copy" if copy_audio else audio_codec,
            "bitrate": None if copy_audio else audio_bitrate,
        },
        "container": container,
        "faststart": faststart,
        "remux": remux,
    }

    if mode == "resize":
        recipe["fit"] = options.get("fit")
        recipe["aspect"] = options.get("aspect")
    if mode == "rotate":
        recipe["angle"] = options.get("angle")
        recipe["flip"] = options.get("flip")
    if mode == "adjust_volume":
        recipe["level"] = options.get("level")
        recipe["normalize"] = bool(options.get("normalize", False))
        recipe["audio_only"] = audio_only
    if mode == "adjust_speed":
        recipe["audio_only"] = audio_only
    if mode == "trim":
        recipe["trim"] = _normalize_trim(
            start=options.get("start"),
            duration=options.get("duration"),
            end=options.get("end"),
            frames=options.get("frames"),
        )
    if mode == "extract_audio":
        recipe["audio_format"] = options.get("audio_format", "mp3")
        if options.get("start") is not None or options.get("end") is not None:
            recipe["trim"] = {
                "start": options.get("start"),
                "duration": None,
                "end": options.get("end"),
            }
        recipe.pop("video", None)
    if mode == "thumbnail":
        # `probe` carries the duration, which is what makes "the last frame" answerable here
        # and nowhere upstream. An unknown token resolves to None; falling back to the default
        # instant would answer a different question than the one asked, so it raises instead.
        requested_at = options.get("at_time", "00:00:01")
        resolved_at = _resolve_at_time(requested_at, duration=probe.get("duration"))
        if resolved_at is None:
            raise ValueError(
                f"Unrecognised time {requested_at!r}. Use a timestamp (00:00:05), "
                "a number of seconds, or 'first' / 'last'."
            )
        recipe["at_time"] = resolved_at
        recipe["image_format"] = options.get("image_format", "jpg")
        recipe["scale"] = _parse_scale(options.get("scale"))
        recipe.pop("audio", None)
    if mode == "compress" and options.get("target_size_mb") is not None:
        recipe["target_size_mb"] = options["target_size_mb"]
        # The duration is what turns a size into a bitrate, and `_build_flags` sees only the
        # recipe. Carried here rather than re-probed there, so the cap is computed from the
        # same probe every other decision in this recipe was made from.
        recipe["source_duration"] = probe.get("duration")
    if mode == "reverse":
        recipe["include_audio"] = options.get("include_audio", True)
        recipe["has_audio"] = probe.get("has_audio", False)
    if mode == "strip_audio":
        recipe.pop("audio", None)
    if mode == "adjust_speed":
        recipe["speed"] = float(options.get("speed", 1.0))

    # Audio-only re-encode into a single-codec container: no video stream to map,
    # so the render path must not emit -c:v / -pix_fmt / scale.
    if drop_video:
        recipe.pop("video", None)

    recipe["pre_input_flags"], recipe["post_input_flags"] = _build_flags(recipe)
    return recipe


# Video codec token → ffmpeg encoder (see vocab.yaml). Single source of truth for
# the convert expander's codec mapping and for detecting a codec token mis-slotted
# as a container (av1/hevc/etc. are codecs, not containers).
_VIDEO_ENCODER_MAP: dict[str, str] = dict(_VOCAB["video_encoder_map"])
_VIDEO_CODEC_ALIASES: frozenset[str] = frozenset(_VIDEO_ENCODER_MAP)
_ENCODER_CODEC_MAP: dict[str, str] = dict(_VOCAB["encoder_codec_map"])


def _codec_from_encoder(encoder: str) -> str:
    return _ENCODER_CODEC_MAP.get(encoder, encoder)


#: Headroom left for container overhead — muxing, the moov atom, per-packet headers — and for
#: x264's rate-control window. A cap that budgeted 100% of the target for the streams would be
#: exceeded by exactly that overhead, which is the one outcome a ceiling may not have.
#:
#: **Both constants are measured, not guessed.** Swept over three fixtures x four targets on
#: 2026-09-16: at `bufsize = 2 x maxrate` a 1 MiB cap produced 1027 KB — over, by 3 KB — because
#: a two-second rate-control window lets the encoder overshoot the average. At `bufsize = maxrate`
#: all twelve combinations landed under, worst case 93.5% of the cap. 0.95 rather than 0.97 keeps
#: margin for sources not in that sweep: undershooting costs some quality, overshooting makes the
#: ceiling a lie.
_SIZE_CAP_HEADROOM = 0.95

#: `bufsize` as a multiple of `maxrate`. 1x is a one-second rate-control window; see above for
#: why the conventional 2x is not safe for a hard ceiling.
_SIZE_CAP_BUFSIZE_MULTIPLE = 1


def _parse_bitrate_bps(value: Any) -> int:
    """``"96k"`` -> 96000. Anything unreadable is 0, i.e. budget nothing for it."""
    if value is None:
        return 0
    text = str(value).strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def _size_cap_kbit(target_size_mb: float, duration_s: Any, audio_bitrate: Any) -> int | None:
    """Video bitrate ceiling, in whole kbit/s, that keeps the output under *target_size_mb*.

    Returns ``None`` when the duration is unknown, because a size only becomes a bitrate once
    there is a length to divide by. Inventing one would produce a cap that means nothing, and
    refusing would fail a request that is otherwise valid — so the caller falls back to plain
    CRF, exactly what it rendered before.

    Floors to whole kbit rather than rounding: this is a ceiling, so every approximation in it
    has to point the same way.
    """
    try:
        duration = float(duration_s)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    total_bps = (float(target_size_mb) * 1024 * 1024 * 8) / duration
    video_bps = total_bps * _SIZE_CAP_HEADROOM - _parse_bitrate_bps(audio_bitrate)
    kbit = int(video_bps // 1000)
    if kbit <= 0:
        raise ValueError(
            f"target_size_mb={target_size_mb:g} is too small for this file: "
            f"{duration:g}s of audio at {audio_bitrate} already exceeds it. "
            "Ask for a larger size, or strip the audio."
        )
    return kbit


def _build_flags(recipe: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (pre_input_flags, post_input_flags) for the given recipe.

    Separated from _build_one_recipe so all mode knowledge lives in one place.
    _render_command becomes a thin assembler that only handles preview overrides.
    """
    mode = recipe.get("mode")
    pre: list[str] = []
    post: list[str] = []

    # Trim: fast-seek before -i; duration/end after -i (falls through to encode below).
    if mode == "trim":
        trim = recipe.get("trim", {})
        if trim.get("start_from_end") is not None:
            # -sseof takes a negative offset from the end of the input.
            pre += ["-sseof", _format_seconds(trim["start_from_end"])]
        elif trim.get("start") is not None:
            pre += ["-ss", str(trim["start"])]
        if trim.get("frames") is not None:
            # A frame count replaces the range rather than joining it: with both, ffmpeg
            # stops at whichever arrives first, so the command would mean neither request.
            # `-vframes`, not `-frames:v`: the thumbnail arm below already uses that
            # spelling and so does the native port. Two spellings of one flag in one
            # renderer is how the two runtimes drift apart on a byte comparison.
            post += ["-vframes", str(trim["frames"])]
        elif trim.get("duration") is not None:
            # `-t` is a LENGTH, already relative to the seek point. Correct after `-i`.
            post += ["-t", str(trim["duration"])]
        elif trim.get("end") is not None:
            # `-to` goes BEFORE `-i`, with `-ss`. As an OUTPUT option it is relative to the
            # seek point, so `-ss 2 -i in.mp4 -to 5` is five seconds starting at two — not the
            # range 2->5. Measured on a real 10s file: 5.000s the old way, 3.000s this way.
            # Every range trim this engine rendered was wrong, and nothing saw it until
            # `duration_s` was added to the corpus.
            pre += ["-to", str(trim["end"])]

    if mode == "reverse":
        post += ["-vf", "reverse"]
        video = recipe.get("video", {})
        if video.get("encoder"):
            post += ["-c:v", video["encoder"]]
        if video.get("crf") is not None:
            post += ["-crf", str(video["crf"])]
        if video.get("preset"):
            post += ["-preset", video["preset"]]
        if video.get("pixel_format"):
            post += ["-pix_fmt", video["pixel_format"]]
        if recipe.get("include_audio") and recipe.get("has_audio"):
            post += ["-af", "areverse"]
            audio = recipe.get("audio", {})
            if audio.get("codec"):
                post += ["-c:a", audio["codec"]]
            if audio.get("bitrate"):
                post += ["-b:a", str(audio["bitrate"])]
        else:
            post += ["-an"]
    elif mode == "extract_audio":
        # Optional trim-while-extract. Both bounds are INPUT options: see the `trim` arm —
        # a `-to` after `-i` is relative to the seek point, which made `ffmpeg_119`
        # ("just the audio from 3 to 5 seconds") render five seconds of audio.
        trim = recipe.get("trim", {})
        if trim.get("start") is not None:
            pre += ["-ss", str(trim["start"])]
        if trim.get("end") is not None:
            pre += ["-to", str(trim["end"])]
        post += ["-vn", "-c:a", _audio_encoder_for(recipe.get("audio_format", "mp3"))]
        bitrate = (recipe.get("audio") or {}).get("bitrate")
        if bitrate:
            post += ["-b:a", str(bitrate)]
    elif mode == "thumbnail":
        post += ["-ss", str(recipe.get("at_time", "00:00:01"))]
        scale = recipe.get("scale")
        if scale:
            post += ["-vf", f"scale={scale}"]
        post += ["-vframes", "1"]
    elif mode == "strip_audio":
        post += ["-an", "-c:v", "copy"]
    elif mode == "adjust_speed":
        speed = float(recipe.get("speed", 1.0))
        pts_factor = round(1.0 / speed, 6)
        # The tempo filter is the request and always applies; `setpts` retimes a video stream
        # an audio-only input does not have, and neither does the video encoder.
        if recipe.get("audio_only"):
            post += ["-af", f"atempo={speed}"]
        else:
            post += ["-vf", f"setpts={pts_factor}*PTS", "-af", f"atempo={speed}"]
            video = recipe.get("video", {})
            if video.get("encoder"):
                post += ["-c:v", video["encoder"]]
            if video.get("crf") is not None:
                post += ["-crf", str(video["crf"])]
            if video.get("preset"):
                post += ["-preset", video["preset"]]
        audio = recipe.get("audio", {})
        if audio.get("codec"):
            post += ["-c:a", audio["codec"]]
        if audio.get("bitrate"):
            post += ["-b:a", str(audio["bitrate"])]
    elif mode == "rotate":
        filters: list[str] = []
        angle = recipe.get("angle")
        flip = recipe.get("flip")
        if angle == 90:
            filters.append("transpose=1")
        elif angle == 180:
            filters.append("hflip,vflip")
        elif angle == 270:
            filters.append("transpose=2")
        if flip == "horizontal":
            filters.append("hflip")
        elif flip == "vertical":
            filters.append("vflip")
        if not filters:
            raise ValueError("rotate_video: at least one of angle or flip must be set")
        post += ["-vf", ",".join(filters)]
        video = recipe.get("video", {})
        if video.get("encoder"):
            post += ["-c:v", video["encoder"]]
        if video.get("crf") is not None:
            post += ["-crf", str(video["crf"])]
        if video.get("preset"):
            post += ["-preset", video["preset"]]
        audio = recipe.get("audio", {})
        if audio.get("codec"):
            post += ["-c:a", audio["codec"]]
    elif mode == "adjust_volume":
        if recipe.get("normalize"):
            post += ["-af", "loudnorm"]
        else:
            level = _coerce_volume_level(recipe.get("level"))
            post += ["-af", f"volume={level}"]
        if not recipe.get("audio_only"):
            post += ["-c:v", "copy"]  # video input: keep the video stream untouched
        audio = recipe.get("audio", {})
        if audio.get("codec"):
            post += ["-c:a", audio["codec"]]
    elif recipe.get("container") == "gif":
        video = recipe.get("video", {})
        scale_h = video.get("max_height") or 480
        post += ["-vf", f"fps=10,scale=-1:{scale_h}:flags=lanczos", "-an"]
    elif recipe.get("remux"):
        post += ["-c", "copy"]
        if recipe.get("faststart"):
            post += ["-movflags", "+faststart"]
    elif mode == "resize":
        video = recipe.get("video", {})
        vf = _geometry_vf(
            video.get("max_width"),
            video.get("max_height"),
            recipe.get("fit"),
            recipe.get("aspect"),
        )
        if vf:
            post += ["-vf", vf]
        if video.get("encoder"):
            post += ["-c:v", video["encoder"]]
        if video.get("crf") is not None:
            post += ["-crf", str(video["crf"])]
        if video.get("preset"):
            post += ["-preset", video["preset"]]
        if video.get("pixel_format"):
            post += ["-pix_fmt", video["pixel_format"]]
        audio = recipe.get("audio", {})
        if audio.get("codec"):
            post += ["-c:a", audio["codec"]]
        if audio.get("bitrate"):
            post += ["-b:a", str(audio["bitrate"])]
        if recipe.get("faststart"):
            post += ["-movflags", "+faststart"]
    else:
        # Handles: platform, compress, convert, trim (encode part), batch.
        video = recipe.get("video", {})
        max_w = video.get("max_width")
        max_h = video.get("max_height")
        if max_w and max_h:
            post += ["-vf", f"scale={max_w}:{max_h}"]
        elif max_w:
            post += ["-vf", f"scale='min({max_w},iw)':-2"]
        elif max_h:
            post += ["-vf", f"scale=-2:{max_h}"]
        if video.get("encoder"):
            post += ["-c:v", video["encoder"]]
        if video.get("crf") is not None:
            post += ["-crf", str(video["crf"])]
        if video.get("preset"):
            post += ["-preset", video["preset"]]
        # Capped CRF: quality still drives the encode, the cap only stops it exceeding the
        # size that was asked for. A fixed `-b:v` derived from the target would INFLATE an
        # already-small clip — `email.yaml` declares `default_target_size_mb: 20`, and a clip
        # that compresses to 200 KB must not become a 20 MB file because a ceiling was named.
        if recipe.get("target_size_mb") is not None:
            kbit = _size_cap_kbit(
                recipe["target_size_mb"],
                recipe.get("source_duration"),
                (recipe.get("audio") or {}).get("bitrate"),
            )
            if kbit is not None:
                post += [
                    "-maxrate",
                    f"{kbit}k",
                    "-bufsize",
                    f"{kbit * _SIZE_CAP_BUFSIZE_MULTIPLE}k",
                ]
        if video.get("pixel_format"):
            post += ["-pix_fmt", video["pixel_format"]]
        audio = recipe.get("audio", {})
        if audio.get("codec"):
            post += ["-c:a", audio["codec"]]
        if audio.get("bitrate"):
            post += ["-b:a", str(audio["bitrate"])]
        if recipe.get("faststart"):
            post += ["-movflags", "+faststart"]

    return pre, post


def _render_command(recipe: dict[str, Any], *, preview: dict[str, Any] | None = None) -> list[str]:
    cmd: list[str] = ["ffmpeg", "-y"]
    if preview and preview.get("start") is not None:
        cmd += ["-ss", str(preview["start"])]
    cmd += recipe.get("pre_input_flags", [])
    cmd += ["-i", recipe["input"]]
    if preview and preview.get("duration") is not None:
        cmd += ["-t", str(preview["duration"])]
    cmd += recipe.get("post_input_flags", [])
    out = (preview or {}).get("output_override") or recipe["output"]
    cmd.append(out)
    return cmd


_AUDIO_FORMAT_ENCODER: dict[str, str] = dict(_VOCAB["audio_format_encoder"])


def _audio_encoder_for(audio_format: str) -> str:
    return _AUDIO_FORMAT_ENCODER.get(audio_format, "copy")


def _preview_output_for(recipe: dict[str, Any]) -> str:
    p = Path(recipe["input"])
    container = recipe.get("container", "mp4")
    return str(p.with_name(f"{p.stem}_preview.{container}"))
