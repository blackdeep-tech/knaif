"""Every model name a user reads in something knaif ships must be the manifest's, not a hand copy.

The 1.2.0 release candidate passed every evaluation and still shipped `knaif-qwen3-4b-v1` in four
places after the v2 promotion: the installer's default-on download task, its post-install text,
the artifacts' README quick start and the CLI's `--model` help — plus a NOTICE that attributed
only the v1 models. Each was a hand-typed copy of "the current model" with nothing tying it to
`contracts/models/model-manifest.yaml` (found by the owner in a manual install, 2026-09-29).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
MANIFEST = yaml.safe_load(
    (REPO / "contracts/models/model-manifest.yaml").read_text(encoding="utf-8")
)
RECOMMENDED = MANIFEST["recommendations"]
MODEL_NAME = re.compile(r"knaif-qwen3-[0-9.]+b-v[0-9]+")


def _names(text: str) -> set[str]:
    return set(MODEL_NAME.findall(text))


def _iss_define(name: str) -> str:
    text = (REPO / "installers/windows/knaif.iss").read_text(encoding="utf-8")
    match = re.search(rf'#define {name} "([^"]+)"', text)
    assert match, f"knaif.iss defines no {name}"
    return match.group(1)


def test_the_installer_downloads_the_recommended_model() -> None:
    desktop = RECOMMENDED["desktop"]
    assert _iss_define("DefaultModel") == desktop
    assert _iss_define("DefaultModelFile") == MANIFEST["models"][desktop]["file"]


def test_the_post_install_text_names_only_recommended_models() -> None:
    text = (REPO / "installers/windows/postinstall.txt").read_text(encoding="utf-8")
    assert _names(text) <= {RECOMMENDED["desktop"], RECOMMENDED["mobile"]}


def test_the_artifact_readme_takes_the_model_from_the_manifest() -> None:
    """package.sh writes README.txt into every artifact: it must not type a model name."""
    text = (REPO / "installers/package.sh").read_text(encoding="utf-8")
    assert not _names(text), f"package.sh hard-codes {sorted(_names(text))}"


def test_the_cli_help_names_only_recommended_models() -> None:
    """Strings compiled into the binary (everything before the test module)."""
    source = (REPO / "apps/cli/src/main.rs").read_text(encoding="utf-8")
    shipped = source.split("#[cfg(test)]", 1)[0]
    assert _names(shipped) <= {RECOMMENDED["desktop"], RECOMMENDED["mobile"]}


def test_notice_attributes_every_published_model() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    missing = [
        name for name in MANIFEST["models"] if name.startswith("knaif-") and name not in notice
    ]
    assert not missing, f"NOTICE does not attribute {missing}"
