"""The release subset: the rows an L4 release cell measures (plan 2026-10-14-faster-release-evidence,
D1, T2b).

Python acceptance answers "is this model good enough" on the full corpus, every paraphrase. A
release cell answers "does the shipped binary do what the accepted runtime did", and for that
coverage matters, not paraphrases: one utterance of the first row of every stratum (expected tool
x expected outcome x tag set), plus every row of the tags where a single representative hides too
much. The subset is derived, deterministic and committed beside the corpus with the sha256 of the
corpus it came from, so a corpus edit that is not followed by regenerating it fails a test instead
of quietly measuring the wrong rows.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .corpus import load_corpus

#: Tags whose every row is in the subset. Chains and edge cases fail in ways one representative
#: does not show; `safety` is the in-corpus adversarial slice; `hard2` is corpus v2's chain and
#: fan-out rows, which lost their chain tags only so slice floors would not count them (A1).
ALWAYS_TAGS = frozenset({"chain2", "chain3", "edge", "safety", "hard2"})

SUBSET_FILE = "release_subset.json"

METHOD = (
    "D1: in corpus order, the first row of each (expected_tool, expected_outcome, tag set) "
    "stratum, plus every row tagged " + "/".join(sorted(ALWAYS_TAGS)) + "; utterance 0 of each"
)


def corpus_path(skill: str, root: Path | str = ".") -> Path:
    return Path(root) / "skills" / skill / "data" / "eval.jsonl"


def subset_path(skill: str, root: Path | str = ".") -> Path:
    return Path(root) / "skills" / skill / "data" / SUBSET_FILE


def corpus_sha256(path: Path | str) -> str:
    """LF-normalised, as the gate hashes a corpus: the same value on a CRLF checkout and on CI."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def select_rows(rows: list[Any]) -> list[tuple[str, int]]:
    """`(id, utterance_idx)` of the subset, in corpus order. *rows* are corpus rows or dicts."""
    seen: set[tuple[Any, Any, frozenset[str]]] = set()
    picked: list[tuple[str, int]] = []
    for row in rows:
        tags = frozenset(_get(row, "tags") or [])
        stratum = (_get(row, "expected_tool"), _get(row, "expected_outcome"), tags)
        if stratum not in seen or tags & ALWAYS_TAGS:
            picked.append((str(_get(row, "id")), 0))
        seen.add(stratum)
    return picked


def build_subset(skill: str, root: Path | str = ".") -> dict[str, Any]:
    """The subset document for *skill*, derived from its corpus as it is on disk."""
    return derive_subset(skill, corpus_path(skill, root))


def derive_subset(skill: str, corpus: Path | str) -> dict[str, Any]:
    """The subset document for the corpus at *corpus*."""
    corpus = Path(corpus)
    rows = select_rows(load_corpus(corpus))
    return {
        "skill": skill,
        # The file name only: the subset travels into public runs (no local paths).
        "corpus": corpus.name,
        "corpus_sha256": corpus_sha256(corpus),
        "method": METHOD,
        "count": len(rows),
        "rows": [[rid, idx] for rid, idx in rows],
    }


def check_subset(skill: str, root: Path | str = ".") -> list[str]:
    """Problems with *skill*'s committed subset; empty when it matches the corpus."""
    return check_subset_beside(skill, corpus_path(skill, root))


def check_subset_beside(skill: str, corpus: Path | str) -> list[str]:
    """Problems with the subset committed beside *corpus*; empty when it matches it."""
    corpus = Path(corpus)
    path = corpus.parent / SUBSET_FILE
    if not path.exists():
        return [f"{skill}: no {SUBSET_FILE}; run `evalsuite release-subset --skill {skill}`"]
    committed = json.loads(path.read_text(encoding="utf-8"))
    derived = derive_subset(skill, corpus)
    stale = committed.get("corpus_sha256") != derived["corpus_sha256"]
    if stale or committed.get("rows") != derived["rows"]:
        return [
            f"{skill}: {SUBSET_FILE} was derived from another corpus; regenerate it with "
            f"`evalsuite release-subset --skill {skill}`"
        ]
    return []


def load_subset_beside(skill: str, corpus: Path | str) -> dict[str, Any]:
    """The subset committed beside *corpus*, refused when it no longer matches it."""
    problems = check_subset_beside(skill, corpus)
    if problems:
        raise ValueError(problems[0])
    return json.loads((Path(corpus).parent / SUBSET_FILE).read_text(encoding="utf-8"))


def subset_rows(doc: dict[str, Any]) -> list[tuple[str, int]]:
    """A subset document's rows as `(id, utterance_idx)` pairs."""
    return [(str(rid), int(idx)) for rid, idx in doc["rows"]]
