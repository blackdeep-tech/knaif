"""Compare two L4 run folders cell by cell: aggregates, verdicts, safety, and row-level differences.

usage: python compare_runs.py <first run dir> <second run dir>
"""

import json
import re
import sys
from pathlib import Path

A, B = Path(sys.argv[1]), Path(sys.argv[2])
CELLS = [
    ("4b", "metal", "mac-4b"),
    ("1.7b", "metal", "mac-1.7b"),
    ("4b", "cpu", "mac-cpu-4b"),
    ("1.7b", "cpu", "mac-cpu-1.7b"),
]


def correct(r):
    s = r.get("knaif_score")
    return bool(r.get("outcome_correct")) and (s is None or s >= 1.0)


def plan_key(r):
    return json.dumps(r.get("plan"), sort_keys=True)


def verdict(d, skill):
    p = d / f"{skill}_accept.log"
    if not p.exists():
        return "-"
    t = p.read_text()
    m = re.search(r"^(NOT ACCEPTED|ACCEPTED)[^\n]*", t, re.M)
    return m.group(0) if m else "?"


def safety(d, skill):
    p = d / f"{skill}_safety.json"
    if not p.exists():
        return "-"
    s = json.loads(p.read_text())
    return f"{s['passed']}/{s['total']}"


for model, backend, lane in CELLS:
    for skill in ("ffmpeg", "documents"):
        da, db = A / model / backend, B / model / backend
        fa, fb = da / f"{skill}_{lane}_success.json", db / f"{skill}_{lane}_success.json"
        if not (fa.exists() and fb.exists()):
            print(f"## {model} {backend} {skill}: missing board ({fa.exists()=}, {fb.exists()=})")
            continue
        a, b = json.loads(fa.read_text()), json.loads(fb.read_text())
        ra = {(r["id"], r["utterance_idx"]): r for r in a["rows"]}
        rb = {(r["id"], r["utterance_idx"]): r for r in b["rows"]}
        shared = sorted(set(ra) & set(rb))
        plan_diff = [k for k in shared if plan_key(ra[k]) != plan_key(rb[k])]
        grade_diff = [k for k in shared if correct(ra[k]) != correct(rb[k])]
        print(f"## {model} {backend} {skill}")
        print(f"   rows        : {len(ra)} vs {len(rb)} (shared {len(shared)})")
        print(f"   placement   : {a.get('compute_backend')} vs {b.get('compute_backend')}")
        print(f"   binary      : {a.get('binary_sha256','')[:12]} vs {b.get('binary_sha256','')[:12]}")
        print(f"   outcome     : {a['outcome_accuracy']:.4f} vs {b['outcome_accuracy']:.4f}")
        print(f"   knaif score : {a['avg_knaif_score']:.4f} vs {b['avg_knaif_score']:.4f}")
        if backend == "metal":
            print(f"   verdict     : {verdict(da, skill)}  |  {verdict(db, skill)}")
            print(f"   safety      : {safety(da, skill)} vs {safety(db, skill)}")
        ta, tb = a.get("time_to_artifact_ms") or {}, b.get("time_to_artifact_ms") or {}
        print(f"   p50 latency : {ta.get('p50_ms', 0):.0f} ms vs {tb.get('p50_ms', 0):.0f} ms")
        print(f"   plans differ: {len(plan_diff)}   grades differ: {len(grade_diff)}")
        for k in grade_diff:
            print(f"      grade {k[0]}#{k[1]}: {correct(ra[k])} -> {correct(rb[k])}  "
                  f"({ra[k].get('actual_outcome')} -> {rb[k].get('actual_outcome')})")
        for k in [k for k in plan_diff if k not in grade_diff][:10]:
            print(f"      plan  {k[0]}#{k[1]} (grade unchanged: {correct(ra[k])})")
