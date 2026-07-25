"""LICENSE and NOTICE must travel inside the published artifacts, not just be linked.

Apache-2.0 §4(a) requires giving every recipient a copy of the License, and §4(d) requires
passing along the NOTICE attribution. A README link does not satisfy either — the files
have to be *in* the wheel and sdist.

setuptools resolves ``license-files`` relative to the directory holding pyproject.toml
(``python/core/``), and PEP 639 forbids escaping it with ``..``, so the repo-root originals
are mirrored there. These guards keep the copies byte-identical and keep the declaration
from being dropped — the same arrangement as core_tools.yaml in test_runtime_data.py.
Regenerate with ``just sync-license``.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    # tomllib is stdlib only from 3.11, but requires-python declares a 3.10 floor. An
    # unconditional import makes the whole suite fail to *collect* on the oldest supported
    # interpreter, so the dev extra supplies the backport there.
    import tomli as tomllib

PYPROJECT = Path("python/core/pyproject.toml")
PAIRS = [
    (Path("LICENSE"), Path("python/core/LICENSE")),
    (Path("NOTICE"), Path("python/core/NOTICE")),
]


def test_packaged_license_files_exist():
    for _, packaged in PAIRS:
        assert packaged.exists(), f"{packaged} missing — run `just sync-license`"


def test_packaged_license_files_match_repo_root():
    for canonical, packaged in PAIRS:
        assert packaged.read_text(encoding="utf-8") == canonical.read_text(
            encoding="utf-8"
        ), f"{packaged.name} drift — run `just sync-license`"


def test_pyproject_declares_license_files():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = data["project"].get("license-files")
    assert declared is not None, "license-files missing: artifacts would ship no LICENSE"
    assert set(declared) == {"LICENSE", "NOTICE"}


def test_build_requires_a_pep639_capable_setuptools():
    """The SPDX license string and license-files both need setuptools >= 77.0.3.

    Older setuptools rejects the config outright ("project.license must be valid exactly
    by one definition"), so a build at the declared floor would fail.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires = data["build-system"]["requires"]
    setuptools_req = next(r for r in requires if r.startswith("setuptools"))
    floor = setuptools_req.split(">=")[1].strip()
    assert tuple(int(p) for p in floor.split(".")) >= (77, 0, 3), setuptools_req
