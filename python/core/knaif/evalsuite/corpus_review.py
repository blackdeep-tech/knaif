"""Applying an approved corpus review (plan 2026-10-14-faster-release-evidence, G2, T7).

The review removes rows and appends rows; kept rows are immutable (G2.1). `apply_removals`
takes the removals the owner approved (each naming the row kept in its place, as
`evalsuite corpus-audit` proposes them), checks that no test is lost, removes those lines and
leaves every other line byte for byte as it was. Beside the corpus it writes:

- `eval_removed.jsonl`: one entry per removed row — id, reason, the kept row, the date, the row
  itself and the corpus sha256 before — so a past run's row can still be read;
- `eval_kept.sha256`: `<sha256>  <id>` for every line kept, so `check_kept_rows` (a test over the
  real corpora) catches a kept row that was reworded, a removed row that came back, or a kept
  row that went missing. Appended rows are the one thing it allows to be new.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .corpus_audit import normalize

REMOVED = "eval_removed.jsonl"
KEPT = "eval_kept.sha256"


def _lines(corpus: Path) -> list[str]:
    return corpus.read_text(encoding="utf-8").splitlines()


def _id_of(line: str) -> str | None:
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    return str(json.loads(line)["id"])


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_removals(corpus: Path, removals: list[dict[str, Any]], *, date: str) -> None:
    """Remove the approved rows from *corpus*, recording each; refuse all if any would lose a
    test (unknown id, unknown kept row, a row kept in place of another, or an utterance of the
    removed row that the kept row does not have)."""
    corpus = Path(corpus)
    text = corpus.read_bytes().decode("utf-8")
    # Each line keeps its own terminator, so what is written back is byte for byte the input
    # minus the removed lines, whatever the checkout's line endings.
    lines = text.splitlines(keepends=True)
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for line in lines:
        rid = _id_of(line)
        if rid is not None:
            by_id[rid] = (line, json.loads(line))
    kept_for = {r["kept"] for r in removals}
    for r in removals:
        for key in ("id", "kept"):
            if r[key] not in by_id:
                raise ValueError(f"{r[key]} is not in the corpus")
        if r["id"] == r["kept"]:
            raise ValueError(f"{r['id']} cannot be its own replacement")
        if r["id"] in kept_for:
            raise ValueError(f"{r['id']} is kept in place of another row and cannot be removed")
        have = {normalize(u) for u in by_id[r["kept"]][1]["utterances"]}
        missing = [u for u in by_id[r["id"]][1]["utterances"] if normalize(u) not in have]
        if missing:
            raise ValueError(f"{r['id']} has utterances not in {r['kept']}: {missing}")

    gone = {r["id"] for r in removals}
    # LF line endings, so the value is the same on every checkout (as the gate hashes a corpus).
    before = _sha(text.replace("\r\n", "\n"))
    kept_lines = [line for line in lines if _id_of(line) not in gone]
    entries = [
        {
            "id": r["id"],
            "kept": r["kept"],
            "reason": r["reason"],
            "removed": date,
            "corpus_sha256_before": before,
            "row": by_id[r["id"]][1],
        }
        for r in removals
    ]
    corpus.write_bytes("".join(kept_lines).encode("utf-8"))
    with (corpus.parent / REMOVED).open("a", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    # A pin hashes the line without its terminator, as `check_kept_rows` reads it back.
    pins = [
        f"{_sha(line.rstrip(chr(13) + chr(10)))}  {_id_of(line)}"
        for line in kept_lines
        if _id_of(line) is not None
    ]
    (corpus.parent / KEPT).write_bytes("".join(p + "\n" for p in pins).encode("utf-8"))


def check_kept_rows(corpus: Path) -> list[str]:
    """Problems with *corpus* against its review record; empty when none, or when no review
    has happened yet."""
    corpus = Path(corpus)
    pins_path, removed_path = corpus.parent / KEPT, corpus.parent / REMOVED
    if not pins_path.exists() and not removed_path.exists():
        return []
    problems = []
    if not pins_path.exists() or not removed_path.exists():
        return [f"{corpus.parent}: {REMOVED} and {KEPT} must exist together"]
    current = {}
    for line in _lines(corpus):
        rid = _id_of(line)
        if rid is not None:
            current[rid] = _sha(line)
    for pin in _lines(pins_path):
        if not pin.strip():
            continue
        digest, rid = pin.split("  ", 1)
        if rid not in current:
            problems.append(f"kept row {rid} is missing from {corpus.name}")
        elif current[rid] != digest:
            problems.append(f"kept row {rid} changed (kept rows are immutable, G2.1)")
    for entry in _lines(removed_path):
        if entry.strip() and json.loads(entry)["id"] in current:
            problems.append(f"removed row {json.loads(entry)['id']} is back in {corpus.name}")
    return problems
