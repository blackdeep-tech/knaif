"""Guard `scripts/site_data.py` and the committed `site/data/site-data.json`.

The JSON is committed so the websites build without a Python environment, which means it
can silently fall out of step with the skills it describes — the same failure mode
`just sync-runtime` exists to prevent for `contracts/runtime/core_tools.yaml`. The drift
test below is the whole reason committing it is safe.

Following `test_pe_imports.py`: assert the extractor **fails** on the cases it claims to
catch, not merely that it succeeds on a healthy tree. The three failure modes that would
otherwise reach a published page are a skill with no end-user copy, a tool advertised with
no class behind it, and a stale skill resurfacing in the catalog.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "site_data.py"
DATA = ROOT / "site" / "data" / "site-data.json"


def _load():
    spec = importlib.util.spec_from_file_location("site_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sd = _load()


# --------------------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------------------


def test_committed_site_data_is_up_to_date():
    """Regenerating must reproduce the committed file byte for byte.

    If this fails, run `just site-data` and commit the result.
    """
    assert DATA.exists(), f"{DATA} is missing — run `just site-data`"
    committed = DATA.read_text(encoding="utf-8")
    regenerated = sd.render(sd.build())
    assert (
        committed == regenerated
    ), "site/data/site-data.json is stale — run `just site-data` and commit the result."


def test_render_is_deterministic():
    """Two renders of one build must be identical, or the drift guard is noise."""
    data = sd.build()
    assert sd.render(data) == sd.render(data)


def test_committed_file_uses_lf_endings():
    """CRLF would make the drift guard fail for whoever is not on the OS that wrote it."""
    assert b"\r\n" not in DATA.read_bytes()


# --------------------------------------------------------------------------------------
# The failure modes that would otherwise reach a published page
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["title", "tagline", "category"])
def test_missing_display_copy_fails_the_build(missing):
    """A skill without end-user copy must fail, never render a degraded card.

    `display:` is optional to the runtime and required to publish. A silent fallback to
    name/description is how a new skill would ship with placeholder copy nobody noticed.
    """
    display = {"title": "X", "tagline": "Y", "category": "z"}
    del display[missing]
    with pytest.raises(sd.ExtractError) as exc:
        sd._display({"display": display}, "example")
    assert missing in str(exc.value)
    assert "TOOL_SCHEMA" in str(exc.value), "the error must say where the contract is documented"


def test_absent_display_block_fails_the_build():
    with pytest.raises(sd.ExtractError):
        sd._display({}, "example")


def test_every_published_skill_declares_display_copy():
    for skill in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        for key in ("title", "tagline", "category"):
            assert skill.get(key), f"{skill['name']} is published without display.{key}"


def test_published_tools_are_all_backed_by_a_class():
    """The site must not advertise a tool the runtime would reject.

    This is why the extractor goes through `Skill.load` rather than `load_registry` alone:
    only the loader proves a Step/Intent class exists for each visible name.
    """
    from knaif.registry import load_registry
    from knaif.skill import Skill

    for entry in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        skill = Skill.load(ROOT / "skills" / entry["name"])
        backed = set(skill.tool_map or {})
        registry = load_registry(skill.tools_yaml_path)
        for tool in entry["tools"]:
            assert tool["name"] in backed, f"{entry['name']}.{tool['name']} has no class"
            assert not registry[
                tool["name"]
            ].internal, f"{entry['name']}.{tool['name']} is internal and must not be published"


def test_internal_and_core_tools_are_not_published():
    """Internal steps are emitted only by code, and core control tools belong to no skill."""
    from knaif.core_tools import CORE_TOOL_DEFS

    for entry in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        names = {t["name"] for t in entry["tools"]}
        assert not (
            names & set(CORE_TOOL_DEFS)
        ), f"{entry['name']} publishes a core control tool as if it were a capability"
        assert "resolve_inputs" not in names, f"{entry['name']} publishes an internal step"


def test_stale_skills_stay_off_the_catalog():
    """`io` is hidden from discovery; the website must not resurface what the runtime hides.

    Asserted through `status:`, not the name — the rule has to hold for the next stale
    skill too.
    """
    published = {s["name"] for s in json.loads(DATA.read_text(encoding="utf-8"))["skills"]}
    for skill_dir in (ROOT / "skills").iterdir():
        manifest_path = skill_dir / "skill.yaml"
        if not manifest_path.is_file():
            continue
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if manifest.get("status") == "stale":
            assert (
                skill_dir.name not in published
            ), f"{skill_dir.name} is status: stale but appears in the public catalog"


# --------------------------------------------------------------------------------------
# Catalog stage
#
# `status:` defaults to `active`, so without a *derived* stage a half-finished skill
# dropped into skills/ would advertise itself as production-ready — and nobody would have
# had to make a wrong decision for that to happen. These pin the derivation.
# --------------------------------------------------------------------------------------


def test_stage_is_derived_from_the_locked_acceptance_bar(tmp_path):
    """A skill with a snapshot is stable; one without is preview, not stable."""
    (tmp_path / "data").mkdir()
    assert sd._stage({}, tmp_path, "example") == "preview"

    (tmp_path / "data" / "eval_snapshot.json").write_text("{}", encoding="utf-8")
    assert sd._stage({}, tmp_path, "example") == "stable"


def test_declared_stage_overrides_the_derivation(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "eval_snapshot.json").write_text("{}", encoding="utf-8")
    manifest = {"display": {"stage": "preview"}}
    assert sd._stage(manifest, tmp_path, "example") == "preview"


def test_unknown_stage_fails_the_build(tmp_path):
    """A typo must not silently fall back to publishing."""
    with pytest.raises(sd.ExtractError) as exc:
        sd._stage({"display": {"stage": "beta"}}, tmp_path, "example")
    assert "beta" in str(exc.value)
    for valid in ("stable", "preview", "hidden"):
        assert valid in str(exc.value), "the error must list the accepted values"


def test_hidden_skills_need_no_catalog_copy(tmp_path):
    """Requiring display copy for something never shown would be busywork."""
    (tmp_path / "skill.yaml").write_text(
        "name: example\ndisplay:\n  stage: hidden\n", encoding="utf-8"
    )
    assert sd._stage({"display": {"stage": "hidden"}}, tmp_path, "example") == "hidden"


def test_published_stage_is_always_a_visible_one():
    for entry in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        assert entry["stage"] in (
            "stable",
            "preview",
        ), f"{entry['name']} is published with stage {entry['stage']!r}"


def test_stable_skills_have_a_locked_snapshot():
    """The catalog's quality claim has to be backed by the thing it claims."""
    for entry in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        if entry["stage"] != "stable":
            continue
        snapshot = ROOT / "skills" / entry["name"] / "data" / "eval_snapshot.json"
        declared = (
            yaml.safe_load((ROOT / "skills" / entry["name"] / "skill.yaml").read_text("utf-8"))
            or {}
        )
        if (declared.get("display") or {}).get("stage"):
            continue  # explicitly overridden — a deliberate call, not a derivation
        assert (
            snapshot.is_file()
        ), f"{entry['name']} is shown as stable but has no locked eval snapshot"


