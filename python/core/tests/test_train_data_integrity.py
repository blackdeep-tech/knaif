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


# ── the reject/clarify taxonomy, in the weights ──────────────────────────────
# One fine-tune serves every skill, so `train.jsonl` is where the reject/clarify split
# either sticks or is unlearned. Auditing ffmpeg's 17 `reject` rows is what showed the
# contradiction was not only in the prompt: only 3 were invariants, and the model had been
# taught "reject = anything I can't do" — the exact conflation this plan removes.
# See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5.

#: Things no change to a tool inventory makes allowed. A `reject` row says one of these.
_INVARIANT_MARKERS = (
    "delete",
    "wipe",
    "shred",
    "rm -rf",
    "overwrite",
    "format the drive",
    "format my",
    "forge",
    "outside the sandbox",
    "system file",
    "system command",
    "on my drive",
    # Two spellings of the same invariant: a path that is outside the sandbox by construction.
    # "outside the sandbox" alone only catches the request that says so in those words, and
    # the one the safety gate actually failed on did not — it named the system root.
    "root directory",
    "/etc/",
)

#: Things this skill has no tool for *today*. A `clarify` row says one of these — training
#: "upload -> reject" teaches a future upload skill that its core capability is a refusal.
_CAPABILITY_GAP_MARKERS = (
    "email",
    "upload",
    "download",
    "to the server",
    "to the cloud",
    "printer",
    "fax",
    "flawless",
    "magically",
    "fakecodec",
    "madeupcodec",
    "does not exist",
    "0x0",
    ".wav",
    ".mp3",
    ".aac",
    "translate",
)


def _control_rows(skill: str, tool: str) -> list[dict]:
    out = []
    for rec in _rows(skill):
        steps = (rec.get("plan") or {}).get("plan") or []
        if steps and steps[0].get("tool") == tool:
            out.append(rec)
    return out


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_reject_rows_teach_invariants_only(skill: str) -> None:
    """A `reject` row must name something that is never allowed, whatever ships later."""
    bad = []
    for rec in _control_rows(skill, "reject"):
        u = rec["utterance"].lower()
        if not any(m in u for m in _INVARIANT_MARKERS):
            bad.append(f"{rec['utterance']!r} — not an invariant; a capability gap is a clarify")
    assert not bad, f"{skill}:\n" + "\n".join(bad)


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_capability_gaps_are_never_taught_as_refusals(skill: str) -> None:
    """The other direction: nothing a future skill could do may be trained as `reject`.

    `build_dataset.py` binds every row to its own skill's prompt and retrieved tools, so the
    supervision is contextual — "email -> clarify **under ffmpeg's inventory**" does not
    contradict "email -> plan under an upload skill's". That is what makes relabelling the
    right move rather than deletion, and it only holds if no gap is left on the reject side.
    """
    bad = []
    for rec in _control_rows(skill, "reject"):
        u = rec["utterance"].lower()
        hit = [m for m in _CAPABILITY_GAP_MARKERS if m in u]
        if hit:
            bad.append(f"{rec['utterance']!r} — {hit} is a capability gap, not a policy violation")
    assert not bad, f"{skill}:\n" + "\n".join(bad)


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_unsupported_clarify_rows_say_so_instead_of_asking(skill: str) -> None:
    """`clarify` now carries two meanings, and only the question text separates them.

    `score_corpus` grades a non-`plan` row on its outcome label alone, so nothing measures
    this — which is exactly why it has to be taught here. A user who asks to email a file
    should be told this skill cannot send files, not asked which mail account to use.
    """
    bad = []
    for rec in _control_rows(skill, "clarify"):
        u = rec["utterance"].lower()
        if not any(m in u for m in _CAPABILITY_GAP_MARKERS):
            continue  # a genuinely ambiguous request; asking is the right answer
        q = rec["plan"]["plan"][0]["args"]["question"].lower()
        if not any(p in q for p in ("supported", "achievable", "can't", "cannot", "not able")):
            bad.append(f"{rec['utterance']!r} -> {q!r} — asks a question instead of saying no")
    assert not bad, f"{skill}:\n" + "\n".join(bad)


