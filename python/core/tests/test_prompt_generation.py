"""The prompt generation: which served prompt a model was trained for.

`contracts/runtime/prompt_generation.yaml` declares a generation number and the fingerprint of
everything the model can be served (headers, rules, examples, model-visible tool definitions of
every active skill). The fingerprint test fails when the served prompt changes without a bump, so
a model's `trained_for` can be trusted, and a knaif version can say when a model predates its
prompt. Retrieval (keywords, triggers) decides which tools a request sees, not what they say,
so it is not part of the fingerprint.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from knaif.prompt_generation import (
    declared,
    fingerprint,
    fingerprint_of,
    note_for,
    served_content,
)

ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "contracts" / "runtime" / "prompt_generation.yaml"
PACKAGED = ROOT / "python" / "core" / "knaif" / "prompt_generation.yaml"
MANIFEST = ROOT / "contracts" / "models" / "model-manifest.yaml"


def test_declared_generation_is_a_positive_number_with_a_fingerprint() -> None:
    gen = declared()
    assert isinstance(gen.generation, int) and gen.generation >= 1
    assert len(gen.fingerprint) == 64 and int(gen.fingerprint, 16) >= 0


def test_the_served_prompt_matches_the_declared_generation() -> None:
    """The drift guard. A change here is a decision: a model trained before it predates it."""
    gen = declared()
    now = fingerprint()
    assert now == gen.fingerprint, (
        f"the served prompt changed since generation {gen.generation} was declared. If the "
        f"change reaches the model (a header, rule, example or tool text), bump `generation` to "
        f"{gen.generation + 1} and set `fingerprint: {now}` in contracts/runtime/"
        f"prompt_generation.yaml, then `just sync-runtime`; a model for it is due with the next "
        f"round. If it does not reach the model, the fingerprint is wrong: fix served_content."
    )


def test_packaged_copy_matches_the_canonical_file() -> None:
    assert PACKAGED.read_text(encoding="utf-8") == CANONICAL.read_text(
        encoding="utf-8"
    ), "prompt_generation.yaml drift: run `just sync-runtime`"


def test_fingerprint_covers_what_the_model_reads_and_nothing_else() -> None:
    content = served_content("ffmpeg")
    tools = content["tools"]
    assert tools and all(set(t) == {"name", "description", "args"} for t in tools)
    assert "clarify" not in {t["name"] for t in tools}  # named in the header, never listed
    assert content["header"] and content["examples"] and content["rules"]
    before = fingerprint_of({"ffmpeg": content})
    tools[0]["description"] += "."
    assert fingerprint_of({"ffmpeg": content}) != before


def test_the_note_names_the_gap_and_the_model_to_pull() -> None:
    assert note_for(None, 3, "knaif-qwen3-4b-v2", pull="knaif-made-up-model") == (
        "note: knaif-qwen3-4b-v2 was trained for an earlier prompt than this knaif serves "
        "(generation 3); results may be worse. The model for it: knaif-made-up-model."
    )
    assert note_for(3, 3, "knaif-made-up-model", pull="knaif-made-up-model") is None
    assert note_for(2, 3, "x", pull="y") == (
        "note: x was trained for an earlier prompt than this knaif serves (generation 3); "
        "results may be worse. The model for it: y."
    )
    # An older model with nothing to pull says nothing: a release that serves the shipped model
    # on a newer prompt must not warn on every run until the model for it exists (owner,
    # 2026-10-26). A model trained for a newer prompt than this knaif still says so (upgrade).
    assert note_for(2, 3, "x", pull=None) is None
    assert note_for(None, 3, "x", pull=None) is None
    assert "a newer prompt" in note_for(4, 3, "x", pull=None)


def test_the_cli_notes_a_model_from_before_this_generation() -> None:
    """`knaif-cli run` prints the note for a models.yaml model whose `trained_for` is not the
    declared generation, names the model that is, and stays quiet for a matching one or a model
    models.yaml does not know."""
    from knaif.app import _trained_for_note

    gen = declared().generation
    registry = {
        "default": "old",
        "models": {
            "old": {"backend": "llama_cpp", "options": {}},
            "fresh": {"backend": "llama_cpp", "options": {}, "trained_for": gen},
        },
    }
    assert _trained_for_note(None, None, registry) == note_for(None, gen, "old", pull="fresh")
    assert _trained_for_note("fresh", None, registry) is None
    assert _trained_for_note("unknown", None, registry) is None
    assert _trained_for_note(None, "fresh", registry) is None  # the skill's recommendation
    # No model for this generation in models.yaml: the shipped model runs without a note.
    only_old = {"default": "old", "models": {"old": registry["models"]["old"]}}
    assert _trained_for_note(None, None, only_old) is None


def test_no_published_model_claims_a_future_generation() -> None:
    gen = declared().generation
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for name, spec in manifest["models"].items():
        trained_for = spec.get("trained_for")
        if trained_for is not None:
            assert isinstance(trained_for, int) and 1 <= trained_for <= gen, name
