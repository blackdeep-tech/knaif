"""Guard each feature's own training rows, `skills/<skill>/data/train_<feature>.jsonl`.

A feature's training rows live in their own file (`train_url.jsonl`; its eval rows are in
`eval.jsonl` under the feature's tag since 2026-10-28) and join
the base `train.jsonl` only in the training run (`build_dataset.py --extra-jsonl`). They get
the base file's guards, and two more that a feature needs:

- **held out from every corpus of the skill**, not just `eval.jsonl`: a feature's eval file is
  where its own rows are measured;
- **every planned tool is in the prompt built for that wording**: a feature's tools are often
  retrieved by triggers (`alert_when_done`, `split_video`, ...) or join through a rule, and a
  row whose tool the prompt does not show teaches a mapping the model can never use.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from knaif import CommandAgent
from knaif.registry import retrieve_tools

ROOT = Path(".").resolve()

#: (skill, feature, the tag every row of that file carries).
FEATURE_FILES = [
    ("ffmpeg", "url", "url_input"),
    ("documents", "url", "url_input"),
    ("ffmpeg", "alert", "completion_alert"),
    ("documents", "alert", "completion_alert"),
    ("ffmpeg", "batch", "batch"),
    ("documents", "batch", "batch"),
]
IDS = [f"{skill}-{feature}" for skill, feature, _ in FEATURE_FILES]

#: Tools every prompt carries, so they need no retrieval.
ALWAYS_SHOWN = {"clarify", "reject"}

FF_QUALITY = {"small_file", "balanced", "visually_good", "high_quality", "lossless"}
FF_QUALITY |= {"best_possible"}
FF_PLATFORM = {"whatsapp", "email", "web", "youtube", "instagram_reels", "tiktok", "archive"}
DOC_TOFORMAT = {"pdf", "txt", "md", "png", "jpg"}
DOC_CQUALITY = {"small", "balanced", "high"}
DOCUMENTS_ONLY_TOOLS = {"compress_pdf", "merge_pdfs", "watermark", "split_pdf"}
FFMPEG_ONLY_TOOLS = {"convert_video", "compress_video", "trim_video", "extract_audio"}


def _path(skill: str, feature: str) -> Path:
    return ROOT / f"skills/{skill}/data/train_{feature}.jsonl"


def _rows(skill: str, feature: str) -> list[dict]:
    lines = _path(skill, feature).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?"))


def _held_out(skill: str) -> dict[str, str]:
    """Every utterance of every corpus the skill is measured on -> the file it is in."""
    out: dict[str, str] = {}
    data = ROOT / f"skills/{skill}/data"
    for path in sorted(data.glob("eval*.jsonl")) + [data / "safety_test.jsonl"]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            for utterance in rec.get("utterances") or [rec.get("utterance") or ""]:
                out[_norm(utterance)] = path.name
    return out


def _generator():
    spec = importlib.util.spec_from_file_location("gen_train", ROOT / "scripts" / "gen_train.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    return gen


@pytest.mark.parametrize(("skill", "feature", "tag"), FEATURE_FILES, ids=IDS)
def test_feature_file_is_reproducible_from_its_generator(skill, feature, tag) -> None:
    """The file is generated: `scripts/gen_train.py` authors `<skill>_rows_<feature>()`."""
    authored = {utt for utt, _plan, _tags in getattr(_generator(), f"{skill}_rows_{feature}")()}
    committed = {rec["utterance"] for rec in _rows(skill, feature)}
    assert committed == authored, (
        f"{skill} train_{feature}: only in the file {sorted(committed - authored)}, "
        f"only in the generator {sorted(authored - committed)} - regenerate it"
    )


@pytest.mark.parametrize(("skill", "feature", "tag"), FEATURE_FILES, ids=IDS)
def test_every_feature_row_validates_and_carries_its_tag(skill, feature, tag) -> None:
    agent = CommandAgent.from_skill(f"skills/{skill}", sandbox="./sandbox")
    bad = []
    for rec in _rows(skill, feature):
        if tag not in (rec.get("tags") or []):
            bad.append(f"{rec['utterance'][:50]!r}: no {tag!r} tag")
        try:
            agent.validate_plan(rec["plan"])
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{rec['utterance'][:50]!r}: {exc}")
    assert not bad, f"{skill} train_{feature}:\n" + "\n".join(bad)


@pytest.mark.parametrize(("skill", "feature", "tag"), FEATURE_FILES, ids=IDS)
def test_feature_rows_use_their_own_skills_values(skill, feature, tag) -> None:
    """The base file's contamination guard: no enum or tool of the other skill."""
    bad = []
    for rec in _rows(skill, feature):
        for s in rec["plan"]["plan"]:
            args = s.get("args", {})
            if skill == "ffmpeg":
                if "quality" in args and args["quality"] not in FF_QUALITY:
                    bad.append(f"{rec['utterance'][:45]!r}: quality={args['quality']!r}")
                if "platform" in args and args["platform"] not in FF_PLATFORM:
                    bad.append(f"{rec['utterance'][:45]!r}: platform={args['platform']!r}")
                if s["tool"] in DOCUMENTS_ONLY_TOOLS:
                    bad.append(f"{rec['utterance'][:45]!r}: tool={s['tool']}")
            else:
                if "to_format" in args and args["to_format"] not in DOC_TOFORMAT:
                    bad.append(f"{rec['utterance'][:45]!r}: to_format={args['to_format']!r}")
                cq = args.get("compress_quality")
                if cq is not None and cq not in DOC_CQUALITY:
                    bad.append(f"{rec['utterance'][:45]!r}: compress_quality={cq!r}")
                if s["tool"] in FFMPEG_ONLY_TOOLS:
                    bad.append(f"{rec['utterance'][:45]!r}: tool={s['tool']}")
    assert not bad, f"{skill} train_{feature} contamination:\n" + "\n".join(bad)