# -- reproducibility -----------------------------------------------------------


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_train_jsonl_is_reproducible_from_its_generator(skill: str) -> None:
    """`train.jsonl` is generated. Anything that exists only in the file is one run from gone.

    Found the hard way on 2026-09-12: regenerating for the T5 relabel silently dropped eight
    `v4` / `terse_no_audio` chain rows that had been added straight to `train.jsonl` and never
    to `scripts/gen_train.py`. They are the fix for a live routing failure and had never been
    trained, so losing them would have been invisible until the next eval — and then looked
    like a regression rather than a deletion.

    This compares utterances, not whole rows: the labels and wording of a row are meant to be
    edited in the generator, but the *set* of things taught must come from one place.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_train", ROOT / "scripts" / "gen_train.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    authored = {utt for utt, _plan, _tags in getattr(gen, f"{skill}_rows")()}
    committed = {rec["utterance"] for rec in _rows(skill)}

    only_in_file = committed - authored
    only_in_generator = authored - committed
    assert not only_in_file, (
        f"{skill}: {len(only_in_file)} row(s) exist only in train.jsonl and would be deleted by "
        f"`uv run python scripts/gen_train.py` - add them to the generator: {sorted(only_in_file)}"
    )
    assert not only_in_generator, (
        f"{skill}: the generator authors {len(only_in_generator)} row(s) that are not in "
        f"train.jsonl - regenerate it: {sorted(only_in_generator)}"
    )


# ── the gate may not test an invariant the training mix never taught ──────────

#: Tags that mark a safety row as a safety row rather than naming *which* invariant it
#: exercises. Everything else on a safety row is a category, and a category is a claim
#: that the model was taught to refuse that specific thing.
_SAFETY_MARKERS = {"safety", "unsafe"}


def _safety_categories(skill: str) -> dict[str, list[str]]:
    """category -> the safety-corpus utterances that exercise it."""
    path = ROOT / f"skills/{skill}/data/safety_test.jsonl"
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for tag in row.get("tags") or []:
            if tag not in _SAFETY_MARKERS:
                out.setdefault(tag, []).append(row["utterance"])
    return out


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_every_safety_category_is_taught_in_training(skill: str) -> None:
    """Every invariant the safety gate tests must appear among the trained `reject` rows.

    The gate admits no tolerance — one miss and the skill fails acceptance — so a category it
    tests with nothing behind it in the training mix is a scheduled failure, not a risk.
    T5 cut ffmpeg's `reject` rows from 17 to 3 because 14 were capability gaps, which was
    right, and left three of the five declared invariants (`sandbox_escape`, `system_files`,
    `shell_injection`) with no supervision at all. The T6a control arm then failed exactly
    one safety row — `ffmpeg_safety_system_root_dir` — with the model planning
    `convert_video` over `/*` and the sandbox validator, not the model, refusing it.

    Categories come from the corpora themselves, so adding a safety row in a new category
    fails here until something teaches it.
    """
    tested = _safety_categories(skill)
    taught = {
        tag
        for rec in _control_rows(skill, "reject")
        for tag in (rec.get("tags") or [])
        if tag != "reject"
    }
    missing = {c: u for c, u in tested.items() if c not in taught}
    assert not missing, (
        f"{skill}: the safety gate tests invariants nothing teaches — "
        + "; ".join(f"{c!r} (e.g. {u[0]!r})" for c, u in sorted(missing.items()))
        + f"\ntaught categories: {sorted(taught) or 'none'}"
    )


@pytest.mark.parametrize("skill", ["ffmpeg", "documents"])
def test_train_utterances_do_not_copy_the_safety_corpus(skill: str) -> None:
    """The safety gate is held out: training on its utterances would measure recall.

    The same rule as `test_ffmpeg_train_utterances_do_not_copy_eval_verbatim`, applied to the
    corpus whose bar is 100% — where a verbatim copy is the difference between "the model
    learned the invariant" and "the model memorised the sentence".
    """
    path = ROOT / f"skills/{skill}/data/safety_test.jsonl"
    held_out = {
        _norm_utterance(json.loads(line)["utterance"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    copied = [r["utterance"] for r in _rows(skill) if _norm_utterance(r["utterance"]) in held_out]
    assert not copied, f"{skill}: trained on held-out safety utterances: {copied}"
