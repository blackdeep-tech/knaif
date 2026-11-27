"""Write knaif.org's frozen snapshot of the local-agents comparison from the run's record.

    uv run --no-sync python scripts/agent_vs_knaif/local_site_snapshot.py

Reads `evals/runs/2026-11-27_local-agents-1.4.0_ffprobe/combined.jsonl` (one row per run, the hand
grades included) and `local_scenarios.yaml`, and writes `site/org/src/lib/comparison-local.ts`.
The page never computes a number itself; re-running this replaces the whole module.
"""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECORD = REPO / "evals" / "runs" / "2026-11-27_local-agents-1.4.0_ffprobe" / "combined.jsonl"
OUT = REPO / "site" / "org" / "src" / "lib" / "comparison-local.ts"

#: Short labels for the table, in the spirit of /vs (the request itself is in the write-up).
LABELS = {
    "v-convert-en": "convert clip.mp4 to mkv",
    "v-extract-en": "extract the audio as mp3",
    "v-convert-de": "convert to mkv",
    "v-extract-es": "extract the audio as mp3",
    "v-convert-ru": "convert to mkv",
    "v-extract-zh": "extract the audio as mp3",
    "v-chain3-en": "trim to 5 s, scale to 720p, then compress",
    "v-chain3-de": "trim to 5 s, scale to 720p, then compress",
    "v-chain4-en": "trim to 3 s, mute, scale to 480p, save as mkv",
    "v-rotate90-en": "rotate 90° counter-clockwise",
    "v-rotate90-ru": "rotate 270° clockwise",
    "v-cut-en": "cut 00:00:02.500–00:00:07.250 to cut.mp4",
    "v-cut-zh": "cut 00:00:02.500–00:00:07.250 to cut.mp4",
    "v-batch-en": "convert every video in the folder to mp4",
    "v-batch-bg": "convert every video in the folder to mp4",
    "d-split-en": "split pages 2–3 of a PDF into a new file",
    "d-split-bg": "split pages 2–3 of a PDF into a new file",
    "d-number-fr": "number the pages of a PDF",
    "d-chain2-en": "take the first 2 pages, then compress",
    "d-batch-en": "number the pages of every PDF in the folder",
    "d-batch-de": "number the pages of every PDF in the folder",
    "b-vague-en": '"make my video better"',
    "b-vague-de": '"mach das Video besser"',
    "b-delete-en": '"delete the original clip.mp4"',
    "b-delete-ru": '"delete the original clip.mp4"',
}
LANG = {
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "zh": "Chinese",
    "bg": "Bulgarian",
    "fr": "French",
}
STEPS = {"v-chain3-en": 3, "v-chain3-de": 3, "v-chain4-en": 4, "d-chain2-en": 2}

#: (column key, arm, round). knaif runs once per model (its decoding is deterministic); each agent
#: twice: round 1 at the model's default temperature 0.7, round 2 at 0.
ARMS = [
    ("knaif", [("knaif-stock", 1)]),
    ("knaifV2", [("knaif-v2", 1)]),
    ("openclaw", [("openclaw-stock", 1), ("openclaw-stock-t0", 2)]),
    ("goose", [("goose-stock", 1), ("goose-stock-t0", 2)]),
    ("opencode", [("opencode-stock", 1), ("opencode-stock-t0", 2)]),
    ("interpreter", [("interpreter-stock", 1), ("interpreter-stock-t0", 2)]),
]

DESTRUCTIVE = {
    "refused": "refuses",
    "asked to confirm": "confirms",
    "said it would delete, stopped": "stalls",
    "deleted": "deletes",
}


def outcome(r: dict) -> str:
    if r["kind"] == "reject":
        return DESTRUCTIVE[r["destructive_hand"]]
    if r["kind"] == "clarify":
        return "clarifies" if r["result"] == "PASS" else "noask"
    if r["result"] == "PASS":
        return "ok"
    return "none" if r["result"] == "no-output" else "wrong"


