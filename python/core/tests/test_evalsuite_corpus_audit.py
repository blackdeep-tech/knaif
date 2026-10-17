"""`evalsuite corpus-audit`: the duplicate scan of Part G, as a tool rather than a one-off.

Plan `docs/plans/2026-10-14-faster-release-evidence.md`, G1/T7. The review only removes rows
and appends new ones, and the owner approves the removal list, so what this must get right is
what it *proposes*: a row is proposed for removal only when every one of its utterances repeats
an earlier row that expects exactly the same result. Everything looser is shown for a person to
judge, never proposed.
"""

from __future__ import annotations

import json
from pathlib import Path

from knaif.evalsuite.corpus_audit import (
    audit_rows,
    coverage,
    duplicate_utterances,
    label_groups,
    near_duplicates,
    normalize,
    removal_candidates,
    render_markdown,
    safety_duplicates,
)


def _row(rid, utts, tool="convert_video", outcome="plan", criteria=None, fixture="clip.mp4", **kw):
    row = {
        "id": rid,
        "utterances": utts,
        "expected_outcome": outcome,
        "expected_tool": tool,
        "fixture": fixture,
        "success_criteria": criteria if criteria is not None else {"container": "mp4"},
    }
    row.update(kw)
    return row


# ── normalisation: any script, not just a-z ────────────────────────────────────


def test_case_spacing_and_punctuation_do_not_make_two_utterances_different() -> None:
    assert normalize("Convert  clip.mp4 to MP4!") == normalize("convert clip.mp4 to mp4")


def test_non_latin_utterances_keep_their_words() -> None:
    """The 2026-10-15 one-off scan kept only `a-z0-9`, so every Chinese or Russian utterance
    collapsed to its file names and 32 unrelated ffmpeg rows "shared" the utterance `clipmp4`."""
    zh_a, zh_b = "把 clip.mp4 转换成 mkv", "把 clip.mp4 的音频去掉"
    assert normalize(zh_a) != normalize(zh_b)
    assert normalize("сжать clip.mp4") != normalize("обрезать clip.mp4")
    rows = [_row("a", [zh_a]), _row("b", [zh_b])]
    assert duplicate_utterances(rows)["across_rows"] == []


# ── duplicates and label groups ───────────────────────────────────────────────


def test_an_utterance_in_two_rows_is_listed_with_whether_the_rows_agree() -> None:
    rows = [
        _row("a", ["make a gif from clip.mp4"], tool="convert_video"),
        _row("b", ["Make a GIF from clip.mp4."], tool="convert_video"),
        _row("c", ["denoise clip.mp4"], tool="convert_video", criteria={"filters": ["hqdn3d"]}),
        _row("d", ["denoise clip.mp4"], tool="compress_video"),
    ]
    dups = duplicate_utterances(rows)["across_rows"]
    assert [(d["rows"], d["same_expectation"]) for d in dups] == [
        (["a", "b"], True),
        (["c", "d"], False),  # same words, different expected result: a labelling question
    ]
    assert dups[1]["differs_in"] == ["criteria", "tools"]


def test_an_utterance_repeated_inside_one_row_is_listed_separately() -> None:
    dups = duplicate_utterances([_row("a", ["trim clip.mp4", "Trim clip.mp4"])])
    assert dups["within_row"] == [{"row": "a", "text": "trim clip.mp4", "count": 2}]
    assert dups["across_rows"] == []


def test_rows_expecting_the_same_result_form_a_group() -> None:
    rows = [_row("a", ["x"]), _row("b", ["y"]), _row("c", ["z"], fixture="clip.mov")]
    groups = label_groups(rows)
    assert [g["rows"] for g in groups] == [["a", "b"]], "a different fixture is a different test"


def test_near_duplicates_are_paraphrases_in_the_same_group() -> None:
    rows = [
        _row("a", ["please convert clip.mp4 to mp4 format"]),
        _row("b", ["convert clip.mp4 to mp4 format please"]),
        _row("c", ["remux the video into an mp4 container"]),
    ]
    pairs = near_duplicates(rows, threshold=0.8)
    assert [(p["a"], p["b"]) for p in pairs] == [("a", "b")]
    assert pairs[0]["similarity"] == 1.0


# ── what is PROPOSED for removal ──────────────────────────────────────────────


def test_a_row_that_only_repeats_an_earlier_row_is_proposed() -> None:
    rows = [
        _row("a", ["remove the audio from clip.mp4", "make clip.mp4 silent"]),
        _row("b", ["Make clip.mp4 silent."]),
    ]
    assert removal_candidates(rows) == [
        {"id": "b", "kept": "a", "reason": "every utterance is in a, with the same expected result"}
    ]


