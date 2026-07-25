"""Read-only descriptor / mixed-intent analyzer.

Classifies each emitted plan input as exact / glob / chain / descriptor, resolves
descriptors against an *available-files* set, and bins (expected, actual,
resolution) into PRIZE / RISK / SAFE / POLICY-CONFLICT — computed under two
worlds:

* **injection OFF** — available files = filenames written in the standalone line
  only (today's harness; no prepend step exists).
* **injection ON**  — available files also include the row ``fixture`` (the proxy
  for a listing an ``ls``-type prepend step would inject).

This module decides nothing and mutates nothing. It reads per-arm ``*_success``
scoreboards plus the corpus and writes ``descriptor_analysis.{md,json}``.

See ``docs/plans/2026-06-09-descriptor-mixed-intent-analyzer.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from knaif.input_refs import (
    classify_token,
    parse_inline_filenames,
    resolve_input,
)

_PATH_KEYS = ("inputs", "input", "files")


# ── input extraction & available-files ───────────────────────────────────────


def extract_input_tokens(plan: dict[str, Any] | None) -> list[str]:
    """Pull path-bearing arg values from a plan's first step, flattened to strings."""
    if not plan:
        return []
    steps = plan.get("plan") or []
    if not steps:
        return []
    args = steps[0].get("args") or {}
    tokens: list[str] = []
    for key in _PATH_KEYS:
        if key not in args:
            continue
        val = args[key]
        if isinstance(val, str):
            tokens.append(val)
        elif isinstance(val, list):
            tokens.extend(str(v) for v in val)
    return tokens


def available_files(fixture: str | None, utterance: str, *, injection: bool) -> set[str]:
    """Available-files set for resolution.

    OFF: inline filenames only. ON: also the row fixture (injected-listing proxy).
    """
    avail = parse_inline_filenames(utterance)
    if injection and fixture:
        avail = avail | {fixture}
    return avail


# ── binning ──────────────────────────────────────────────────────────────────


def bin_row(expected: str, actual: str, resolution_label: str) -> str:
    """Bin a descriptor (NL) row by (expected_outcome, actual_outcome, resolution)."""
    if expected == "clarify" and actual == "plan":
        return "POLICY-CONFLICT" if resolution_label == "resolves_unique" else "PRIZE"
    if expected == "plan" and actual == "plan":
        return "SAFE" if resolution_label == "resolves_unique" else "RISK"
    if expected == "plan" and actual == "clarify":
        return "PLAN_MISS"
    if expected == "clarify" and actual == "clarify":
        return "CLARIFY_OK"
    return "OTHER"


def stem_resolves(token: str, fixture: str | None) -> bool:
    """True if the fixture's stem matches the emitted stem token (case-insensitive).

    ``clip_4k`` vs ``clip_4k.mp4`` → True (stem resolver would find the file).
    ``clip_mov`` vs ``clip.mov``    → False (stem is "clip", not "clip_mov").
    """
    if not fixture:
        return False
    return Path(fixture).stem.lower() == token.lower()


def bin_stem(expected: str, actual: str, *, resolves: bool) -> str:
    """Bin a stem-class token row by (expected_outcome, actual_outcome, resolves)."""
    if resolves:
        return "STEM_SAFE" if expected == "plan" else "STEM_CONFLICT"
    return "STEM_RISK" if expected == "plan" else "STEM_OK"


# ── analysis over scoreboards ────────────────────────────────────────────────

_SKIP_JSON = frozenset({"descriptor_analysis.json", "score.json", "report.json", "review_log.json"})
_BINS = ("PRIZE", "RISK", "SAFE", "POLICY-CONFLICT", "PLAN_MISS", "CLARIFY_OK", "OTHER")
_STEM_BINS = ("STEM_SAFE", "STEM_RISK", "STEM_CONFLICT", "STEM_OK")


def _load_corpus_fixtures(corpus_path: Path) -> dict[str, str | None]:
    fixtures: dict[str, str | None] = {}
    with corpus_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            fixtures[row["id"]] = row.get("fixture")
    return fixtures


def _discover_scoreboards(run_dir: Path) -> dict[str, Path]:
    arms: dict[str, Path] = {}
    for jf in sorted(run_dir.glob("*.json")):
        if jf.name in _SKIP_JSON:
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            arms[jf.stem] = jf
    return arms


