"""Folder rows: a request over a folder of files, graded on what the folder holds after the run.

A corpus row whose ``fixture`` ends in ``/`` names a fixture folder (``batch/``). The runner copies
that folder whole into the row's own folder, plans and executes the request there for real, and
:func:`grade_folder` scores what is left. A dry run cannot grade a folder: it would expand a glob
against the shared fixture folder, and a later step of a per-file chain reads what an earlier one
wrote. See ``CorpusRow`` for the row fields (``expect_files``, ``expect_gone``,
``expected_failures``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .corpus import CorpusRow
from .protocols import VerifyResult

#: The skill's verifier for one produced file: ``check(path, criteria) -> VerifyResult``.
FileCheck = Callable[[Path, dict[str, Any]], VerifyResult]


def _files(folder: Path) -> Iterator[Path]:
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            yield p


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def grade_folder(
    row: CorpusRow,
    row_dir: Path,
    fixture_dir: Path,
    check: FileCheck,
    failures: int | None = None,
) -> VerifyResult:
    """Score *row_dir* after the run against *row*'s folder expectations.

    Components, each 0..1, averaged: inputs untouched; one per ``expect_files`` entry (count, then
    the mean of its files' *check* scores); ``expect_gone`` matches nothing; nothing else is in the
    folder; and, when the row gives ``expected_failures``, the run's *failures* equal it.
    """
    matched: list[str] = []
    failed: list[str] = []
    parts: list[float] = []

    inputs = {_rel(p, fixture_dir): _digest(p) for p in _files(fixture_dir)}
    input_ok = True
    for name, digest in inputs.items():
        here = row_dir / name
        if not here.is_file():
            failed.append(f"input_missing:{name}")
            input_ok = False
        elif _digest(here) != digest:
            failed.append(f"input_changed:{name}")
            input_ok = False
    parts.append(1.0 if input_ok else 0.0)
    if input_ok:
        matched.append("inputs_untouched")

    produced = [p for p in _files(row_dir) if _rel(p, row_dir) not in inputs]
    claimed: set[Path] = set()
    for i, entry in enumerate(row.expect_files or []):
        hits = sorted(p for p in row_dir.glob(entry["name"]) if p in produced)
        claimed.update(hits)
        count = entry.get("count")
        if (count is not None and len(hits) != count) or not hits:
            want = count if count is not None else ">=1"
            failed.append(f"expect_files[{i}]:count {len(hits)} != {want}")
            parts.append(0.0)
            continue
        scores = []
        for p in hits:
            res = check(p, entry.get("criteria") or {})
            scores.append(res.score)
            failed.extend(f"{_rel(p, row_dir)}:{f}" for f in res.failed)
        parts.append(sum(scores) / len(scores))
        matched.append(f"expect_files[{i}]:{len(hits)}")

    gone_hits = sorted(
        {p for pattern in row.expect_gone or [] for p in row_dir.glob(pattern) if p in produced}
    )
    failed.extend(f"gone:{_rel(p, row_dir)}" for p in gone_hits)
    parts.append(0.0 if gone_hits else 1.0)

    unexpected = [p for p in produced if p not in claimed and p not in gone_hits]
    failed.extend(f"unexpected:{_rel(p, row_dir)}" for p in unexpected)
    parts.append(0.0 if unexpected else 1.0)

    if row.expected_failures is not None:
        if failures is None:
            failed.append("failures not reported")
            parts.append(0.0)
        elif failures != row.expected_failures:
            failed.append(f"failures {failures} != {row.expected_failures}")
            parts.append(0.0)
        else:
            matched.append(f"failures:{failures}")
            parts.append(1.0)

    return VerifyResult(
        score=sum(parts) / len(parts), matched=matched, failed=failed, verifier_kind="folder"
    )
