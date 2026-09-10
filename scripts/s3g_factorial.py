#!/usr/bin/env python3
"""S3g: the prompt factorial — example selection x retrieval `top_k`, both skills.

Two questions this settles, together rather than one after the other:

* **V2 — does `select_examples` help or hurt?** The 2026-09-09 factorial favoured Rust's
  static block, but it graded first-tool choice and step count, explicitly *not* argument
  correctness or executed artifacts, and its one piece of counter-evidence (the chain
  slice) was discounted partly because *native* cannot execute chains. A limitation of the
  port must never set Python's target behavior, so that measurement licenses an experiment
  and nothing more.
* **V1 — does `top_k=5` still suit a larger skill?** ffmpeg shows 5 of 13 public tools,
  documents 5 of 15. Only documents can say whether the ratio starts to hurt.

Running them as one factorial is the point: the "don't change two things at once" rule
asks that factors be *separable*, which is exactly what a factorial does by construction —
and the harness, model load and GPU time are paid once instead of twice.

Every cell is graded with an **executing** verifier on real artifacts, reported **per
required slice** from the skill's `acceptance.yaml`, and paired against the shipped
configuration row by row (McNemar's exact test), because two independent percentages over
the same corpus throw away the pairing that makes a small difference detectable.

    uv run python scripts/s3g_factorial.py --out evals/runs/2026-09-10_s3g
    uv run python scripts/s3g_factorial.py --out <dir> --analyze-only   # re-report

Accepting a winner is a separate, deliberate act: re-lock the snapshot in its own commit
(S5), with the new `prompt_config` recorded in it.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python" / "core"))

DEFAULT_BACKEND = "qwen3-4b-sft-v3-flat-q4"
DEFAULT_SKILLS = ("ffmpeg", "documents")

#: The factor levels. `selected` x `top_k=5` is the shipped configuration and the
#: baseline every other cell is paired against.
EXAMPLES_LEVELS = ("selected", "static")
TOP_K_LEVELS = (5, 8, 99)  # 99 = every public tool; measures ordering without filtering

BASELINE_CELL = ("selected", 5)


@dataclass(frozen=True)
class Cell:
    skill: str
    examples: str
    top_k: int

    @property
    def name(self) -> str:
        return f"{self.skill}_{self.examples}_k{self.top_k}"

    @property
    def is_baseline(self) -> bool:
        return (self.examples, self.top_k) == BASELINE_CELL


def cells(skills: list[str]) -> list[Cell]:
    return [
        Cell(skill, examples, k)
        for skill in skills
        for examples in EXAMPLES_LEVELS
        for k in TOP_K_LEVELS
    ]


# -- pairing and statistics (pure) --------------------------------------------


def row_key(row: dict[str, Any]) -> tuple[str, int]:
    """A per-utterance key.

    `id` alone is NOT one: a corpus row expands into several utterances, so joining two
    runs on id silently keeps one utterance per row and drops the rest.
    """
    return (row["id"], int(row.get("utterance_idx") or 0))


def row_success(row: dict[str, Any]) -> bool:
    """Did this utterance end in the right outcome *and* a fully correct artifact?

    A row that routed correctly and then produced a wrong file is not a success. Rows with
    no artifact to grade (a correct clarify/reject) count on their outcome alone.
    """
    if not row.get("outcome_correct"):
        return False
    score = row.get("knaif_score")
    return True if score is None else float(score) >= 0.999


def paired(a_rows: list[dict], b_rows: list[dict]) -> tuple[int, int, int]:
    """Return (a_only, b_only, n_paired): wins for A, wins for B, and rows compared."""
    a = {row_key(r): row_success(r) for r in a_rows}
    b = {row_key(r): row_success(r) for r in b_rows}
    shared = a.keys() & b.keys()
    a_only = sum(1 for k in shared if a[k] and not b[k])
    b_only = sum(1 for k in shared if b[k] and not a[k])
    return a_only, b_only, len(shared)


def mcnemar_exact(a_only: int, b_only: int) -> float:
    """Two-sided exact binomial p for the discordant pairs.

    Under the null the two configurations are equally likely to win a pair, so the
    discordant count is Binomial(n, 0.5). Exact rather than chi-square because the
    discordant counts here are small — often single digits — where the approximation is
    not trustworthy.
    """
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


# -- reporting ----------------------------------------------------------------


@dataclass
class CellResult:
    cell: Cell
    board: dict[str, Any]
    safety: dict[str, Any] | None = None
    accepted: bool = False
    violations: list[str] = field(default_factory=list)


def grade_against_the_bar(result: CellResult) -> None:
    """Fill in whether this cell clears the skill's written S2 bar."""
    from knaif.evalsuite.acceptance import check_acceptance, load_acceptance

    spec = load_acceptance(result.cell.skill, root=REPO_ROOT / "skills")
    report = check_acceptance(spec, result.board, safety=result.safety)
    result.accepted = report.ok
    result.violations = [v.message for v in report.violations]


def slice_table(spec: dict[str, Any], board: dict[str, Any]) -> dict[str, float | None]:
    """Per required slice, this cell's outcome accuracy."""
    by_tag = board.get("by_tag") or {}
    return {tag: (by_tag.get(tag) or {}).get("outcome_accuracy") for tag in spec.get("slices", {})}


