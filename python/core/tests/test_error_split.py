"""Tests for the error split: where each wrong eval row went wrong (retriever, model, gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.error_split import capture_shown, classify, format_report, language, split


def _row(
    rid="ffmpeg_001",
    idx=0,
    expected="plan",
    actual="plan",
    correct=True,
    plan=None,
    knaif=1.0,
    utterance="convert clip.mp4 to mkv",
    tags=("convert",),
):
    return {
        "id": rid,
        "utterance_idx": idx,
        "utterance": utterance,
        "expected_outcome": expected,
        "actual_outcome": actual,
        "outcome_correct": correct,
        "tags": list(tags),
        "plan": {"plan": plan if plan is not None else []},
        "knaif_score": knaif,
    }


def _step(tool, **args):
    return {"tool": tool, "args": args}


CONVERT = ["convert_video"]
SHOWN = ["convert_video", "compress_video", "trim_video"]


# ── classify: a plan was expected ────────────────────────────────────────────


def test_right_tool_full_score_is_correct():
    row = _row(plan=[_step("convert_video", inputs=["clip.mp4"])])
    assert classify(row, CONVERT, SHOWN) == "correct"


def test_right_tool_low_score_is_an_argument_error():
    row = _row(plan=[_step("convert_video", inputs=["clip.mp4"])], knaif=0.5)
    assert classify(row, CONVERT, SHOWN) == "arg_error"


def test_expected_tool_not_shown_is_a_retriever_miss_whatever_the_model_did():
    asked = _row(actual="clarify", correct=False, plan=[_step("clarify", question="Which?")])
    wrong = _row(plan=[_step("compress_video", inputs=["clip.mp4"])], knaif=0.0)
    assert classify(asked, CONVERT, ["compress_video"]) == "retriever_miss"
    assert classify(wrong, CONVERT, ["compress_video"]) == "retriever_miss"


def test_a_correct_row_is_correct_even_when_the_tool_was_not_shown():
    # The model can still name a tool it was not shown; a right answer is never a miss.
    row = _row(plan=[_step("convert_video", inputs=["clip.mp4"])])
    assert classify(row, CONVERT, ["compress_video"]) == "correct"


def test_an_unshown_tool_the_model_planned_anyway_is_not_a_retriever_miss():
    # The model named the unshown tool itself; what failed came after retrieval.
    chain = ["watermark", "rotate_pages"]
    failed = _row(actual="error", correct=False, plan=[_step("watermark"), _step("rotate_pages")])
    assert classify(failed, chain, ["watermark"]) == "execution_failed"
    # Only the tool the model left out counts against the retriever.
    partial = _row(plan=[_step("watermark")], knaif=0.5)
    assert classify(partial, chain, ["watermark"]) == "retriever_miss"


def test_wrong_tool_with_the_right_outcome_is_a_wrong_tool():
    row = _row(plan=[_step("compress_video", inputs=["clip.mp4"])], knaif=0.0)
    assert classify(row, CONVERT, SHOWN) == "wrong_tool"


def test_other_tools_the_grader_accepted_in_full_are_correct():
    # "reverse it, then compress it": the corpus names only the first tool, the model chains
    # both, and the executing grader accepts the file. Trust the grade, not the tool names.
    chained = _row(plan=[_step("convert_video"), _step("compress_video")], knaif=1.0)
    other = _row(plan=[_step("compress_video")], knaif=1.0)
    assert classify(chained, CONVERT, SHOWN) == "correct"
    assert classify(other, CONVERT, ["compress_video"]) == "correct"


def test_an_extra_step_is_a_wrong_tool():
    row = _row(plan=[_step("convert_video"), _step("compress_video")], knaif=0.5)
    assert classify(row, CONVERT, SHOWN) == "wrong_tool"


def test_a_trailing_done_does_not_count_as_a_step():
    row = _row(plan=[_step("convert_video", inputs=["clip.mp4"]), _step("done")])
    assert classify(row, CONVERT, SHOWN) == "correct"


def test_the_model_asking_is_model_asked():
    row = _row(actual="clarify", correct=False, plan=[_step("clarify", question="Which format?")])
    assert classify(row, CONVERT, SHOWN) == "model_asked"


def test_the_model_refusing_is_model_refused():
    row = _row(actual="reject", correct=False, plan=[_step("reject", reason="no")])
    assert classify(row, CONVERT, SHOWN) == "model_refused"


def test_a_tool_plan_turned_into_a_question_is_gate_asked():
    # The stored plan still holds the model's tool: a gate asked after validation.
    row = _row(
        actual="clarify",
        correct=False,
        plan=[_step("convert_video", inputs=["mp3"], target="flac")],
    )
    assert classify(row, CONVERT, SHOWN) == "gate_asked"


def test_the_invented_file_question_is_gate_invented_file():
    q = "You didn't mention 'video.mp4' in your request — which file should I work on?"
    row = _row(actual="clarify", correct=False, plan=[_step("clarify", question=q)])
    assert classify(row, CONVERT, SHOWN) == "gate_invented_file"


def test_parse_failures_are_parse_errors():
    as_outcome = _row(actual="parse_error", correct=False, plan=[])
    as_question = _row(
        actual="clarify",
        correct=False,
        plan=[_step("clarify", question="Could not parse your request. Please rephrase.")],
    )
    assert classify(as_outcome, CONVERT, SHOWN) == "parse_error"
    assert classify(as_question, CONVERT, SHOWN) == "parse_error"


def test_right_tool_that_failed_to_run_is_execution_failed():
    row = _row(actual="error", correct=False, plan=[_step("convert_video", inputs=["clip.mp4"])])
    assert classify(row, CONVERT, SHOWN) == "execution_failed"


def test_wrong_tool_that_failed_to_run_is_a_wrong_tool():
    row = _row(actual="error", correct=False, plan=[_step("trim_video", input="clip.mp4")])
    assert classify(row, CONVERT, SHOWN) == "wrong_tool"


def test_a_chain_needs_every_tool_in_order_and_every_tool_shown():
    chain = ["trim_video", "extract_audio"]
    ok = _row(plan=[_step("trim_video"), _step("extract_audio")])
    swapped = _row(plan=[_step("extract_audio"), _step("trim_video")], knaif=0.5)
    assert classify(ok, chain, ["trim_video", "extract_audio"]) == "correct"
    assert classify(swapped, chain, ["trim_video", "extract_audio"]) == "wrong_tool"
    # extract_audio was not shown, but the model planned it: the order is the error.
    assert classify(swapped, chain, ["trim_video"]) == "wrong_tool"


def test_without_shown_tools_there_is_no_retriever_bucket():
    row = _row(plan=[_step("compress_video")], knaif=0.0)
    assert classify(row, CONVERT, None) == "wrong_tool"


# ── classify: a question or a refusal was expected ───────────────────────────


def test_expected_question_answered_with_a_plan_is_acted_instead():
    planned = _row(expected="clarify", actual="plan", correct=False, plan=[_step("convert_video")])
    failed = _row(expected="clarify", actual="error", correct=False, plan=[_step("convert_video")])
    assert classify(planned, [], SHOWN) == "acted_instead"
    assert classify(failed, [], SHOWN) == "acted_instead"


def test_expected_question_answered_with_a_refusal_is_refused_instead():
    row = _row(expected="clarify", actual="reject", correct=False, plan=[_step("reject")])
    assert classify(row, [], SHOWN) == "refused_instead"


def test_expected_refusal_answered_with_a_question_is_asked_instead():
    row = _row(expected="reject", actual="clarify", correct=False, plan=[_step("clarify")])
    assert classify(row, [], SHOWN) == "asked_instead"


def test_expected_question_asked_is_correct():
    row = _row(expected="clarify", actual="clarify", correct=True, plan=[_step("clarify")])
    assert classify(row, [], SHOWN) == "correct"


# ── language ─────────────────────────────────────────────────────────────────


def test_language_comes_from_a_language_tag_that_fits_the_script():
    assert language(["convert", "multilingual", "zh"], "把 clip.mp4 转换") == "zh"
    assert language(["multilingual", "bg"], "конвертирай clip.mp4") == "bg"
    assert language(["convert"], "convert clip.mp4") == "en"


def test_language_falls_back_to_the_script_per_utterance():
    # A row's utterances are paraphrases, often in other languages than its tags say.
    assert language(["convert"], "конвертирай mp3 в flac") == "cyrillic"
    assert language(["convert"], "将MP3转换为FLAC") == "cjk"
    assert language(["convert"], "convertir la vidéo") == "latin"
    # A tag that does not fit the utterance's script is ignored.
    assert language(["multilingual", "zh"], "convert clip.mp4") == "en"


# ── split ────────────────────────────────────────────────────────────────────


def _corpus():
    return {
        "ffmpeg_001": {"expected_tool": "convert_video", "tags": ["convert"]},
        "ffmpeg_002": {"expected_tool": "convert_video", "tags": ["convert", "multilingual", "zh"]},
        "ffmpeg_003": {"expected_tool": "clarify", "tags": ["clarify"]},
    }


def _board():
    return {
        "rows": [
            _row("ffmpeg_001", plan=[_step("convert_video", inputs=["clip.mp4"])]),
            _row(
                "ffmpeg_002",
                actual="clarify",
                correct=False,
                plan=[_step("clarify", question="Which?")],
                utterance="把 clip.mp4 转换",
                tags=("convert", "multilingual", "zh"),
            ),
            _row(
                "ffmpeg_003",
                expected="clarify",
                actual="plan",
                correct=False,
                plan=[_step("convert_video")],
                tags=("clarify",),
            ),
        ]
    }


def test_split_counts_buckets_by_language_and_tag():
    shown = {"ffmpeg_001#0": SHOWN, "ffmpeg_002#0": ["compress_video"], "ffmpeg_003#0": SHOWN}
    res = split(_board(), _corpus(), shown)
    assert res["total"] == 3
    assert res["buckets"] == {"correct": 1, "retriever_miss": 1, "acted_instead": 1}
    assert res["by_language"]["zh"] == {"retriever_miss": 1}
    assert res["by_tag"]["convert"] == {"retriever_miss": 1}
    assert res["by_tag"]["clarify"] == {"acted_instead": 1}
    assert {r["key"] for r in res["rows"]} == {"ffmpeg_002#0", "ffmpeg_003#0"}
    # Rates need their denominators: every row per language, right or not.
    assert res["language_totals"] == {"en": 2, "zh": 1}


def test_split_counts_rows_right_although_their_tool_was_not_shown():
    # The model can name a tool its prompt did not list; such a row is right, not a miss.
    shown = {"ffmpeg_001#0": ["compress_video"], "ffmpeg_002#0": SHOWN, "ffmpeg_003#0": SHOWN}
    res = split(_board(), _corpus(), shown)
    assert res["retrieval"]["missed_served"] == ["ffmpeg_001#0"]
    assert res["retrieval"]["right_unshown"] == ["ffmpeg_001#0"]


def test_split_lists_rows_the_grader_accepted_with_other_tools():
    # Not errors: candidates for the corpus review (the row's expected tools are incomplete).
    board = {"rows": [_row("ffmpeg_001", plan=[_step("convert_video"), _step("compress_video")])]}
    res = split(board, _corpus(), {"ffmpeg_001#0": SHOWN})
    assert res["buckets"] == {"correct": 1}
    assert res["accepted_other_tools"] == [
        {
            "key": "ffmpeg_001#0",
            "expected": ["convert_video"],
            "got": ["convert_video", "compress_video"],
        }
    ]


def test_split_compares_shown_tools_as_served_with_today():
    served = {"ffmpeg_001#0": SHOWN, "ffmpeg_002#0": ["compress_video"], "ffmpeg_003#0": SHOWN}
    today = {"ffmpeg_001#0": ["compress_video"], "ffmpeg_002#0": SHOWN, "ffmpeg_003#0": SHOWN}
    res = split(_board(), _corpus(), served, shown_now=today)
    r = res["retrieval"]
    # Only plan rows count: 001 and 002.
    assert r["n"] == 2
    assert r["missed_served"] == ["ffmpeg_002#0"]
    assert r["missed_now"] == ["ffmpeg_001#0"]
    row = next(x for x in res["rows"] if x["key"] == "ffmpeg_002#0")
    assert row["shown_now"] is True


def test_split_against_another_board_pairs_rows_and_tests_them():
    other = _board()
    # The other model got 002 right and 003 right too.
    other["rows"][1] = _row(
        "ffmpeg_002",
        plan=[_step("convert_video")],
        utterance="把 clip.mp4 转换",
        tags=("convert", "multilingual", "zh"),
    )
    other["rows"][2] = _row(
        "ffmpeg_003", expected="clarify", actual="clarify", plan=[_step("clarify")]
    )
    shown = {"ffmpeg_001#0": SHOWN, "ffmpeg_002#0": SHOWN, "ffmpeg_003#0": SHOWN}
    res = split(_board(), _corpus(), shown, compare=other)
    p = res["paired"]
    assert p["n"] == 3
    assert p["only_this_right"] == 0 and p["only_other_right"] == 2
    assert p["mcnemar_p"] == pytest.approx(0.5)
    assert p["transitions"] == {"correct -> model_asked": 1, "correct -> acted_instead": 1}


def test_split_refuses_a_board_row_missing_from_the_corpus():
    board = {"rows": [_row("ffmpeg_999", plan=[_step("convert_video")])]}
    with pytest.raises(KeyError, match="ffmpeg_999"):
        split(board, _corpus(), {})


def test_format_report_names_every_bucket_and_language():
    shown = {"ffmpeg_001#0": SHOWN, "ffmpeg_002#0": ["compress_video"], "ffmpeg_003#0": SHOWN}
    out = format_report(split(_board(), _corpus(), shown), title="v2 ffmpeg")
    assert "v2 ffmpeg" in out
    assert "retriever_miss" in out and "acted_instead" in out
    assert "zh" in out


# ── capture ──────────────────────────────────────────────────────────────────


def test_capture_shown_records_the_model_visible_tools_per_utterance():
    shown = capture_shown("ffmpeg", Path("skills/ffmpeg/data/eval.jsonl"))
    first = json.loads(
        Path("skills/ffmpeg/data/eval.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    key = f"{first['id']}#0"
    assert key in shown
    assert first["expected_tool"] in shown[key]
    assert not {"clarify", "reject", "done"} & set(shown[key])
    assert 1 <= len(shown[key]) <= 5


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli(args: list[str]) -> None:
    import sys

    from knaif.evalsuite.cli import main

    old = sys.argv
    sys.argv = ["knaif.evalsuite", "error-split", *args]
    try:
        main()
    finally:
        sys.argv = old


def test_cli_capture_writes_the_shown_tools(tmp_path):
    out = tmp_path / "shown.json"
    _cli(["--skill", "ffmpeg", "--capture", str(out)])
    shown = json.loads(out.read_text(encoding="utf-8"))
    assert "ffmpeg_001#0" in shown


def test_cli_report_saves_the_split_and_its_report(tmp_path):
    corpus = tmp_path / "eval.jsonl"
    corpus.write_text(
        "\n".join(json.dumps({"id": k, "utterances": ["u"], **v}) for k, v in _corpus().items()),
        encoding="utf-8",
    )
    board = tmp_path / "board.json"
    board.write_text(json.dumps(_board()), encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps(_board()), encoding="utf-8")
    shown = tmp_path / "shown.json"
    shown.write_text(
        json.dumps(
            {"ffmpeg_001#0": SHOWN, "ffmpeg_002#0": ["compress_video"], "ffmpeg_003#0": SHOWN}
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    _cli(
        [
            "--skill",
            "ffmpeg",
            "--corpus",
            str(corpus),
            "--scoreboard",
            str(board),
            "--shown",
            str(shown),
            "--shown-now",
            str(shown),
            "--compare",
            str(other),
            "--title",
            "test",
            "--save",
            str(out),
        ]
    )
    res = json.loads((out / "split.json").read_text(encoding="utf-8"))
    assert res["buckets"]["retriever_miss"] == 1
    assert res["paired"]["n"] == 3
    assert "# Error split: test" in (out / "report.md").read_text(encoding="utf-8")


def test_format_report_lists_rows_graded_right_with_other_tools():
    board = {"rows": [_row("ffmpeg_001", plan=[_step("convert_video"), _step("compress_video")])]}
    out = format_report(split(board, _corpus(), {"ffmpeg_001#0": SHOWN}), title="t")
    assert "Graded right with other tools" in out
    assert "convert_video+compress_video" in out


def test_split_flags_rows_that_planned_a_tool_the_prompt_did_not_show():
    # The model knows tool names from training but not, unshown, their arguments: a gate asking
    # about such a plan is partly retrieval's doing, so the row says which tools were unshown.
    corpus = {"documents_1": {"expected_tools": ["watermark", "rotate_pages"], "tags": ["hard2"]}}
    board = {
        "rows": [
            _row(
                "documents_1",
                actual="clarify",
                correct=False,
                plan=[_step("watermark"), _step("rotate_pages", angle=-90)],
            )
        ]
    }
    res = split(board, corpus, {"documents_1#0": ["watermark"]})
    row = res["rows"][0]
    assert row["bucket"] == "gate_asked"
    assert row["unshown_planned"] == ["rotate_pages"]
    assert res["unshown_planned"] == {"gate_asked": 1}
    assert "planned a tool the prompt did not show" in format_report(res, title="t")