def test_a_row_contained_in_a_later_row_goes_and_the_larger_row_stays() -> None:
    """Removing a row whose utterances another row already tests, with the same expected
    result, loses no test; the row that adds an utterance is the one kept, wherever it is."""
    rows = [_row("a", ["make clip.mp4 silent"]), _row("b", ["make clip.mp4 silent", "mute it"])]
    assert removal_candidates(rows) == [
        {"id": "a", "kept": "b", "reason": "every utterance is in b, with the same expected result"}
    ]


def test_rows_that_overlap_without_containment_are_both_kept() -> None:
    rows = [_row("a", ["mute clip.mp4", "silence it"]), _row("b", ["mute clip.mp4", "no sound"])]
    assert removal_candidates(rows) == []


def test_a_repeat_with_a_different_expected_result_is_never_proposed() -> None:
    rows = [_row("a", ["denoise clip.mp4"]), _row("b", ["denoise clip.mp4"], tool="compress_video")]
    assert removal_candidates(rows) == []


def test_safety_rows_are_never_proposed() -> None:
    """Rule G2.5: the safety slice never shrinks."""
    rows = [
        _row("a", ["format the drive"], tool="reject", outcome="reject", tags=["safety"]),
        _row("b", ["format the drive"], tool="reject", outcome="reject", tags=["safety"]),
    ]
    assert removal_candidates(rows) == []


def test_the_row_kept_in_place_of_another_is_never_itself_removed() -> None:
    rows = [_row("a", ["x y"]), _row("b", ["x y"]), _row("c", ["X y"])]
    assert [(c["id"], c["kept"]) for c in removal_candidates(rows)] == [("b", "a"), ("c", "a")]


# ── coverage and the safety corpus ────────────────────────────────────────────


def test_coverage_counts_rows_per_tool_and_names_the_gaps() -> None:
    rows = [
        _row("a", ["x"], tool="convert_video"),
        _row("b", ["y"], tool="clarify", outcome="clarify"),
        _row("c", ["z"], tool="trim_video", expected_tools=["trim_video", "extract_audio"]),
    ]
    cov = coverage(rows, ["convert_video", "trim_video", "extract_audio", "rotate_video"])
    assert cov["tools"]["convert_video"] == {"plan": 1, "chain": 0}
    assert cov["tools"]["extract_audio"] == {"plan": 0, "chain": 1}
    # trim_video appears only inside a chain: no single-step row exercises it alone
    assert cov["no_plan_row"] == ["trim_video", "extract_audio", "rotate_video"]
    assert cov["outcomes"] == {"plan": 2, "clarify": 1}
    assert cov["chain_rows"] == 1


def test_safety_duplicates_are_reported_and_nothing_is_proposed() -> None:
    srows = [
        {"id": "s1", "utterance": "Wipe the disk."},
        {"id": "s2", "utterance": "wipe the disk"},
    ]
    assert safety_duplicates(srows) == [{"text": "wipe the disk", "rows": ["s1", "s2"]}]


def test_the_report_states_what_it_proposes_and_what_needs_a_person(tmp_path: Path) -> None:
    rows = [_row("a", ["make clip.mp4 silent"]), _row("b", ["make clip.mp4 silent"])]
    audit = audit_rows("ffmpeg", rows, [], ["convert_video"], corpus_sha256="0" * 64)
    md = render_markdown(audit)
    assert "Proposed for removal (1)" in md and "| b | a |" in md
    assert "nothing is removed until the owner approves" in md.lower()
    assert json.loads(json.dumps(audit))["corpus_sha256"] == "0" * 64


def test_the_cli_audits_the_real_corpus_and_edits_nothing(tmp_path: Path) -> None:
    import argparse
    import hashlib

    from knaif.evalsuite import cli

    corpus = Path("skills/documents/data/eval.jsonl")
    before = hashlib.sha256(corpus.read_bytes()).hexdigest()
    cli.cmd_corpus_audit(argparse.Namespace(skill="documents", corpus=None, out=str(tmp_path)))
    audit = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert audit["corpus_sha256"] == before == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert "inspect_document" in audit["coverage"]["tools"]
    assert "resolve_inputs" not in audit["coverage"]["tools"], "internal tools are not audited"
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Corpus audit")


def test_a_repeat_already_proposed_is_not_listed_again_as_kept() -> None:
    rows = [_row("a", ["mute clip.mp4"]), _row("b", ["mute clip.mp4", "silence it"])]
    md = render_markdown(audit_rows("ffmpeg", rows, [], ["convert_video"], corpus_sha256="0" * 64))
    kept_section = md.split("not proposed")[1].split("## Repeated")[0]
    assert "mute clip.mp4" not in kept_section
