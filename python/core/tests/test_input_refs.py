"""Characterization tests for knaif.input_refs — pure token-classification primitives.

These mirror the relevant portions of test_descriptor_analysis.py but import from
the core module rather than the evalsuite.  Both test files must pass identically;
descriptor_analysis.py re-exports these functions from knaif.input_refs.
"""

from __future__ import annotations

from knaif.input_refs import (
    Resolution,
    classify_token,
    parse_inline_filenames,
    resolve_input,
)

# ── classify_token ────────────────────────────────────────────────────────────


def test_classify_chain():
    assert classify_token("$1", set()) == "chain"
    assert classify_token("$prev.output", set()) == "chain"


def test_classify_glob():
    assert classify_token("*.mp4", set()) == "glob"
    assert classify_token("clip_?.mov", set()) == "glob"


def test_classify_exact_by_extension():
    assert classify_token("clip_4k.mp4", set()) == "exact"
    assert classify_token("audio.mp3", set()) == "exact"


def test_classify_exact_by_membership():
    assert classify_token("rawfile", {"rawfile"}) == "exact"


def test_classify_stem():
    assert classify_token("clip_4k", set()) == "stem"
    assert classify_token("clip_mov", set()) == "stem"
    assert classify_token("clip_no_audio", set()) == "stem"


def test_classify_stem_not_descriptor():
    assert classify_token("clip_4k", set()) != "descriptor"


def test_classify_descriptor():
    assert classify_token("the 4K video", set()) == "descriptor"
    assert classify_token("the audio file", set()) == "descriptor"
    assert classify_token("the mov", set()) == "descriptor"
    assert classify_token("it", set()) == "descriptor"
    assert classify_token("silent clip", set()) == "descriptor"


# ── resolve_input ─────────────────────────────────────────────────────────────


def test_resolve_structural_by_type_keyword():
    r = resolve_input("the audio file", {"movie.mp4", "audio.mp3"})
    assert r.count == 1
    assert r.label == "resolves_unique"
    assert r.mode in ("structural", "both")


def test_resolve_structural_by_bare_extension():
    r = resolve_input("the mov", {"clip.mov", "song.mp3"})
    assert r.count == 1
    assert r.label == "resolves_unique"
    assert r.mode == "structural"


def test_resolve_attribute_substring_low_confidence():
    r = resolve_input("the 4K file", {"clip_4k.mp4"})
    assert r.count == 1
    assert r.label == "resolves_unique"
    assert r.mode == "attribute"
    assert r.low_confidence is True


def test_resolve_none_when_no_match():
    r = resolve_input("the 4K file", {"plain.mp4"})
    assert r.count == 0
    assert r.label == "resolves_none"


def test_resolve_none_when_empty_context():
    r = resolve_input("the audio file", set())
    assert r.count == 0
    assert r.label == "resolves_none"


def test_resolve_ambiguous_multiple_type_matches():
    r = resolve_input("the mp4", {"a.mp4", "b.mp4"})
    assert r.count == 2
    assert r.label == "resolves_ambiguous"


def test_resolve_generic_single_file():
    r = resolve_input("it", {"only.mp4"})
    assert r.count == 1
    assert r.label == "resolves_unique"
    assert r.mode == "generic"


def test_resolve_generic_multiple_ambiguous():
    r = resolve_input("the file", {"a.mp4", "b.mov"})
    assert r.count == 2
    assert r.label == "resolves_ambiguous"


# ── Resolution dataclass ──────────────────────────────────────────────────────


def test_resolution_fields():
    r = Resolution(count=1, mode="structural", label="resolves_unique", low_confidence=False)
    assert r.count == 1
    assert r.mode == "structural"
    assert r.label == "resolves_unique"
    assert r.low_confidence is False


# ── parse_inline_filenames ────────────────────────────────────────────────────


def test_parse_inline_picks_real_filenames():
    assert parse_inline_filenames("compress clip_4k.mp4 to half") == {"clip_4k.mp4"}


def test_parse_inline_ignores_descriptors():
    assert parse_inline_filenames("convert the audio file to flac") == set()


def test_parse_inline_multiple():
    got = parse_inline_filenames("merge a.mp4 and b.mov")
    assert got == {"a.mp4", "b.mov"}


def test_parse_inline_empty_string():
    assert parse_inline_filenames("") == set()


def test_parse_inline_audio_extension():
    assert parse_inline_filenames("extract audio from track.flac") == {"track.flac"}


def test_parse_inline_cjk_surrounding_text():
    """Filename surrounded by CJK characters must be found (word-boundary fix)."""
    assert parse_inline_filenames("将clip.mp4转换为H.264 MP4") == {"clip.mp4"}


def test_parse_inline_cjk_multiple():
    assert parse_inline_filenames("从clip.mp4提取音频，保存为track.mp3") == {
        "clip.mp4",
        "track.mp3",
    }


# ── non-media (document) extensions: structural recognition ───────────────────


def test_parse_inline_recognizes_document_extensions():
    got = parse_inline_filenames("merge report.pdf and notes.docx")
    assert got == {"report.pdf", "notes.docx"}


def test_parse_inline_recognizes_short_and_office_extensions():
    assert parse_inline_filenames("get the text from sample.txt") == {"sample.txt"}
    assert parse_inline_filenames("open brief.md please") == {"brief.md"}
    assert parse_inline_filenames("inspect data.xlsx and deck.pptx") == {
        "data.xlsx",
        "deck.pptx",
    }


def test_parse_inline_rejects_digit_only_and_one_char_extensions():
    # "H.264" (digit-only ext) and "e.g" (1-char ext) are not filenames.
    assert parse_inline_filenames("encode to H.264 e.g. fast") == set()
    assert parse_inline_filenames("version 3.14 of the spec") == set()


def test_classify_document_filename_is_exact():
    assert classify_token("report.pdf", set()) == "exact"
    assert classify_token("brief.md", set()) == "exact"
