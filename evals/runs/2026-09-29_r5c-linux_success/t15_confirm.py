"""T15: draw the Linux CPU samples (`draw`), then judge them (`confirm`). Written with the rules.

The Linux CPU cell is taken by reuse (plan T15): the Windows CPU cell stands for Linux iff the
Linux binary, on the CPU, plans a pre-drawn sample of 150 requests per model exactly as the
Windows CPU plans did, at the *decision* level (tools and non-file arguments;
`scripts/flip_rate.py`). References: the 4B's 2026-09-25 CPU plans (`*_cpu.jsonl`; T9a confirmed
them for the release binary), the 1.7B's T10 board (its plans are the Windows release binary's).

    draw:    uv run python <this> draw <model> <skill> <n> <windows cpu board.json> [<plans .jsonl>]
             -> prints the sample (a JSON list of [id, utterance_idx]), seed 20260929
    confirm: uv run python <this> confirm <linux sample board.json> <reference> <sample.json>
             reference: a `*_cpu.jsonl` (flip_rate format) or a board .json with per-row plans

Eligible for the sample: requests that reach the model on Windows (a plan in the Windows CPU
board; pre-inference rejects, which plan mode never sees, are excluded) and, for the 4B, that
have a 2026-09-25 plan. `confirm` sets aside a sampled row the Linux binary rejects before
inference (reported, never counted as agreement); any other row without a plan is a flip.
Exit 0: 0 decision flips and nothing missing -> the Windows CPU cell stands for Linux. Exit 1:
the full Linux CPU L4 for that model instead, as a separate approved run.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import flip_rate as fr  # noqa: E402

SEED = 20260929
Key = tuple[str, int]


def _board_plans(path: Path) -> dict[Key, list[dict[str, Any]] | None]:
    board = json.loads(path.read_text(encoding="utf-8"))
    out: dict[Key, list[dict[str, Any]] | None] = {}
    for row in board["rows"]:
        plan = row.get("plan")
        out[(row["id"], int(row["utterance_idx"]))] = (
            plan.get("plan") if isinstance(plan, dict) else None
        )
    return out


def _reference(path: Path) -> dict[Key, Any]:
    if path.suffix == ".jsonl":
        return {key: run.plan for key, run in fr.load_run(path).items()}
    return _board_plans(path)


def draw(model: str, skill: str, n: int, board: str, plans: str | None = None) -> int:
    windows = _board_plans(Path(board))
    eligible = {k for k, plan in windows.items() if plan is not None}
    if plans:
        old = _reference(Path(plans))
        eligible &= {k for k, plan in old.items() if plan is not None}
    # One stream per model and skill, so drawing one sample never shifts another.
    rng = random.Random(f"{SEED}:{model}:{skill}")
    sample = sorted(rng.sample(sorted(eligible), n))
    print(json.dumps([list(k) for k in sample]))
    return 0


def confirm(board_path: str, reference_path: str, sample_path: str) -> int:
    board = json.loads(Path(board_path).read_text(encoding="utf-8"))
    rows = {(r["id"], int(r["utterance_idx"])): r for r in board["rows"]}
    ref = _reference(Path(reference_path))
    sample = [(rid, int(idx)) for rid, idx in json.loads(Path(sample_path).read_text("utf-8"))]

    missing = [k for k in sample if k not in rows or ref.get(k) is None]
    gated, flips = [], []
    for key in sample:
        row = rows.get(key)
        if row is None or ref.get(key) is None:
            continue
        plan = (row.get("plan") or {}).get("plan") if isinstance(row.get("plan"), dict) else None
        if plan is None and row.get("actual_outcome") == "reject":
            gated.append(key)
        elif plan is None or fr.decision(plan) != fr.decision(ref[key]):
            flips.append(key)

    compared = len(sample) - len(missing) - len(gated)
    print(f"sample {len(sample)}: compared {compared}, decision flips {len(flips)}")
    print(f"  missing (board or reference): {missing}")
    print(f"  rejected before inference (set aside): {gated}")
    for key in flips:
        print(f"  FLIP {key[0]}#{key[1]}")
    ok = not flips and not missing
    print("VERDICT: the Windows CPU cell stands for Linux" if ok else "VERDICT: full Linux CPU L4")
    return 0 if ok else 1


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "draw":
        sys.exit(draw(args[0], args[1], int(args[2]), *args[3:5]))
    sys.exit(confirm(*args[:3]))
