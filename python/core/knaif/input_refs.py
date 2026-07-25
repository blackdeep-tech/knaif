"""Pure token-classification primitives for file references.

No eval, no corpus, no planner dependencies.  Consumed by:
- knaif/nl_clarify_gate.py  (runtime)
- knaif/evalsuite/descriptor_analysis.py  (analysis-only)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── media vocabulary ──────────────────────────────────────────────────────────

AUDIO_EXTS = frozenset({"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"})
VIDEO_EXTS = frozenset({"mp4", "mov", "mkv", "webm", "avi", "flv", "wmv", "m4v"})
IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff"})
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS | frozenset({"srt", "vtt"})

# Type keyword → extension class it implies.
TYPE_KEYWORDS: dict[str, frozenset[str]] = {}
for _w in ("audio", "sound", "music", "track", "song", "voice"):
    TYPE_KEYWORDS[_w] = AUDIO_EXTS
for _w in ("video", "clip", "movie", "footage", "film", "vid"):
    TYPE_KEYWORDS[_w] = VIDEO_EXTS
for _w in ("image", "photo", "picture", "frame", "thumbnail", "thumb", "screenshot", "still"):
    TYPE_KEYWORDS[_w] = IMAGE_EXTS

# Words that carry no discriminating signal on their own.
STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "it",
        "my",
        "your",
        "file",
        "files",
        "one",
        "thing",
        "please",
        "to",
        "of",
        "in",
        "from",
        "with",
    }
)

GLOB_CHARS = frozenset("*?[")

# A filename is recognized structurally: a stem followed by a plausible file
# extension (2–4 chars containing at least one letter). Extension-CLASS vocab
# (MEDIA_EXTS / TYPE_KEYWORDS) stays media-specific and is only used for
# descriptor resolution ("the audio file") — but plain inline-filename
# detection is extension-agnostic, so any skill's file types (.pdf, .docx, .md,
# .mp4, …) are recognized without baking that skill's vocab into core.
FILENAME_RE = re.compile(r"\b[\w\-]+\.[A-Za-z0-9]{2,4}\b", re.ASCII)

# Extension-less ASCII identifier — emitted filename stem without extension.
# Must contain at least one structural marker (underscore, hyphen, or digit)
# to distinguish ``clip_4k`` from bare English words like ``it`` or ``the``.
_STEM_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$")
_STEM_STRUCTURE = re.compile(r"[_\-]|\d")

# ── data ──────────────────────────────────────────────────────────────────────


@dataclass
class Resolution:
    count: int
    mode: str | None  # "structural" | "attribute" | "both" | "generic" | None
    label: str  # "resolves_unique" | "resolves_ambiguous" | "resolves_none"
    low_confidence: bool


# ── helpers ───────────────────────────────────────────────────────────────────


def _ext_of(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.lower()


def _words(token: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", token.lower())


def _is_file_ext(ext: str) -> bool:
    """A plausible file extension: 2–4 chars with at least one letter.

    Excludes digit-only tails ("H.264", "3.14") and 1-char tails ("e.g").
    """
    return 2 <= len(ext) <= 4 and any(c.isalpha() for c in ext)


def _has_file_ext(token: str) -> bool:
    return "." in token and _is_file_ext(_ext_of(token))


# ── classification ────────────────────────────────────────────────────────────


def classify_token(token: str, available: set[str]) -> str:
    """Classify one emitted input token.

    Returns one of: "chain", "glob", "exact", "stem", "descriptor".

    "stem"       — extension-less ASCII identifier (e.g. ``clip_4k``); handled
                   by the deterministic stem resolver, not the clarify gate.
    "descriptor" — natural-language text with spaces (e.g. "the 4K video").
    """
    if token.startswith("$"):
        return "chain"
    if any(c in token for c in GLOB_CHARS):
        return "glob"
    if token in available or _has_file_ext(token):
        return "exact"
    if _STEM_RE.match(token) and _STEM_STRUCTURE.search(token):
        return "stem"
    return "descriptor"


# ── resolution ────────────────────────────────────────────────────────────────


def resolve_input(token: str, available_files: set[str]) -> Resolution:
    """Resolve a descriptor *token* against the *available_files* set.

    Structural: type/extension cues ("the audio file", "the mov").
    Attribute:  qualifier substrings ("the 4K file" → clip_4k.mp4).
    Generic:    no cue at all ("it", "the file") — refers to everything available.
    """
    words = _words(token)

    implied_exts: set[str] = set()
    for w in words:
        if w in TYPE_KEYWORDS:
            implied_exts |= TYPE_KEYWORDS[w]
        if w in MEDIA_EXTS:
            implied_exts.add(w)

    quals = [
        w for w in words if w not in STOPWORDS and w not in TYPE_KEYWORDS and w not in MEDIA_EXTS
    ]

    structural = (
        {f for f in available_files if _ext_of(f) in implied_exts} if implied_exts else set()
    )
    attribute = {f for f in available_files for q in quals if q in f.lower()}

    if not implied_exts and not quals:
        matched = set(available_files)
        mode: str | None = "generic"
    else:
        matched = structural | attribute
        if not matched:
            mode = None
        elif structural and attribute:
            mode = "both"
        elif structural:
            mode = "structural"
        else:
            mode = "attribute"

    count = len(matched)
    label = (
        "resolves_none" if count == 0 else "resolves_unique" if count == 1 else "resolves_ambiguous"
    )
    return Resolution(
        count=count,
        mode=mode,
        label=label,
        low_confidence=(mode == "attribute"),
    )


# ── inline-filename parser ────────────────────────────────────────────────────


def parse_inline_filenames(utterance: str) -> set[str]:
    """Return real filename tokens (stem + file extension) written in the utterance."""
    return {
        m.group(0)
        for m in FILENAME_RE.finditer(utterance or "")
        if _is_file_ext(_ext_of(m.group(0)))
    }
