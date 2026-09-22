"""`quality` is a closed vocabulary, and nothing used to say so.

The model was asked for "visually lossless quality" — a real term of art, and one this skill
supports: ``best_possible`` is CRF 18 and its own profile notes call it *"Visually transparent
quality"*. But the prompt's phrase table never names that profile, and the two nearest values it
does list sit on adjacent lines (``visually_good``, ``lossless``). The model blended them into
``visually_lossless``.

Nothing caught it. `quality` carried no ``arg_schema`` on any of the ten tools that accept it, so
the two passes that exist for exactly this — enum-alias coercion in ``normalize_plan`` and the
enum check in ``validate_plan`` — had nothing to check against. The value survived validation and
died four steps later inside a *filesystem lookup*, as ``Unknown quality profile``, because
profiles are files and the file was not there. A vocabulary error reported as a missing file,
after the plan had been accepted.

The split these tests pin: the **model-facing** `quality` is closed, because the model is choosing
from a list it was shown. The **internal** ``load_quality_profile.quality`` stays open, because
expansion deliberately routes a CRF spelling (``crf 20``) through it via ``_quality_from_crf``.
Closing both would break a tolerance that exists on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from knaif.planner import normalize_plan, validate_plan
from knaif.registry import load_registry

FFMPEG_SKILL_DIR = Path(__file__).parents[2]
REGISTRY = load_registry(FFMPEG_SKILL_DIR / "tools.yaml")

#: Every tool whose `quality` the model picks. `load_quality_profile` is deliberately absent.
MODEL_FACING = [
    name for name, tool in REGISTRY.items() if "quality" in tool.optional_args and not tool.internal
]


def _plan(quality: str, tool: str = "convert_video") -> dict:
    return {"plan": [{"tool": tool, "args": {"inputs": ["clip.mp4"], "quality": quality}}]}


# ── the vocabulary is declared at all ────────────────────────────────────────


def test_every_tool_that_takes_a_quality_declares_which_ones() -> None:
    """A tool that accepts `quality` without a schema is the hole this closes.

    Asserted across the whole registry rather than on one tool: the bug was not that
    `convert_video` lacked a schema, it was that *nothing* had one, and a fix applied to the
    tool that happened to fail would leave the other nine open.
    """
    assert MODEL_FACING, "no model-facing tool takes a quality — the test is mis-wired"
    for name in MODEL_FACING:
        schema = REGISTRY[name].arg_schemas.get("quality")
        assert schema is not None, f"{name} accepts a quality but declares no schema for it"
        assert schema.type == "enum", f"{name}'s quality is not typed as a closed vocabulary"
        assert schema.enum, f"{name}'s quality enum is empty"


def test_the_vocabulary_is_exactly_the_profiles_on_disk() -> None:
    """The enum and the profile files must agree, in both directions.

    A profile is loaded by *filename*, so the enum is a second spelling of the directory
    listing — and a second spelling drifts. Adding a profile without the enum makes it
    unreachable; adding an enum value without the profile restores the original bug exactly,
    since validation would then wave through a name that has no file.
    """
    on_disk = {p.stem for p in (FFMPEG_SKILL_DIR / "profiles" / "quality").glob("*.yaml")}
    declared = set(REGISTRY["convert_video"].arg_schemas["quality"].enum or ())
    assert declared == on_disk


# ── the value the model actually sent ────────────────────────────────────────


def test_visually_lossless_resolves_to_the_profile_that_means_it() -> None:
    """The term the user typed is not a mistake — it is this skill's `best_possible`.

    Coercion, not rejection. The model asked for a capability that exists under the name its
    own domain uses, and answering that with a validation error would be knaif failing to
    speak ffmpeg.
    """
    plan = _plan("visually_lossless")
    normalize_plan(plan, REGISTRY)
    assert plan["plan"][0]["args"]["quality"] == "best_possible"


@pytest.mark.parametrize(
    "spelling", ["visually lossless", "Visually_Lossless", "visually-lossless"]
)
def test_the_phrase_survives_however_it_is_spelled(spelling: str) -> None:
    """Separator and case are the model's choice, not a different request."""
    plan = _plan(spelling)
    normalize_plan(plan, REGISTRY)
    assert plan["plan"][0]["args"]["quality"] == "best_possible"


def test_an_invented_quality_is_rejected_before_anything_runs(tmp_path: Path) -> None:
    """The failure moves from execution to validation, and says what the choices were.

    This is the half that matters even when the alias table misses: an unknown value now
    stops at the gate with the list in hand — which also feeds the validator-feedback retry,
    so the model gets a second attempt it can actually act on.
    """
    plan = _plan("ultra_max_quality")
    normalize_plan(plan, REGISTRY)
    with pytest.raises(ValueError, match="quality") as excinfo:
        validate_plan(plan, REGISTRY, tmp_path)
    assert "best_possible" in str(excinfo.value), "the error must name the real choices"


# ── the tolerance that must survive ──────────────────────────────────────────


def test_the_internal_step_still_takes_a_crf_spelling(tmp_path: Path) -> None:
    """`crf 20` reaches `quality` by design, from the *crf* arg, during expansion.

    `_quality_from_crf` puts a CRF spelling into the internal step's quality slot so a raw
    CRF survives to the engine, which maps it to the nearest profile and keeps the exact
    number. Typing that slot as a closed enum would reject knaif's own expansion output.
    """
    assert REGISTRY["load_quality_profile"].arg_schemas.get("quality") is None
    plan = {"plan": [{"tool": "load_quality_profile", "args": {"quality": "crf 20"}}]}
    normalize_plan(plan, REGISTRY)
    validate_plan(plan, REGISTRY, tmp_path, allow_internal=True)
    assert plan["plan"][0]["args"]["quality"] == "crf 20"


# ── the root cause: the model was never taught the word ──────────────────────


def test_the_prompt_teaches_the_phrase_it_kept_guessing() -> None:
    """An alias catches the blend; the phrase table is why the blend happened.

    `best_possible` appeared in `prompt.yaml` exactly once, inside an example, and never in
    the phrase→value table the model is actually reading when it maps an adjective. Leaving
    that gap and fixing only the alias would treat the symptom.
    """
    prompt = yaml.safe_load((FFMPEG_SKILL_DIR / "prompt.yaml").read_text(encoding="utf-8"))
    header = prompt["system_header"]
    assert "visually lossless" in header.lower()
    # Specifically in the phrase table, not merely somewhere in the file — `best_possible`
    # was already present in an example, which is exactly what failed to teach it.
    table = header.split("Map qualitative phrases:", 1)[-1]
    assert "best_possible" in table
