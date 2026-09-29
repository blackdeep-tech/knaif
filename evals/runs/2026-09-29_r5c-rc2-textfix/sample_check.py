"""RC2 text fix: draw the sample (`draw`) and compare a rebuilt binary's sample run with the accepted run
(`compare`). Written with the rules (run.sh header), before the runs.

    draw:    uv run python <this> draw <accepted board.json> <skill> <n>   -> JSON [[id, idx], ...]
    compare: uv run python <this> compare <accepted board.json> <sample board.json> <sample.json>

`compare` passes (exit 0) only if every sampled request is present in both boards and has the same
plan decision (tools and non-file arguments, `scripts/flip_rate.py`), the same outcome and the same
knaif score in both. Seed 20260929, requests that reach the model (a plan in the accepted board).
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


def _rows(path: str) -> dict[tuple[str, int], dict[str, Any]]:
    board = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(r["id"], int(r["utterance_idx"])): r for r in board["rows"]}


def _plan(row: dict[str, Any]) -> Any:
    plan = row.get("plan")
    return plan.get("plan") if isinstance(plan, dict) else None


def draw(board: str, skill: str, n: int) -> int:
    rows = _rows(board)
    eligible = sorted(k for k, r in rows.items() if _plan(r) is not None)
    sample = sorted(random.Random(f"{SEED}:rc2:{skill}").sample(eligible, n))
    print(json.dumps([list(k) for k in sample]))
    return 0


def compare(accepted: str, run: str, sample_path: str) -> int:
    a, b = _rows(accepted), _rows(run)
    sample = [(i, int(j)) for i, j in json.loads(Path(sample_path).read_text("utf-8"))]
    bad = []
    for key in sample:
        if key not in a or key not in b:
            bad.append((key, "missing"))
            continue
        pa, pb = _plan(a[key]), _plan(b[key])
        same_plan = pa is not None and pb is not None and fr.decision(pa) == fr.decision(pb)
        same_grade = (a[key].get("outcome_correct"), a[key].get("knaif_score")) == (
            b[key].get("outcome_correct"),
            b[key].get("knaif_score"),
        )
        if not (same_plan and same_grade):
            bad.append((key, f"plan same {same_plan}, grade same {same_grade}"))
    print(f"sample {len(sample)}: {len(sample) - len(bad)} identical, {len(bad)} different")
    for key, why in bad:
        print(f"  DIFF {key[0]}#{key[1]}: {why}")
    print("VERDICT: equivalent on the sample" if not bad else "VERDICT: NOT equivalent")
    return 0 if not bad else 1


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    sys.exit(draw(args[0], args[1], int(args[2])) if cmd == "draw" else compare(*args[:3]))