# --------------------------------------------------------------------------------------
# Content the sites depend on
# --------------------------------------------------------------------------------------


def test_examples_come_from_prompt_yaml_and_carry_their_tool():
    """Catalog cards need action-producing utterances, not clarify/reject rows."""
    for entry in json.loads(DATA.read_text(encoding="utf-8"))["skills"]:
        examples = entry["examples"]
        assert examples, f"{entry['name']} has no example utterances"
        published = {t["name"] for t in entry["tools"]}
        actionable = [e for e in examples if e["tool"] in published]
        assert len(actionable) >= 3, (
            f"{entry['name']} has {len(actionable)} action-producing examples; "
            f"catalog cards need at least 3"
        )


def test_platform_matrix_is_carried_and_has_a_supported_platform():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    platforms = data["platforms"]["platforms"]
    assert any(p["status"] == "supported" for p in platforms)
    for entry in platforms:
        assert entry.get("id") and entry.get("name") and entry.get("status")


def test_exactly_one_default_model_is_published():
    models = json.loads(DATA.read_text(encoding="utf-8"))["models"]
    assert sum(1 for m in models if m["default"]) == 1


def test_model_urls_and_hashes_are_not_copied_into_the_site():
    """The site never serves a model download; a second copy of a hash is a second thing
    that can rot. They stay in contracts/models/model-manifest.yaml."""
    for model in json.loads(DATA.read_text(encoding="utf-8"))["models"]:
        assert "url" not in model and "sha256" not in model
