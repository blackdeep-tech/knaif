"""A release must not recommend a model its users cannot download.

The manifest ships *inside* the artifact and binds a knaif release to the model it was tested
against, so whatever `recommendations` names here is what every install will try to use.

This became load-bearing with default-model auto-select (plan B1): the recommended model is now
the **default path**, not just the answer to an explicit `knaif models pull`. If it were shipped
with `url: TODO`, first run would fail for every user who didn't pass `--model` — auto-select
offers the download, the pull aborts with "no download URL", and there is no fallback. Before B1
that was a harmless placeholder; now it is a shipped outage, so assert it at build time.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(".").resolve()
MANIFEST = ROOT / "contracts" / "models" / "model-manifest.yaml"

# Mirrors `is_real` in native/crates/knaif-models/src/manifest.rs: absent, empty, or the literal
# "TODO" placeholder all mean "not yet published".
PLACEHOLDERS = {None, "", "TODO"}


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _recommended_names() -> list[tuple[str, str]]:
    recs = _manifest().get("recommendations") or {}
    return [(surface, name) for surface, name in recs.items() if name]


def test_manifest_has_recommendations() -> None:
    # Guard the guard: if recommendations vanished, the checks below would pass vacuously.
    assert _recommended_names(), "manifest declares no recommendations"


def test_recommended_models_exist_in_the_manifest() -> None:
    models = _manifest().get("models") or {}
    for surface, name in _recommended_names():
        assert name in models, f"recommendations.{surface} = {name!r}, which has no models: entry"


@pytest.mark.parametrize("field", ["url", "sha256"])
def test_recommended_models_are_published(field: str) -> None:
    models = _manifest().get("models") or {}
    for surface, name in _recommended_names():
        value = (models.get(name) or {}).get(field)
        assert value not in PLACEHOLDERS, (
            f"recommendations.{surface} = {name!r} has {field}={value!r} — that model is not "
            f"published, so auto-select (B1) would fail on first run for anyone without --model. "
            f"Publish it (scripts/publish_model.py) or recommend a model that is hosted."
        )


def test_recommended_models_pin_an_immutable_url() -> None:
    # A `main`-style URL can move under a fixed sha256 and silently break `verify`; the publish
    # script pins a commit SHA precisely to prevent that.
    models = _manifest().get("models") or {}
    for surface, name in _recommended_names():
        url = (models.get(name) or {}).get("url") or ""
        if "huggingface.co" in url:
            assert (
                "/resolve/main/" not in url
            ), f"recommendations.{surface} = {name!r} pins a moving `main` URL: {url}"