def analyze_arm(scoreboard: dict[str, Any], fixtures: dict[str, str | None]) -> dict[str, Any]:
    """Analyze one arm's scoreboard rows. Returns per-world bins + row records.

    Stem-format tokens (extension-less ASCII identifiers like ``clip_4k``) are
    tracked separately from NL descriptors (``"the 4K video"``) because they are
    resolved by the deterministic stem resolver, not by the clarify gate.
    """
    records: list[dict[str, Any]] = []
    stem_records: list[dict[str, Any]] = []
    counts = {"OFF": dict.fromkeys(_BINS, 0), "ON": dict.fromkeys(_BINS, 0)}
    stem_counts = dict.fromkeys(_STEM_BINS, 0)
    token_classes = {"descriptor": 0, "stem": 0, "exact": 0, "glob": 0, "chain": 0, "none": 0}

    for row in scoreboard.get("rows", []):
        tokens = extract_input_tokens(row.get("plan"))
        if not tokens:
            token_classes["none"] += 1
            continue

        utterance = row.get("utterance", "")
        fixture = fixtures.get(row.get("id"))
        primary = tokens[0]
        on_avail = available_files(fixture, utterance, injection=True)
        cls = classify_token(primary, on_avail)
        token_classes[cls] += 1

        expected = row.get("expected_outcome")
        actual = row.get("actual_outcome")

        if cls == "stem":
            resolves = stem_resolves(primary, fixture)
            b = bin_stem(expected, actual, resolves=resolves)
            stem_counts[b] += 1
            stem_records.append(
                {
                    "id": row.get("id"),
                    "utterance": utterance,
                    "token": primary,
                    "fixture": fixture,
                    "expected": expected,
                    "actual": actual,
                    "resolves": resolves,
                    "bin": b,
                }
            )
        elif cls == "descriptor":
            rec: dict[str, Any] = {
                "id": row.get("id"),
                "utterance": utterance,
                "token": primary,
                "expected": expected,
                "actual": actual,
                "tags": row.get("tags"),
            }
            for world, inject in (("OFF", False), ("ON", True)):
                avail = available_files(fixture, utterance, injection=inject)
                res = resolve_input(primary, avail)
                b = bin_row(expected, actual, res.label)
                counts[world][b] += 1
                rec[world] = {
                    "label": res.label,
                    "mode": res.mode,
                    "low_confidence": res.low_confidence,
                    "bin": b,
                }
            records.append(rec)

    return {
        "counts": counts,
        "stem_counts": stem_counts,
        "token_classes": token_classes,
        "records": records,
        "stem_records": stem_records,
    }


def analyze_run(run_dir: Path, corpus_path: Path) -> dict[str, Any]:
    fixtures = _load_corpus_fixtures(corpus_path)
    arms = _discover_scoreboards(run_dir)
    result: dict[str, Any] = {"run_dir": str(run_dir), "arms": {}}
    for arm, path in arms.items():
        scoreboard = json.loads(path.read_text(encoding="utf-8"))
        result["arms"][arm] = analyze_arm(scoreboard, fixtures)
    return result


# ── report rendering ─────────────────────────────────────────────────────────


def _bin_table(counts: dict[str, int]) -> str:
    return " | ".join(f"{b}={v}" for b, v in counts.items() if v)


def render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Descriptor / Mixed-Intent Analysis",
        "",
        f"Run: `{result['run_dir']}`",
        "",
    ]
    for arm, data in result["arms"].items():
        c = data["counts"]
        sc = data["stem_counts"]
        tc = data["token_classes"]
        lines += [
            f"## {arm}",
            "",
            "Input-token classes: " + ", ".join(f"{k}={v}" for k, v in tc.items() if v),
            "",
            "### NL descriptor bins (injection OFF / ON)",
            "",
            f"- injection OFF (today): {_bin_table(c['OFF']) or '(none)'}",
            f"- injection ON  (future): {_bin_table(c['ON']) or '(none)'}",
            "",
        ]
        flips = [
            r for r in data["records"] if r["OFF"]["bin"] == "RISK" and r["ON"]["bin"] == "SAFE"
        ]
        if flips:
            lines.append(f"#### NL mislabeled-for-today (RISK→SAFE under injection): {len(flips)}")
            for r in flips:
                lines.append(f"- `{r['id']}` [{r['token']}] — {r['utterance']}")
            lines.append("")
        conflicts = [r for r in data["records"] if r["ON"]["bin"] == "POLICY-CONFLICT"]
        if conflicts:
            lines.append(
                f"#### NL POLICY-CONFLICT (expected clarify, resolves under ON): {len(conflicts)}"
            )
            for r in conflicts:
                lines.append(f"- `{r['id']}` [{r['token']}] — {r['utterance']}")
            lines.append("")

        lines += [
            "### Stem-format bins (handled by deterministic stem resolver)",
            "",
            f"- {_bin_table(sc) or '(none)'}",
            "",
        ]
        stem_conflicts = [r for r in data["stem_records"] if r["bin"] == "STEM_CONFLICT"]
        if stem_conflicts:
            lines.append(
                f"#### STEM_CONFLICT (expected clarify, stem resolves → policy call): {len(stem_conflicts)}"
            )
            for r in stem_conflicts:
                lines.append(
                    f"- `{r['id']}` [{r['token']}] fixture={r['fixture']} — {r['utterance']}"
                )
            lines.append("")
        stem_risks = [r for r in data["stem_records"] if r["bin"] == "STEM_RISK"]
        if stem_risks:
            lines.append(f"#### STEM_RISK (expected plan, stem doesn't resolve): {len(stem_risks)}")
            for r in stem_risks:
                lines.append(
                    f"- `{r['id']}` [{r['token']}] fixture={r['fixture']} — {r['utterance']}"
                )
            lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Read-only descriptor / mixed-intent analyzer.")
    p.add_argument(
        "run_dir",
        help="Directory of *_success.json scoreboards (e.g. evals/runs/<run>).",
    )
    p.add_argument(
        "--corpus",
        default="skills/ffmpeg/data/eval.jsonl",
        help="Corpus JSONL (for fixture lookup).",
    )
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir)
    result = analyze_run(run_dir, Path(args.corpus))

    (run_dir / "descriptor_analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = render_markdown(result)
    (run_dir / "descriptor_analysis.md").write_text(md, encoding="utf-8")

    # The report carries "→" and multilingual utterances, so a cp1252 console
    # (Windows default) raises on print().  Write bytes and let unrepresentable
    # characters degrade rather than kill the run — the .md file is already saved.
    import sys

    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(md.encode("utf-8", errors="replace"))
        buf.write(b"\n")
    else:  # captured stdout (pytest, etc.)
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
