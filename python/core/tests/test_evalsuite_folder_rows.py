"""A row can describe a request over a folder of files, graded on what the folder holds after.

- `fixture` ending in `/` names a fixture FOLDER (`batch/`), copied whole into the row's folder.
- `expect_files`: each entry is `{"name": <glob>, "count": <n, optional>, "criteria": {...}}`;
  after the run, the folder holds files matching each `name` (exactly `count` of them when given,
  else at least one), each passing `criteria`.
- `expect_gone`: name globs that must match nothing after the run (intermediates the user did
  not ask to keep).
- `expected_failures`: how many files of the batch are expected to fail (a broken input).
- They are optional. A row without them serializes exactly as before, so no committed corpus or
  baseline moves. They only make sense on a `plan` row whose fixture is a folder.
"""

from __future__ import annotations

import pytest

from knaif.evalsuite.corpus import CorpusRow


def _row(**kw) -> CorpusRow:
    base: dict = {
        "id": "ffmpeg_batch_001",
        "utterances": ["convert all the .mov files here to mp4 and make them smaller"],
        "expected_outcome": "plan",
        "fixture": "batch/",
        "expected_tools": ["convert_video", "compress_video"],
    }
    base.update(kw)
    return CorpusRow(**base)


KEEP = [{"name": "*.mp4", "count": 2, "criteria": {"container": "mp4"}}]


def test_a_folder_fixture_is_a_fixture_ending_in_a_slash():
    assert _row().is_folder_row
    assert not _row(fixture="clip.mp4").is_folder_row
    assert not _row(fixture=None).is_folder_row


def test_row_accepts_kept_and_gone_files_and_failures():
    row = _row(expect_files=KEEP, expect_gone=["*_converted.mp4"], expected_failures=1)
    assert row.expect_files == KEEP
    assert row.expect_gone == ["*_converted.mp4"]
    assert row.expected_failures == 1


def test_they_round_trip():
    row = _row(expect_files=KEEP, expect_gone=["*_converted.mp4"], expected_failures=1)
    again = CorpusRow.from_dict(row.to_dict())
    assert (again.expect_files, again.expect_gone, again.expected_failures) == (
        KEEP,
        ["*_converted.mp4"],
        1,
    )


def test_a_row_without_them_serializes_as_before():
    d = _row(fixture="clip.mp4").to_dict()
    assert "expect_files" not in d
    assert "expect_gone" not in d
    assert "expected_failures" not in d


@pytest.mark.parametrize(
    "kw",
    [
        {"expect_files": KEEP, "fixture": "clip.mp4"},  # not a folder
        {"expect_gone": ["*.tmp"], "fixture": "clip.mp4"},
        {"expect_files": KEEP, "expected_outcome": "clarify"},  # files only for a plan
        {"expect_files": [{"count": 1}]},  # an entry needs a name
        {"expect_files": [{"name": "*.mp4", "count": 0}]},  # count must be positive
        {"expect_gone": ["", "*.tmp"]},  # an empty pattern
        {"expected_failures": -1},
        {"expected_failures": True},  # not a count
    ],
)
def test_row_refuses_inconsistent_folder_fields(kw):
    with pytest.raises(ValueError):
        _row(**kw)