def analyse(results: list[CellResult]) -> dict[str, Any]:
    from knaif.evalsuite.acceptance import load_acceptance

    out: dict[str, Any] = {"skills": {}}
    by_skill: dict[str, list[CellResult]] = {}
    for r in results:
        by_skill.setdefault(r.cell.skill, []).append(r)

    for skill, rs in by_skill.items():
        spec = load_acceptance(skill, root=REPO_ROOT / "skills")
        base = next((r for r in rs if r.cell.is_baseline), None)
        cells_out = []
        for r in rs:
            entry: dict[str, Any] = {
                "cell": r.cell.name,
                "examples": r.cell.examples,
                "top_k": r.cell.top_k,
                "is_baseline": r.cell.is_baseline,
                "total": r.board.get("total"),
                "outcome_accuracy": r.board.get("outcome_accuracy"),
                "avg_knaif_score": r.board.get("avg_knaif_score"),
                "coverage": r.board.get("coverage"),
                "safety_pass_rate": (r.safety or {}).get("pass_rate"),
                "clears_s2_bar": r.accepted,
                "violations": r.violations,
                "slices": slice_table(spec, r.board),
            }
            if base is not None and r is not base:
                wins, losses, n = paired(r.board.get("rows") or [], base.board.get("rows") or [])
                entry["vs_baseline"] = {
                    "cell_wins": wins,
                    "baseline_wins": losses,
                    "paired_rows": n,
                    "p_value": round(mcnemar_exact(wins, losses), 4),
                }
            cells_out.append(entry)
        out["skills"][skill] = {"baseline": base.cell.name if base else None, "cells": cells_out}
    return out


def render(summary: dict[str, Any]) -> str:
    lines = ["# S3g — prompt factorial (examples x top_k)", ""]
    for skill, data in summary["skills"].items():
        lines += [f"## {skill}", "", f"Baseline (shipped): `{data['baseline']}`", ""]
        lines += [
            "| cell | examples | top_k | outcome | knaif | coverage | safety | S2 | "
            "vs baseline (w/l, p) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for c in data["cells"]:
            vs = c.get("vs_baseline")
            vs_txt = (
                f"{vs['cell_wins']}/{vs['baseline_wins']}, p={vs['p_value']}" if vs else "baseline"
            )
            lines.append(
                f"| `{c['cell']}` | {c['examples']} | {c['top_k']} | "
                f"{_pct(c['outcome_accuracy'])} | {_pct(c['avg_knaif_score'])} | "
                f"{_pct(c['coverage'])} | {_pct(c['safety_pass_rate'])} | "
                f"{'PASS' if c['clears_s2_bar'] else 'fail'} | {vs_txt} |"
            )
        lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


# -- running ------------------------------------------------------------------


def run_cell(cell: Cell, out_dir: Path, backend: str, limit: int | None) -> None:
    cell_dir = out_dir / cell.name
    cmd = [
        sys.executable,
        "-m",
        "knaif.evalsuite",
        "run",
        "--skill",
        cell.skill,
        "--verifier",
        "success",
        "--config",
        "eval_backends.yaml",
        "--backends",
        backend,
        "--examples",
        cell.examples,
        "--top-k",
        str(cell.top_k),
        "--save",
        str(cell_dir),
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    print(f"\n=== {cell.name} ===", flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)

    safety_cmd = [
        sys.executable,
        "-m",
        "knaif.evalsuite",
        "safety",
        "--skill",
        cell.skill,
        "--config",
        "eval_backends.yaml",
        "--backends",
        backend,
        "--examples",
        cell.examples,
        "--top-k",
        str(cell.top_k),
        "--save",
        str(cell_dir / "safety.json"),
    ]
    # A safety miss is a finding about this cell, not a reason to abandon the factorial.
    subprocess.run(safety_cmd, cwd=REPO_ROOT, check=False)


def load_cell(cell: Cell, out_dir: Path, backend: str) -> CellResult | None:
    cell_dir = out_dir / cell.name
    board_path = cell_dir / f"{cell.skill}_{backend}_success.json"
    if not board_path.exists():
        return None
    board = json.loads(board_path.read_text(encoding="utf-8"))
    safety_path = cell_dir / "safety.json"
    safety = json.loads(safety_path.read_text(encoding="utf-8")) if safety_path.exists() else None
    return CellResult(cell=cell, board=board, safety=safety)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path, help="Run directory")
    ap.add_argument("--skills", default=",".join(DEFAULT_SKILLS))
    ap.add_argument("--backend", default=DEFAULT_BACKEND)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--analyze-only", action="store_true", dest="analyze_only")
    args = ap.parse_args()

    skills = [s.strip() for s in args.skills.split(",") if s.strip()]
    out_dir = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = cells(skills)
    if not args.analyze_only:
        for cell in plan:
            run_cell(cell, out_dir, args.backend, args.limit)

    results = [r for r in (load_cell(c, out_dir, args.backend) for c in plan) if r is not None]
    if not results:
        sys.exit(f"No cell scoreboards found under {out_dir}")
    for r in results:
        grade_against_the_bar(r)

    summary = analyse(results)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    text = render(summary)
    (out_dir / "summary.md").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    print(f"\nsummary -> {out_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
