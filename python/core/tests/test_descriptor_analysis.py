"""Tests for evalsuite.descriptor_analysis: token classification, resolution,
available-files (injection OFF/ON), and binning."""

from __future__ import annotations

from knaif.evalsuite.descriptor_analysis import (
    available_files,
    bin_row,
    bin_stem,
    classify_token,
    extract_input_tokens,
    parse_inline_filenames,
    resolve_input,
    stem_resolves,
)

# ── classify_token ───────────────────────────────────────────────────────────


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
    # No media extension, but literally present in available files.
    assert classify_token("rawfile", {"rawfile"}) == "exact"


def test_classify_stem():
    # Extension-less, ASCII, no spaces — emitted filename stem without extension.
    assert classify_token("clip_4k", set()) == "stem"
    assert classify_token("clip_mov", set()) == "stem"
    assert classify_token("clip_no_audio", set()) == "stem"


def test_classify_stem_not_confused_with_descriptor():
    # Even though "clip_4k" has no extension, it must be stem not descriptor.
    assert classify_token("clip_4k", set()) != "descriptor"


def test_classify_descriptor():
    # NL descriptors have spaces — never stems.
    assert classify_token("the 4K video", set()) == "descriptor"
    assert classify_token("the audio file", set()) == "descriptor"
    assert classify_token("the mov", set()) == "descriptor"
    assert classify_token("it", set()) == "descriptor"
    assert classify_token("silent clip", set()) == "descriptor"


# ── resolve_input ────────────────────────────────────────────────────────────


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
    # injection OFF, descriptor names nothing in the line → clarify.
    r = resolve_input("the audio file", set())
    assert r.count == 0
    assert r.label == "resolves_none"


def test_resolve_ambiguous_multiple_type_matches():
    r = resolve_input("the mp4", {"a.mp4", "b.mp4"})
    assert r.count == 2
    assert r.label == "resolves_ambiguous"


def test_resolve_generic_single_file_resolves():
    # Pure generic descriptor refers to the whole available set.
    r = resolve_input("it", {"only.mp4"})
    assert r.count == 1
    assert r.label == "resolves_unique"
    assert r.mode == "generic"


def test_resolve_generic_multiple_ambiguous():
    r = resolve_input("the file", {"a.mp4", "b.mov"})
    assert r.count == 2
    assert r.label == "resolves_ambiguous"


# ── extract_input_tokens ─────────────────────────────────────────────────────


def test_extract_inputs_list_key():
    plan = {
        "plan": [{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "mp4"}}]
    }
    assert extract_input_tokens(plan) == ["clip.mp4"]


def test_extract_input_singular_key():
    plan = {"plan": [{"tool": "extract_frame", "args": {"input": "the 4K video"}}]}
    assert extract_input_tokens(plan) == ["the 4K video"]


def test_extract_files_key():
    plan = {"plan": [{"tool": "concat", "args": {"files": ["a.mp4", "b.mp4"]}}]}
    assert extract_input_tokens(plan) == ["a.mp4", "b.mp4"]


def test_extract_empty_when_no_input_key_or_no_plan():
    assert extract_input_tokens({"plan": [{"tool": "clarify", "args": {}}]}) == []
    assert extract_input_tokens({"plan": []}) == []
    assert extract_input_tokens(None) == []


# ── parse_inline_filenames ───────────────────────────────────────────────────


def test_parse_inline_picks_real_filenames():
    assert parse_inline_filenames("compress clip_4k.mp4 to half") == {"clip_4k.mp4"}


def test_parse_inline_ignores_descriptors():
    assert parse_inline_filenames("convert the audio file to flac") == set()


def test_parse_inline_multiple():
    got = parse_inline_filenames("merge a.mp4 and b.mov")
    assert got == {"a.mp4", "b.mov"}


# ── available_files (injection OFF/ON) ───────────────────────────────────────


def test_available_off_inline_only():
    # OFF: only filenames written in the line; fixture is NOT visible.
    avail = available_files("clip_4k.mp4", "trim the 4K file to 30s", injection=False)
    assert avail == set()


def test_available_off_includes_inline_filename():
    avail = available_files("clip_4k.mp4", "compress clip_4k.mp4", injection=False)
    assert avail == {"clip_4k.mp4"}


def test_available_on_includes_fixture():
    avail = available_files("clip_4k.mp4", "trim the 4K file to 30s", injection=True)
    assert avail == {"clip_4k.mp4"}


# ── bin_row ──────────────────────────────────────────────────────────────────


def test_bin_prize():
    # expected clarify, model planned, descriptor unresolvable.
    assert bin_row("clarify", "plan", "resolves_none") == "PRIZE"


def test_bin_policy_conflict():
    # expected clarify, but descriptor resolves uniquely.
    assert bin_row("clarify", "plan", "resolves_unique") == "POLICY-CONFLICT"


def test_bin_safe():
    assert bin_row("plan", "plan", "resolves_unique") == "SAFE"


def test_bin_risk():
    assert bin_row("plan", "plan", "resolves_none") == "RISK"
    assert bin_row("plan", "plan", "resolves_ambiguous") == "RISK"


def test_bin_plan_miss():
    assert bin_row("plan", "clarify", "resolves_none") == "PLAN_MISS"


def test_bin_clarify_ok():
    assert bin_row("clarify", "clarify", "resolves_none") == "CLARIFY_OK"


# ── stem_resolves ─────────────────────────────────────────────────────────────


def test_stem_resolves_matching_fixture():
    # clip_4k → clip_4k.mp4: stem matches → resolver would succeed.
    assert stem_resolves("clip_4k", "clip_4k.mp4") is True


def test_stem_no_match_different_stem():
    # clip_mov → clip.mov: stem is "clip", not "clip_mov" → resolver finds nothing.
    assert stem_resolves("clip_mov", "clip.mov") is False


def test_stem_resolves_none_fixture():
    assert stem_resolves("clip_4k", None) is False


def test_stem_resolves_case_insensitive():
    assert stem_resolves("CLIP_4K", "clip_4k.mp4") is True


# ── bin_stem ──────────────────────────────────────────────────────────────────


def test_bin_stem_safe():
    # expected plan, stem resolves → resolver handles correctly.
    assert bin_stem("plan", "plan", resolves=True) == "STEM_SAFE"


def test_bin_stem_risk():
    # expected plan, stem doesn't resolve → resolver would wrongly clarify.
    assert bin_stem("plan", "plan", resolves=False) == "STEM_RISK"


def test_bin_stem_conflict():
    # expected clarify, but stem resolves → policy call (like POLICY-CONFLICT).
    assert bin_stem("clarify", "plan", resolves=True) == "STEM_CONFLICT"


def test_bin_stem_ok():
    # expected clarify, stem doesn't resolve → clarify is correct.
    assert bin_stem("clarify", "plan", resolves=False) == "STEM_OK"
