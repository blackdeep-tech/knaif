"""Build preference pairs from real eval failures.

The rejected answer is a failed plan from the parent model. The chosen answer is
the highest-scoring plan for the same row from another already-evaluated model.
This keeps preference data grounded in actual model behavior instead of
hand-written counterfactuals.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from knaif import CommandAgent  # noqa: E402
from knaif.registry import retrieve_tools  # noqa: E402

DEFAULT_PARENT = (
    ROOT
    / "evals/runs/2026-07-01_sft-v3-flat_success"
    / "ffmpeg_qwen3-1.7b-sft-v3-flat-q6_success.json"
)
DEFAULT_CANDIDATES = [
    ROOT
    / "evals/runs/2026-07-01_sft-v3-gentle2_success"
    / "ffmpeg_qwen3-1.7b-sft-v3-gentle2-q6_success.json",
    ROOT
    / "evals/runs/2026-07-01_sft-v3-hard3_success"
    / "ffmpeg_qwen3-1.7b-sft-v3-hard3-q6_success.json",
    ROOT
    / "evals/runs/2026-07-01_sft-v3-low-lr_success"
    / "ffmpeg_qwen3-1.7b-sft-v3-low-lr-q6_success.json",
    ROOT
    / "evals/runs/2026-07-01_sft-v3-ffmpeg-flat_success"
    / "ffmpeg_qwen3-1.7b-sft-v3-ffmpeg-flat-q6_success.json",
]
DEFAULT_OUT = ROOT / "training" / "ffmpeg_pref_v1.jsonl"


def load_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(row["id"], row["utterance"]): row for row in data["rows"]}


def plan_text(row: dict[str, Any]) -> str:
    return json.dumps(row["plan"], ensure_ascii=False, separators=(",", ":"))


def usable_chosen(row: dict[str, Any], min_score: float) -> bool:
    if not row.get("outcome_correct"):
        return False
    if row.get("actual_outcome") in {"error", "parse_error"}:
        return False
    if row.get("expected_outcome") != "plan":
        return True
    score = row.get("knaif_score")
    return score is not None and float(score) >= min_score


def chosen_score(row: dict[str, Any]) -> float:
    if row.get("expected_outcome") != "plan":
        return 1.0
    return float(row.get("knaif_score") or 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", default=str(DEFAULT_PARENT))
    ap.add_argument("--candidate", action="append", dest="candidates")
    ap.add_argument("--skill", default="ffmpeg")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--min-score", type=float, default=0.8)
    args = ap.parse_args()

    parent_path = Path(args.parent)
    candidate_paths = [Path(p) for p in (args.candidates or DEFAULT_CANDIDATES)]
    parent_rows = load_rows(parent_path)
    candidate_rows = [(path.stem, load_rows(path)) for path in candidate_paths if path.exists()]
    if not candidate_rows:
        raise SystemExit("No candidate eval files found")

    agent = CommandAgent.from_skill(f"src/skills/{args.skill}", sandbox="./sandbox")
    pairs: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}

    for key, rejected in sorted(parent_rows.items()):
        if rejected.get("outcome_correct"):
            continue
        if not rejected.get("plan"):
            continue

        choices: list[tuple[float, str, dict[str, Any]]] = []
        for label, rows in candidate_rows:
            candidate = rows.get(key)
            if candidate and usable_chosen(candidate, args.min_score):
                choices.append((chosen_score(candidate), label, candidate))
        if not choices:
            continue

        choices.sort(key=lambda item: item[0], reverse=True)
        score, provider, chosen = choices[0]
        utterance = rejected["utterance"]
        override = retrieve_tools(utterance, agent.registry)
        system_msg, user_msg = agent.build_prompt(utterance, registry_override=override)
        pairs.append(
            {
                "id": rejected["id"],
                "utterance": utterance,
                "tags": rejected.get("tags") or [],
                "chosen_provider": provider,
                "chosen_score": score,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "chosen": plan_text(chosen),
                "rejected": plan_text(rejected),
            }
        )
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    out = Path(args.out)
    out.write_text(
        "".join(json.dumps(pair, ensure_ascii=False) + "\n" for pair in pairs),
        encoding="utf-8",
    )
    print(f"wrote {len(pairs)} preference pairs -> {out}")
    print("chosen providers:")
    for provider, count in sorted(provider_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {provider}: {count}")


if __name__ == "__main__":
    main()
