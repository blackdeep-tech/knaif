"""Guard the fine-tuning train.jsonl against schema breakage and cross-skill contamination.

The union fine-tune (one model serving every skill) is prone to vocabulary bleed — e.g. a
documents enum value (`compress_quality: small`) or tool name leaking into an ffmpeg plan,
which then teaches the model the wrong mapping (observed post-Task-8). `validate_plan` checks
tools + arg *keys* but NOT enum *values*, so these tests add the value-level guard on top of
a full structural validation of every row.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from knaif import CommandAgent

ROOT = Path(".").resolve()

# Canonical value spaces (mirror scripts/gen_train.py / the skill profiles).
FF_QUALITY = {
    "small_file",
    "balanced",
    "visually_good",
    "high_quality",
    "lossless",
    "best_possible",
}
FF_PLATFORM = {"whatsapp", "email", "web", "youtube", "instagram_reels", "tiktok", "archive"}
DOC_TOFORMAT = {"pdf", "txt", "md", "png", "jpg"}
DOC_CQUALITY = {"small", "balanced", "high"}


def _rows(skill: str) -> list[dict]:
    path = ROOT / f"skills/{skill}/data/train.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _agent(skill: str) -> CommandAgent:
    return CommandAgent.from_skill(f"skills/{skill}", sandbox="./sandbox")


def _norm_utterance(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?"))


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_every_train_plan_validates(skill: str) -> None:
    """Every train row's plan must pass the live validator (valid tools + arg keys)."""
    agent = _agent(skill)
    bad: list[str] = []
    for rec in _rows(skill):
        try:
            agent.validate_plan(rec["plan"])
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{rec['utterance'][:50]!r}: {exc}")
    assert not bad, f"{skill}: {len(bad)} invalid train plan(s):\n" + "\n".join(bad[:20])


def test_ffmpeg_no_documents_enum_or_tool_contamination() -> None:
    """ffmpeg plans must use ffmpeg's value space — no documents enum bleed.

    The exact post-Task-8 failure was `quality: "small"` (documents' compress_quality) leaking
    into ffmpeg, which wants `small_file`; and a hallucinated `convert_audio` tool.
    """
    bad: list[str] = []
    for rec in _rows("ffmpeg"):
        for s in rec["plan"]["plan"]:
            args = s.get("args", {})
            if "quality" in args and args["quality"] not in FF_QUALITY:
                bad.append(f"{rec['utterance'][:45]!r}: quality={args['quality']!r}")
            if "platform" in args and args["platform"] not in FF_PLATFORM:
                bad.append(f"{rec['utterance'][:45]!r}: platform={args['platform']!r}")
            if s["tool"] == "convert_audio":  # non-existent tool the LoRA hallucinated
                bad.append(f"{rec['utterance'][:45]!r}: tool=convert_audio")
    assert not bad, "ffmpeg cross-skill contamination:\n" + "\n".join(bad)


def test_documents_enum_values_canonical() -> None:
    """documents plans must use documents' own enum spaces (kept distinct from ffmpeg)."""
    bad: list[str] = []
    for rec in _rows("documents"):
        for s in rec["plan"]["plan"]:
            args = s.get("args", {})
            if "to_format" in args and args["to_format"] not in DOC_TOFORMAT:
                bad.append(f"{rec['utterance'][:45]!r}: to_format={args['to_format']!r}")
            if "compress_quality" in args and args["compress_quality"] not in DOC_CQUALITY:
                bad.append(
                    f"{rec['utterance'][:45]!r}: compress_quality={args['compress_quality']!r}"
                )
    assert not bad, "documents enum drift:\n" + "\n".join(bad)


def test_ffmpeg_train_utterances_do_not_copy_eval_verbatim() -> None:
    """Hard-focused training rows must stay adjacent to eval rows, not copy them."""
    eval_path = ROOT / "skills/ffmpeg/data/eval.jsonl"
    eval_utts: dict[str, str] = {}
    for line in eval_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        for utterance in rec.get("utterances", []):
            eval_utts[_norm_utterance(utterance)] = rec["id"]

    bad: list[str] = []
    for rec in _rows("ffmpeg"):
        key = _norm_utterance(rec["utterance"])
        if key in eval_utts:
            bad.append(f"{eval_utts[key]}: {rec['utterance']!r}")

    assert not bad, "ffmpeg train row copies eval utterance(s):\n" + "\n".join(bad)
