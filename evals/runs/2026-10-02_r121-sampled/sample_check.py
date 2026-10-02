"""1.2.1 sampled equivalence: draw the sample, check the CUDA payload, and compare a sample run with
the accepted 1.2.0 runs. Written with the rules (run.sh header), before any run.

    draw:     uv run python <this> draw <accepted board.json> <skill>          -> JSON [[id, idx], ...]
    payload:  uv run python <this> payload <dir> <platform>     files == the 1.2.1 backend manifest
    compare:  uv run python <this> compare <accepted board> <sample board> <sample.json>
    parity:   uv run python <this> parity <accepted L3 report> <sample report> <sample.json>

`compare` passes (exit 0) only if every sampled request is in both boards with the same plan
decision (tools and non-file arguments, `scripts/flip_rate.py`), the same outcome and the same
knaif score. `parity` passes only if every sampled id is in both L3 reports with the same status
and the same outcome kind and commands on BOTH runtimes. Seed 20261002.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import flip_rate as fr  # noqa: E402

SEED = 20261002
#: Random draw per skill, on top of the targeted rows below.
RANDOM_N = {"ffmpeg": 20, "documents": 10}


def _board(path: str) -> dict[tuple[str, int], dict[str, Any]]:
    board = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(r["id"], int(r["utterance_idx"])): r for r in board["rows"]}


def _plan(row: dict[str, Any]) -> Any:
    plan = row.get("plan")
    return plan.get("plan") if isinstance(plan, dict) else None


def _corpus(skill: str) -> list[dict[str, Any]]:
    path = ROOT / "skills" / skill / "data" / "eval.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _targeted(skill: str) -> set[tuple[str, int]]:
    """Requests on the paths 1.2.1 changed: every chain's first phrasing (B1 stop-on-decline, the
    run executor, the plain lines the lane reads); ffmpeg's `reverse_video` (B2); documents' every
    password phrasing (B5, grounded values)."""
    out: set[tuple[str, int]] = set()
    for rec in _corpus(skill):
        utts, tools = rec.get("utterances") or [], rec.get("expected_tools") or []
        if len(tools) > 1:
            out.add((rec["id"], 0))
        for i, u in enumerate(utts):
            if skill == "ffmpeg" and "reverse_video" in tools:
                out.add((rec["id"], i))
            if skill == "documents" and "password" in u.lower():
                out.add((rec["id"], i))
    return out


def draw(board: str, skill: str) -> int:
    rows = _board(board)
    eligible = sorted(k for k, r in rows.items() if _plan(r) is not None)
    drawn = set(random.Random(f"{SEED}:r121:{skill}").sample(eligible, RANDOM_N[skill]))
    # Targeted rows join only where the accepted run planned something: `compare` needs a plan on
    # both sides, and a request the model declined says nothing about the changed code.
    sample = sorted(drawn | (_targeted(skill) & set(eligible)))
    print(json.dumps([list(k) for k in sample]))
    return 0


def payload(directory: str, platform: str) -> int:
    manifest = yaml.safe_load(
        (ROOT / "contracts" / "backends" / "backend-manifest.yaml").read_text(encoding="utf-8")
    )
    files = manifest["backends"]["cuda"]["platforms"][platform]["files"]
    bad = 0
    for f in files:
        p = Path(directory) / f["name"]
        ok = p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == f["sha256"]
        print(("ok  " if ok else "BAD ") + f["name"])
        bad += not ok
    print(f"payload {platform}: {len(files) - bad}/{len(files)} match the manifest")
    return 1 if bad else 0


def compare(accepted: str, run: str, sample_path: str) -> int:
    a, b = _board(accepted), _board(run)
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
    return _verdict(len(sample), bad)


def _report(path: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in json.loads(Path(path).read_text(encoding="utf-8"))["rows"]}


def _side(row: dict[str, Any], side: str) -> tuple[Any, Any]:
    out = row.get(side) or {}
    return out.get("kind"), out.get("commands")


def parity(accepted: str, run: str, sample_path: str) -> int:
    a, b = _report(accepted), _report(run)
    ids = sorted({i for i, _ in json.loads(Path(sample_path).read_text("utf-8"))})
    bad = []
    for key in ids:
        if key not in a or key not in b:
            bad.append(((key, 0), "missing"))
            continue
        diffs = [
            name
            for name, x, y in (
                ("status", a[key].get("status"), b[key].get("status")),
                ("python", _side(a[key], "python"), _side(b[key], "python")),
                ("native", _side(a[key], "native"), _side(b[key], "native")),
            )
            if x != y
        ]
        if diffs:
            bad.append(((key, 0), "differs in " + ", ".join(diffs)))
    return _verdict(len(ids), bad)


def _verdict(n: int, bad: list[tuple[tuple[str, int], str]]) -> int:
    print(f"sample {n}: {n - len(bad)} identical, {len(bad)} different")
    for key, why in bad:
        print(f"  DIFF {key[0]}#{key[1]}: {why}")
    print("VERDICT: equivalent on the sample" if not bad else "VERDICT: NOT equivalent")
    return 0 if not bad else 1


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    handlers = {"draw": draw, "payload": payload, "compare": compare, "parity": parity}
    sys.exit(handlers[cmd](*args))
