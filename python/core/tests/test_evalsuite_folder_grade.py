"""Grading a folder row: what the row's folder holds after the run.

`grade_folder(row, row_dir, fixture_dir, check, failures=None)` scores the folder against the
row's expectations, each one a component of the score (their mean):
- **inputs untouched**: every file of the fixture folder is still in the row folder, byte for byte;
- **each `expect_files` entry**: files matching `name` (not inputs), exactly `count` of them when
  given, else at least one, each passing `criteria` through the skill's `check(path, criteria)`;
- **`expect_gone`**: no file (other than an input) matches any of these globs;
- **nothing else**: every other file is an input or matched by an `expect_files` entry;
- **`expected_failures`**: when the row gives it, the run reported that many failed files.
`check` is the skill's verifier for one file, so core stays domain-agnostic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knaif.evalsuite.corpus import CorpusRow
from knaif.evalsuite.folder_rows import grade_folder
from knaif.evalsuite.protocols import VerifyResult


def _row(**kw) -> CorpusRow:
    base: dict = {
        "id": "ffmpeg_batch_001",
        "utterances": ["convert all the .mov files here to mp4 and make them smaller"],
        "expected_outcome": "plan",
        "fixture": "batch/",
        "expect_files": [{"name": "*_small.mp4", "count": 2, "criteria": {"container": "mp4"}}],
        "expect_gone": ["*_converted.mp4"],
    }
    base.update(kw)
    return CorpusRow(**base)


def _ok(path: Path, criteria: dict) -> VerifyResult:
    return VerifyResult(score=1.0, matched=["ok"], verifier_kind="output")


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    fixture = tmp_path / "fixture"
    row = tmp_path / "row"
    for d in (fixture, row):
        d.mkdir()
        (d / "a.mov").write_bytes(b"A")
        (d / "b.mov").write_bytes(b"B")
    return fixture, row


def _write(folder: Path, *names: str) -> None:
    for n in names:
        p = folder / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"out")


def test_the_expected_folder_scores_one(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4")
    res = grade_folder(_row(), row, fixture, _ok)
    assert res.score == 1.0, res.failed
    assert res.verifier_kind == "folder"


def test_a_changed_input_fails(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4")
    (row / "a.mov").write_bytes(b"overwritten")
    res = grade_folder(_row(), row, fixture, _ok)
    assert res.score < 1.0
    assert "input_changed:a.mov" in res.failed


def test_a_missing_input_fails(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4")
    (row / "b.mov").unlink()
    assert "input_missing:b.mov" in grade_folder(_row(), row, fixture, _ok).failed


def test_too_few_expected_files_fail(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4")
    res = grade_folder(_row(), row, fixture, _ok)
    assert "expect_files[0]:count 1 != 2" in res.failed


def test_without_a_count_one_file_is_enough(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4")
    row_def = _row(expect_files=[{"name": "*_small.mp4"}])
    assert grade_folder(row_def, row, fixture, _ok).score == 1.0


def test_a_file_failing_its_criteria_lowers_the_score(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4")

    def half(path: Path, criteria: dict) -> VerifyResult:
        good = path.name.startswith("a")
        return VerifyResult(
            score=1.0 if good else 0.0, failed=[] if good else ["container"], verifier_kind="output"
        )

    res = grade_folder(_row(), row, fixture, half)
    assert 0.0 < res.score < 1.0
    assert "b_small.mp4:container" in res.failed


def test_a_left_over_intermediate_fails(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4", "a_converted.mp4")
    res = grade_folder(_row(), row, fixture, _ok)
    assert "gone:a_converted.mp4" in res.failed


def test_an_unexpected_file_fails(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4", "notes.txt")
    assert "unexpected:notes.txt" in grade_folder(_row(), row, fixture, _ok).failed


def test_names_in_subfolders_match_with_a_path_glob(dirs):
    fixture, row = dirs
    _write(row, "small/a.mp4", "small/b.mp4")
    row_def = _row(expect_files=[{"name": "small/*.mp4", "count": 2}], expect_gone=None)
    assert grade_folder(row_def, row, fixture, _ok).score == 1.0


def test_expected_failures_are_compared_when_given(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4")
    row_def = _row(expect_files=[{"name": "*_small.mp4", "count": 1}], expected_failures=1)
    assert grade_folder(row_def, row, fixture, _ok, failures=1).score == 1.0
    res = grade_folder(row_def, row, fixture, _ok, failures=0)
    assert "failures 0 != 1" in res.failed


def test_a_row_that_expects_failures_needs_the_count(dirs):
    fixture, row = dirs
    _write(row, "a_small.mp4")
    row_def = _row(expect_files=[{"name": "*_small.mp4", "count": 1}], expected_failures=1)
    assert "failures not reported" in grade_folder(row_def, row, fixture, _ok).failed


# ── scoring: a folder row with an executing verifier is graded on its folder ─────────────────

from knaif.evalsuite.runner import AgentOutput  # noqa: E402
from knaif.evalsuite.scoring import score_corpus  # noqa: E402


def _folder_output(row_dir: Path, fixture: Path) -> AgentOutput:
    return AgentOutput(
        id="ffmpeg_batch_001",
        utterance="convert all the .mov files here to mp4 and make them smaller",
        plan={"plan": [{"tool": "convert_video", "args": {"inputs": ["*.mov"]}}]},
        artifact=None,
        outcome="plan",
        latency_ms=1.0,
        folder_dir=row_dir,
        fixture_path=fixture,
    )


def test_score_corpus_grades_a_folder_row_on_its_folder(dirs, tmp_path):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4")
    seen: list[Path] = []

    def success(output, criteria, sandbox) -> VerifyResult:
        seen.append(output.artifact_path)
        assert output.plan is not None  # the real output, with only the artifact swapped
        return VerifyResult(score=1.0, matched=["ok"], verifier_kind="output")

    sb = score_corpus(
        [_folder_output(row, fixture)], [_row()], {"success": success}, "success", tmp_path
    )
    [scored] = sb["rows"]
    assert scored["knaif_score"] == 1.0
    assert scored["verifier_kind"] == "folder"
    assert sorted(p.name for p in seen) == ["a_small.mp4", "b_small.mp4"]


def test_a_folder_row_left_wrong_scores_below_one(dirs, tmp_path):
    fixture, row = dirs
    _write(row, "a_small.mp4", "b_small.mp4", "a_converted.mp4")

    def success(output, criteria, sandbox) -> VerifyResult:
        return VerifyResult(score=1.0, verifier_kind="output")

    [scored] = score_corpus(
        [_folder_output(row, fixture)], [_row()], {"success": success}, "success", tmp_path
    )["rows"]
    assert scored["knaif_score"] < 1.0
    assert "gone:a_converted.mp4" in scored["knaif_failed"]


def test_a_plan_level_verifier_keeps_its_own_path_for_a_folder_row(dirs, tmp_path):
    fixture, row = dirs
    out = _folder_output(row, fixture)
    out.folder_dir = None  # a plan-level run keeps no folder

    def cheap(output, criteria, sandbox) -> VerifyResult:
        return VerifyResult(score=0.5, verifier_kind="plan")

    [scored] = score_corpus([out], [_row()], {"cheap": cheap}, "cheap", tmp_path)["rows"]
    assert scored["knaif_score"] == 0.5
    assert scored["verifier_kind"] == "plan"
