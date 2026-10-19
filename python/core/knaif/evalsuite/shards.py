"""Pre-drawn shards of one L4 cell, so its rows can run on several CI machines at once.

A shard is a `native --only` file: a list of `(id, utterance_idx)` pairs. The draw is a pure
function of the corpus and the two numbers, so it can be written into a run before any result
(the pre-registration rule) and redrawn anywhere to check it.

Two choices, both for the jobs rather than for the score:

- `rows` picks utterances **evenly spaced in corpus order**, so a smaller sample still reaches
  every part of a corpus that is grouped by tool.
- the picks are dealt **round-robin**, so every shard holds a similar mix of light and heavy rows
  and the jobs finish at similar times.
"""

from __future__ import annotations

from collections.abc import Sequence

from .corpus import CorpusRow

Pair = tuple[str, int]


def draw_shards(
    corpus: Sequence[CorpusRow],
    *,
    count: int,
    rows: int | None = None,
    within: Sequence[Pair] | None = None,
) -> list[list[Pair]]:
    """Split the corpus (or `rows` utterances of it) into `count` shards, deterministically.

    *within* limits the draw to those utterances, kept in corpus order: the release subset, for
    a subset cell's shards and its cold-start sample (plan 2026-10-14-faster-release-evidence,
    Part I, I5).
    """
    pairs: list[Pair] = [(r.id, j) for r in corpus for j in range(len(r.utterances))]
    if within is not None:
        keep = set(within)
        pairs = [p for p in pairs if p in keep]
    if rows is not None:
        if rows < 1:
            raise ValueError(f"rows must be at least 1, got {rows}")
        if rows < len(pairs):
            total = len(pairs)
            pairs = [pairs[i * total // rows] for i in range(rows)]
    if count < 1:
        raise ValueError(f"count must be at least 1, got {count}")
    if count > len(pairs):
        raise ValueError(f"{count} shards for {len(pairs)} utterances would leave a shard empty")
    return [pairs[k::count] for k in range(count)]
