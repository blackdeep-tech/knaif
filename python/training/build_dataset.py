"""Assemble the union chat dataset for fine-tuning (Task 6 input).

Run in the CORE venv (needs `knaif` + skills): it reproduces the EXACT inference
prompt per row — retrieve_tools(utterance) → agent.build_prompt(...) — so the
model trains on the same (system, user) it will see at eval time. The target is
the literal {"plan": [...]} JSON. Writes a static training/union_chat.jsonl that
the isolated train venv consumes without importing knaif.

    uv run python training/build_dataset.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from knaif import CommandAgent  # noqa: E402
from knaif.registry import retrieve_tools  # noqa: E402

SKILLS = ["ffmpeg", "documents"]
OUT = ROOT / "training" / "union_chat.jsonl"


def parse_weight_map(spec: str) -> dict[str, int]:
    """Parse comma-separated NAME=WEIGHT pairs for deterministic row replication."""
    weights: dict[str, int] = {}
    if not spec:
        return weights
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SystemExit(f"Invalid weight {item!r}; expected name=integer")
        name, value = item.split("=", 1)
        name = name.strip()
        try:
            weight = int(value)
        except ValueError as exc:
            raise SystemExit(f"Invalid weight for {name!r}: {value!r}") from exc
        if not name or weight < 1:
            raise SystemExit(f"Invalid weight {item!r}; names must be nonempty and weights >= 1")
        weights[name] = weight
    return weights


def parse_extra_jsonl(specs: list[str]) -> dict[str, list[Path]]:
    """Parse repeated SKILL=PATH specs for extra train rows."""
    extras: dict[str, list[Path]] = defaultdict(list)
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"Invalid --extra-jsonl {spec!r}; expected skill=path")
        skill, raw_path = spec.split("=", 1)
        skill = skill.strip()
        path = Path(raw_path.strip())
        if not skill or not raw_path.strip():
            raise SystemExit(f"Invalid --extra-jsonl {spec!r}; expected nonempty skill=path")
        extras[skill].append(path)
    return dict(extras)


def repetition_count(
    skill: str, tags: list[str], skill_weights: dict[str, int], tag_weights: dict[str, int]
) -> int:
    skill_weight = skill_weights.get(skill, 1)
    tag_weight = max(
        (max(tag_weights.get(tag, 1), tag_weights.get(f"{skill}:{tag}", 1)) for tag in tags),
        default=1,
    )
    return skill_weight * tag_weight


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skills",
        default=",".join(SKILLS),
        help="comma-separated skill subset (default: all). e.g. --skills ffmpeg",
    )
    ap.add_argument("--out", default=str(OUT), help="output chat-dataset path")
    ap.add_argument(
        "--skill-weights",
        default="",
        help="comma-separated skill=integer replication weights, e.g. ffmpeg=2",
    )
    ap.add_argument(
        "--weight-tags",
        default="",
        help="comma-separated tag=integer weights, optionally skill-scoped, e.g. hard_target=3,ffmpeg:chain3=3",
    )
    ap.add_argument(
        "--extra-jsonl",
        action="append",
        default=[],
        help="extra train rows as skill=path; may be repeated",
    )
    args = ap.parse_args()
    skills = [s.strip() for s in args.skills.split(",") if s.strip()]
    out_path = Path(args.out)
    skill_weights = parse_weight_map(args.skill_weights)
    tag_weights = parse_weight_map(args.weight_tags)
    extra_jsonl = parse_extra_jsonl(args.extra_jsonl)

    rows: list[dict] = []
    source_by_skill: Counter[str] = Counter()
    expanded_by_skill: Counter[str] = Counter()
    source_by_tag: Counter[str] = Counter()
    expanded_by_tag: Counter[str] = Counter()
    for skill in skills:
        agent = CommandAgent.from_skill(f"src/skills/{skill}", sandbox="./sandbox")
        paths = [ROOT / f"src/skills/{skill}/data/train.jsonl", *extra_jsonl.get(skill, [])]
        n = 0
        expanded = 0
        for path in paths:
            path = path if path.is_absolute() else ROOT / path
            file_n = 0
            file_expanded = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                utt, plan = rec["utterance"], rec["plan"]
                tags = list(rec.get("tags") or [])
                # Faithful inference prompt: retrieved-tools subset + that skill's header/examples.
                override = retrieve_tools(utt, agent.registry)
                system_msg, user_msg = agent.build_prompt(utt, registry_override=override)
                # Sanity: the plan must still validate (the train.jsonl is pre-validated,
                # but re-checking guards against a stale file).
                agent.validate_plan(plan)
                row = {
                    "skill": skill,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
                    ],
                }
                reps = repetition_count(skill, tags, skill_weights, tag_weights)
                rows.extend(row for _ in range(reps))
                n += 1
                expanded += reps
                file_n += 1
                file_expanded += reps
                source_by_skill[skill] += 1
                expanded_by_skill[skill] += reps
                for tag in tags:
                    source_by_tag[tag] += 1
                    expanded_by_tag[tag] += reps
                    source_by_tag[f"{skill}:{tag}"] += 1
                    expanded_by_tag[f"{skill}:{tag}"] += reps
            if path.name != "train.jsonl":
                print(f"  extra {path}: {file_n} source rows -> {file_expanded} expanded rows")
        print(f"{skill}: {n} source rows -> {expanded} expanded rows")

    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print("\nsummary by skill:")
    for skill in skills:
        print(f"  {skill}: {source_by_skill[skill]} source -> {expanded_by_skill[skill]} expanded")
    if tag_weights:
        print("weighted tags:")
        for tag in sorted(tag_weights):
            print(f"  {tag}: {source_by_tag[tag]} source -> {expanded_by_tag[tag]} expanded")
    print(f"\nwrote {len(rows)} rows ({'+'.join(skills)}) -> {out_path}")


if __name__ == "__main__":
    main()
