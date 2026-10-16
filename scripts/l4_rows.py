"""Per-row extracts of L4 boards, and the cross-platform comparison that reads them.

D14/D17 of the 2026-08-02 macOS support plan: the Mac's L4 rows are compared row by row against
Windows and Linux, through a committed, compact extract of the 1.2.0 L4 boards — the boards
themselves are large, carry user utterances and local paths, and are not committed.

An extract keeps, per row, only what a comparison needs: the row id, the utterance index, whether
the row was graded correct (the outcome AND, where an artifact was graded, a full score — the same
rule as 1.2.0's `t11_flips.py`), and short hashes of the plan's decision and canonical fingerprints
(`scripts/flip_rate.py`: tools and non-file arguments; canonical also folds spellings the engine
renders identically). No utterance, no plan text, no path.

    uv run python scripts/l4_rows.py extract <board.json> --label windows/4b/cuda/ffmpeg \\
        --source evals/runs/<run>/<rel path> -o evals/parity/1.2.0-l4-rows/<name>.json
    uv run python scripts/l4_rows.py compare <extract.json> <other board.json> [--list]

`compare` prints the decision and canonical flips between the reference extract and another board
(extracted on the fly), each split by who was right, and the rows whose grade differs. A flip is
not automatically a port bug: triage it against C5's three confounds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flip_rate as fr  # noqa: E402

Key = tuple[str, int]


def correct(row: dict[str, Any]) -> bool:
    score = row.get("knaif_score")
    return bool(row.get("outcome_correct")) and (score is None or score >= 1.0)


def _plan(row: dict[str, Any]) -> list[dict[str, Any]]:
    plan = row.get("plan")
    return list((plan.get("plan") if isinstance(plan, dict) else plan) or [])


def _hash(fingerprint: tuple) -> str:
    return hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()[:12]


def fingerprints(plan: list[dict[str, Any]]) -> tuple[str, str]:
    """Short hashes of the plan's decision and canonical fingerprints."""
    return _hash(fr.decision(plan)), _hash(fr.canonical(plan))


def _board_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def extract(board: Path, *, label: str, source: str) -> dict[str, Any]:
    out_rows = []
    for row in sorted(_board_rows(board), key=lambda r: (r["id"], int(r["utterance_idx"]))):
        decision, canonical = fingerprints(_plan(row))
        out_rows.append(
            [row["id"], int(row["utterance_idx"]), int(correct(row)), decision, canonical]
        )
    return {
        "label": label,
        "source": source,
        "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
        "columns": ["id", "utterance_idx", "correct", "decision", "canonical"],
        "rows": out_rows,
    }


def _index(doc: dict[str, Any]) -> dict[Key, list]:
    return {(r[0], int(r[1])): r for r in doc["rows"]}


def compare(reference: dict[str, Any], other_board: Path) -> dict[str, Any]:
    other_doc = extract(other_board, label="other", source=str(other_board.name))
    ref, other = _index(reference), _index(other_doc)
    shared = sorted(set(ref) & set(other))
    report: dict[str, Any] = {
        "reference": reference["label"],
        "shared": len(shared),
        "only_one_side": len(set(ref) ^ set(other)),
    }
    for level, col in (("decision", 3), ("canonical", 4)):
        flips = [k for k in shared if ref[k][col] != other[k][col]]
        split: dict[str, list[str]] = {
            "both_correct": [],
            "only_reference": [],
            "only_other": [],
            "both_wrong": [],
        }
        for key in flips:
            a, b = bool(ref[key][2]), bool(other[key][2])
            bucket = (
                "both_correct"
                if a and b
                else "only_reference" if a else "only_other" if b else "both_wrong"
            )
            split[bucket].append(f"{key[0]}#{key[1]}")
        report[level] = {"flips": len(flips), **split}
    report["grades_differ"] = [f"{k[0]}#{k[1]}" for k in shared if ref[k][2] != other[k][2]]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="write the compact per-row extract of one board")
    ex.add_argument("board", type=Path)
    ex.add_argument("--label", required=True, help="platform/model/backend/skill")
    ex.add_argument(
        "--source", required=True, help="the board's repo-relative path, for the record"
    )
    ex.add_argument("-o", "--out", type=Path, required=True)
    cmp_ = sub.add_parser("compare", help="flips between a reference extract and another board")
    cmp_.add_argument("reference", type=Path)
    cmp_.add_argument("board", type=Path)
    cmp_.add_argument("--list", action="store_true", help="also list the flipped rows")
    args = parser.parse_args(argv)

    if args.command == "extract":
        doc = extract(args.board, label=args.label, source=args.source)
        lines = [json.dumps({k: v for k, v in doc.items() if k != "rows"})[:-1] + ', "rows": [']
        lines += [f"  {json.dumps(r)}," for r in doc["rows"]]
        lines[-1] = lines[-1].rstrip(",")
        lines.append("]}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"{args.out.name}: {len(doc['rows'])} rows")
        return 0

    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    report = compare(reference, args.board)
    if not args.list:
        for level in ("decision", "canonical"):
            report[level] = {
                k: (len(v) if isinstance(v, list) else v) for k, v in report[level].items()
            }
        report["grades_differ"] = len(report["grades_differ"])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
