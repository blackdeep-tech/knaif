"""T9a verdict: do the 2026-09-25 CPU plans stand for the release binary on the CPU?

Compares the sampled CPU board (`evalsuite native --only`, one process per utterance, GPU hidden)
with the 2026-09-25 CPU plans (`flip_rate.py native`, plan mode) at the *decision* level (tools and
non-file arguments; `scripts/flip_rate.py`). Written with the rules, before the run.

A sampled row the binary rejected before inference (no plan recorded, outcome `reject`: the
skill's unsafe-phrase gate in `run`, which plan mode never applies) has no model decision to
compare. The sample was drawn without such rows (they are known from the CUDA board); one that
appears anyway is set aside and reported, never counted as agreement. Any other row without a
plan (an error before a plan was dumped) counts as a flip.

    uv run python <this> <sample board.json> <2026-09-25 cpu .jsonl> <sample .json>

Exit 0: 0 decision flips over the whole sample -> the plans stand. Exit 1: any flip, or a sampled
row missing from the board -> the full CPU L4 instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import flip_rate as fr  # noqa: E402


def main(board_path: str, plans_path: str, sample_path: str) -> int:
    board = json.loads(Path(board_path).read_text(encoding="utf-8"))
    rows = {(r["id"], int(r["utterance_idx"])): r for r in board["rows"]}
    old = fr.load_run(Path(plans_path))
    sample = [(rid, int(idx)) for rid, idx in json.loads(Path(sample_path).read_text("utf-8"))]

    missing = [k for k in sample if k not in rows]
    gated, flips = [], []
    for key in sample:
        row = rows.get(key)
        if row is None:
            continue
        plan = (row.get("plan") or {}).get("plan") if isinstance(row.get("plan"), dict) else None
        if plan is None and row.get("actual_outcome") == "reject":
            gated.append(key)
        elif plan is None or fr.decision(plan) != fr.decision(old[key].plan):
            flips.append(key)

    compared = len(sample) - len(missing) - len(gated)
    print(f"sample {len(sample)}: compared {compared}, decision flips {len(flips)}")
    print(f"  missing from the board: {missing}")
    print(f"  rejected before inference (set aside): {gated}")
    for key in flips:
        print(f"  FLIP {key[0]}#{key[1]}")
    ok = not flips and not missing
    print("VERDICT: the 2026-09-25 CPU plans stand" if ok else "VERDICT: full CPU L4 instead")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