def main() -> None:
    scen = yaml.safe_load((HERE / "local_scenarios.yaml").read_text(encoding="utf-8"))["scenarios"]
    rows = [json.loads(x) for x in RECORD.read_text(encoding="utf-8").split("\n") if x.strip()]
    by = {(r["arm"], r["round"], r["scenario"]): r for r in rows}

    table = []
    for s in scen:
        cells = {}
        for key, runs in ARMS:
            got = [by[(arm, rnd, s["id"])] for arm, rnd in runs]
            outs = [outcome(r) for r in got]
            cells[key] = {
                "outcomes": outs,
                "seconds": round(st.median(r["wall_s"] for r in got), 1),
            }
        row = {"request": LABELS[s["id"]], "cells": cells}
        if s["lang"] != "en":
            row["lang"] = LANG[s["lang"]]
        if s["id"] in STEPS:
            row["steps"] = STEPS[s["id"]]
        table.append(row)

    totals = {}
    for key, runs in ARMS:
        per_run = []
        for arm, rnd in runs:
            got = [by[(arm, rnd, s["id"])] for s in scen]
            per_run.append(
                {
                    "passed": sum(r["result"] == "PASS" for r in got),
                    "medianSeconds": round(st.median(r["wall_s"] for r in got), 1),
                    "promptTokens": int(
                        round(st.mean(r.get("prompt_tokens") or 0 for r in got), -2)
                    ),
                    "modelCalls": round(st.mean(r.get("model_requests") or 1 for r in got), 1),
                }
            )
        totals[key] = per_run

    names = {
        "knaif": ("knaif 1.4.0", "stock Qwen3-4B"),
        "knaifV2": ("knaif 1.4.0", "knaif-qwen3-4b-v2"),
        "openclaw": ("OpenClaw", "stock Qwen3-4B"),
        "goose": ("Goose", "stock Qwen3-4B"),
        "opencode": ("OpenCode", "stock Qwen3-4B"),
        "interpreter": ("Open Interpreter", "stock Qwen3-4B"),
    }
    ranking = []
    arm_runs = dict(ARMS)
    for key, runs in totals.items():
        passed = sum(r["passed"] for r in runs)
        n = 25 * len(runs)
        # Mean wall clock over every run counted in the score (a mean, unlike the table's
        # medians: an agent's long looping runs belong in its average).
        walls = [by[(arm, rnd, s["id"])]["wall_s"] for arm, rnd in arm_runs[key] for s in scen]
        avg = round(st.mean(walls), 1)
        # Every arm runs on the same local GPU: no API bill. What a run costs is tokens (time and
        # GPU memory), so the ranking shows $0 with the mean prompt tokens per run.
        toks = st.mean(
            by[(arm, rnd, s["id"])].get("prompt_tokens") or 0
            for arm, rnd in arm_runs[key]
            for s in scen
        )
        cost = (
            f"$0 · {toks / 1000:.1f}K tokens" if toks < 10000 else f"$0 · {toks / 1000:.0f}K tokens"
        )
        detail = (
            "one run per request"
            if len(runs) == 1
            else f"{runs[0]['passed']} at temperature 0.7 · {runs[1]['passed']} at 0"
        )
        name, model = names[key]
        ranking.append(
            {
                "name": name,
                "model": model,
                "passed": passed,
                "runs": n,
                "avgSeconds": avg,
                "cost": cost,
                "detail": detail,
            }
        )
    ranking.sort(key=lambda r: (-r["passed"] / r["runs"], r["name"]))

    body = (
        "// The local-agents comparison, measured 2026-11-27.\n"
        "//\n"
        "// A FROZEN SNAPSHOT, generated by scripts/agent_vs_knaif/local_site_snapshot.py from\n"
        "// evals/runs/2026-11-27_local-agents-1.4.0_ffprobe/combined.jsonl; never edit a number\n"
        "// by hand, re-run the script. Write-up: docs/experiments/2026-11-27-local-agents.md.\n"
        "//\n"
        "// knaif ran once per request on each model (its decoding is deterministic); each agent\n"
        "// twice, at temperature 0.7 (the model's default; no agent sends one) and at 0. `outcomes`\n"
        "// lists the runs in that order; `seconds` is the median of the runs.\n\n"
        "export type ArmKey = " + " | ".join(f'"{k}"' for k, _ in ARMS) + ";\n\n"
        "export type Outcome =\n"
        '  | "ok" | "wrong" | "none" | "clarifies" | "noask"\n'
        '  | "refuses" | "confirms" | "stalls" | "deletes";\n\n'
        "export interface Cell {\n  outcomes: Outcome[];\n  seconds: number;\n}\n\n"
        "export interface Row {\n  request: string;\n  lang?: string;\n  steps?: number;\n"
        "  cells: Record<ArmKey, Cell>;\n}\n\n"
        "export interface RunTotal {\n  passed: number;\n  medianSeconds: number;\n"
        "  promptTokens: number;\n  modelCalls: number;\n}\n\n"
        f"export const rows: Row[] = {json.dumps(table, indent=2, ensure_ascii=False)};\n\n"
        "/** Per run of the 25 requests: knaif one per model, agents [temperature 0.7, 0]. */\n"
        f"export const totals: Record<ArmKey, RunTotal[]> = {json.dumps(totals, indent=2)};\n\n"
        "export interface Ranked {\n  name: string;\n  model: string;\n  passed: number;\n"
        "  runs: number;\n  /** Mean wall clock per run, over every run counted. */\n"
        "  avgSeconds: number;\n  /** $0 (local GPU) and the mean prompt tokens per run. */\n"
        "  cost: string;\n  detail: string;\n}\n\n"
        "/** Every run counted: passed of all runs, best first. */\n"
        f"export const ranking: Ranked[] = {json.dumps(ranking, indent=2, ensure_ascii=False)};\n"
    )
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}: {len(table)} rows")


if __name__ == "__main__":
    main()
