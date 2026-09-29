"""T11: cross-backend flips between two L4 boards, each flip set split by the `success` grades.

Written with the rules, before T11 runs. For the CUDA board and another backend's board of the
same model and skill, it lists the decision flips and the canonical flips (`scripts/flip_rate.py`
levels) and splits each set into:

* both-correct  — the plans differ and both still produced a graded-correct result;
* one-side-correct (cuda / other) — the flip decided the outcome for a user;
* both-wrong    — the plans differ and neither was right.

A row is *correct* when its outcome is the expected one and, where the verifier graded an
artifact (`knaif_score` not null), that score is 1.0 — the `success` grade, not the outcome alone
(`flip_rate.py compare` reads `outcome_correct` only; Codex audit of e6fea79).

    uv run python <this> <cuda board.json> <other board.json> [--label cuda/cpu]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import flip_rate as fr  # noqa: E402


def correct(row: dict[str, Any]) -> bool:
    score = row.get("knaif_score")
    return bool(row.get("outcome_correct")) and (score is None or score >= 1.0)


def _rows(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {(r["id"], int(r["utterance_idx"])): r for r in doc["rows"]}


def _plan(row: dict[str, Any]) -> list[dict[str, Any]]:
    plan = row.get("plan")
    return list((plan.get("plan") if isinstance(plan, dict) else plan) or [])


def split(a: dict, b: dict, keys: list) -> dict[str, list]:
    out: dict[str, list] = {"both_correct": [], "only_a": [], "only_b": [], "both_wrong": []}
    for key in keys:
        ca, cb = correct(a[key]), correct(b[key])
        bucket = "both_correct" if ca and cb else "only_a" if ca else "only_b" if cb else "both_wrong"
        out[bucket].append(f"{key[0]}#{key[1]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--label", default="a/b")
    args = ap.parse_args()
    a, b = _rows(Path(args.a)), _rows(Path(args.b))
    shared = sorted(set(a) & set(b))
    name_a, name_b = (args.label.split("/") + ["b"])[:2]
    report: dict[str, Any] = {"label": args.label, "shared": len(shared)}
    print(f"{args.label}: {len(shared)} shared rows (in one board only: {len(set(a) ^ set(b))})")
    for level, fn in (("decision", fr.decision), ("canonical", fr.canonical)):
        flips = [k for k in shared if fn(_plan(a[k])) != fn(_plan(b[k]))]
        parts = split(a, b, flips)
        report[level] = {"flips": len(flips), **parts}
        print(
            f"  {level:<9} flips {len(flips):>3}: both-correct {len(parts['both_correct'])}, "
            f"only {name_a} correct {len(parts['only_a'])}, only {name_b} correct "
            f"{len(parts['only_b'])}, both-wrong {len(parts['both_wrong'])}"
        )
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
