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


def _parse_scale(scale: object | None) -> str | None:
    """Resolve a scale shorthand or WxH/W:H literal to a 'W:H' string.

    Returns None when *scale* is None. Raises ValueError for unrecognised values.

    Coerces with ``str()`` before parsing because the value comes from MODEL OUTPUT, not from
    typed Python: the annotation says what we want, it does not enforce it. Observed 2026-08-04
    in an eval run — `create a 4K thumbnail ...` produced `{"scale": 2}`, and `scale.strip()`
    raised `'int' object has no attribute 'strip'`, an unhandled TypeError escaping as a crash
    instead of this function's own ValueError. The sibling parsers here already coerce
    (`_parse_aspect`, `_parse_crf`); this one was the outlier.
    """
    if scale is None:
        return None
    text = str(scale).strip()
    key = text.lower()
    if key in _SCALE_PRESETS:
        return _SCALE_PRESETS[key]
    m = _WxH_RE.match(text)
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
        return f"crop=min(iw\\,ih*{aw}/{ah}):min(ih\\,iw*{ah}/{aw})"

    if width and height:
        effective_fit = fit or "crop"
        if effective_fit == "stretch":
            return f"scale={width}:{height}"
        if effective_fit == "pad":
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase," f"crop={width}:{height}"
        )

    if width:
        return f"scale=min({width}\\,iw):-2"
    if height:
        return f"scale=-2:{height}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Recipe + command rendering.
# ─────────────────────────────────────────────────────────────────────────────


_OUTPUT_SUFFIX_BY_MODE: dict[str, str] = dict(_VOCAB["output_suffix_by_mode"])


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
    # aac track. Only adjust_volume currently routes audio-only inputs here.
    audio_only = mode == "adjust_volume" and not probe.get("video_codec") and not probe.get("width")
    if audio_only:
        container = (
            options.get("container")
            or probe.get("container")
            or input_path.suffix.lstrip(".")
            or container
        )
        audio_codec = _audio_encoder_for(container)

    if container == "gif":
        video_encoder = ""
        pixel_format = ""
        audio_codec = ""
        faststart = False

    output_options = dict(options)
    output_options["container"] = container
    raw_output = options.get("output_path")
    if raw_output:
        out = Path(raw_output)
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
    if mode == "trim":
        recipe["trim"] = {
            "start": options.get("start"),
            "duration": options.get("duration"),
            "end": options.get("end"),
        }
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
        recipe["at_time"] = options.get("at_time", "00:00:01")
        recipe["image_format"] = options.get("image_format", "jpg")
        recipe["scale"] = _parse_scale(options.get("scale"))
        recipe.pop("audio", None)
    if mode == "compress" and options.get("target_size_mb") is not None:
        recipe["target_size_mb"] = options["target_size_mb"]
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
        if trim.get("start") is not None:
            pre += ["-ss", str(trim["start"])]
        if trim.get("duration") is not None:
            post += ["-t", str(trim["duration"])]
        elif trim.get("end") is not None:
            post += ["-to", str(trim["end"])]

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
        # Optional trim-while-extract: -ss before -i (fast seek), -to after.
        trim = recipe.get("trim", {})
        if trim.get("start") is not None:
            pre += ["-ss", str(trim["start"])]
        if trim.get("end") is not None:
            post += ["-to", str(trim["end"])]
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