@pytest.mark.parametrize(("skill", "feature", "tag"), FEATURE_FILES, ids=IDS)
def test_feature_rows_copy_no_held_out_utterance(skill, feature, tag) -> None:
    """Not from `eval.jsonl`, the feature eval files, the removed rows or the safety corpus."""
    held_out = _held_out(skill)
    copied = [
        f"{held_out[_norm(r['utterance'])]}: {r['utterance']!r}"
        for r in _rows(skill, feature)
        if _norm(r["utterance"]) in held_out
    ]
    assert not copied, f"{skill} train_{feature} copies held-out utterances:\n" + "\n".join(copied)


def test_no_utterance_is_taught_twice_across_a_skills_files() -> None:
    """One request, one plan: a wording in two files would be taught two answers, or twice."""
    bad = []
    for skill in sorted({s for s, _f, _t in FEATURE_FILES}):
        seen: dict[str, str] = {}
        base = ROOT / f"skills/{skill}/data/train.jsonl"
        files = [("train", base)] + [
            (f"train_{f}", _path(s, f)) for s, f, _t in FEATURE_FILES if s == skill
        ]
        for name, path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                key = _norm(json.loads(line)["utterance"])
                if key in seen:
                    bad.append(f"{skill}: {key!r} in {seen[key]} and {name}")
                seen.setdefault(key, name)
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize(("skill", "feature", "tag"), FEATURE_FILES, ids=IDS)
def test_every_planned_tool_is_in_the_prompt_for_that_wording(skill, feature, tag) -> None:
    """Training a tool the prompt does not show teaches a mapping the model cannot use.

    `build_dataset.py` builds each row's prompt from `retrieve_tools(utterance)`, so a wording
    whose trigger or keyword does not retrieve the tool (often a language the triggers do not
    cover) trains the model to emit a tool it was never offered.
    """
    agent = CommandAgent.from_skill(f"skills/{skill}", sandbox="./sandbox")
    bad = []
    for rec in _rows(skill, feature):
        shown = set(retrieve_tools(rec["utterance"], agent.registry))
        missing = [
            s["tool"]
            for s in rec["plan"]["plan"]
            if s["tool"] not in shown and s["tool"] not in ALWAYS_SHOWN
        ]
        if missing:
            bad.append(f"{rec['utterance']!r}: {missing} not retrieved")
    assert not bad, f"{skill} train_{feature}:\n" + "\n".join(bad)
