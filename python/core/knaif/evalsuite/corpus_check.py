"""Checks on rows proposed for a corpus (plan 2026-10-14-faster-release-evidence, G2.2/G2.4).

Run before new rows are appended, so what lands is already known to parse, to carry the
review's tag, to add utterances the corpus does not have, and to copy nothing from
`train.jsonl` (the separation `test_train_data_integrity` enforces afterwards).
"""

from __future__ import annotations

import re
from typing import Any

from .corpus import CorpusRow


def _norm(text: str) -> str:
    """As `test_train_data_integrity._norm_utterance` compares utterances."""
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?"))


def check_new_rows(
    rows: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    train: list[dict[str, Any]],
    *,
    tag: str,
) -> list[str]:
    """Problems with *rows* as additions to *existing*; empty when they can be appended."""
    problems: list[str] = []
    ids = {str(r["id"]) for r in existing}
    seen = {_norm(u): str(r["id"]) for r in existing for u in r.get("utterances", [])}
    trained = {_norm(t["utterance"]) for t in train if t.get("utterance")}
    for row in rows:
        rid = str(row.get("id"))
        try:
            CorpusRow.from_dict(row)
        except (KeyError, ValueError, TypeError) as exc:
            problems.append(f"{rid}: schema: {exc}")
            continue
        if rid in ids:
            problems.append(f"{rid}: id already in the corpus")
        if tag not in (row.get("tags") or []):
            problems.append(f"{rid}: missing the review tag {tag!r}")
        tools = row.get("expected_tools")
        if tools and row.get("expected_tool") != tools[0]:
            problems.append(f"{rid}: expected_tool must be expected_tools[0] ({tools[0]})")
        for u in row.get("utterances", []):
            key = _norm(u)
            if key in seen and seen[key] in ids:
                problems.append(f"{rid}: {u!r} is already in the corpus ({seen[key]})")
            elif key in seen:
                problems.append(f"{rid}: {u!r} is also in proposed row {seen[key]}")
            elif key in trained:
                problems.append(f"{rid}: {u!r} copies a train.jsonl utterance")
            else:
                seen[key] = rid
        for u in row.get("utterances", []):
            if sum(_norm(v) == _norm(u) for v in row["utterances"]) > 1:
                problems.append(f"{rid}: {u!r} appears twice")
                break
    return problems
