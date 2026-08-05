import re
from pathlib import Path

ROOT = Path(".").resolve()
JUSTFILE = ROOT / "justfile"


def test_justfile_does_not_force_platform_shell() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "set shell" not in text


def test_init_bootstraps_without_python_helper() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "init:\n    uv venv\n    just install" in text
    assert "scripts/dev.py init" not in text


def test_platform_sensitive_recipes_delegate_to_private_helpers() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "clean: _clean" in text
    assert "freeze: _freeze" in text
    assert "[windows]\n_clean:" in text
    assert "[unix]\n_clean:" in text
    assert "[windows]\n_freeze:" in text
    assert "[unix]\n_freeze:" in text
    assert "scripts/dev.py" not in text


def test_mkdocs_site_is_fully_removed() -> None:
    """The mkdocs site was replaced by two Astro sites (docs/plans/2026-08-04-website-split.md).

    Replaces the old guard that mkdocs was invoked as a module rather than a console
    script. A leftover recipe would fail on a `site/mkdocs.yml` that no longer exists.
    """
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "mkdocs" not in text
    assert "web-build" not in text


def test_site_recipes_use_pnpm_not_npm() -> None:
    """npm ignores `pnpm-workspace.yaml` entirely.

    pnpm reads its workspace members from that file, not from a `workspaces` field in
    package.json — so an `npm` invocation here resolves no workspaces and silently builds
    only the root, which is the single most common way a pnpm monorepo breaks.
    """
    text = JUSTFILE.read_text(encoding="utf-8")
    assert "pnpm --dir site" in text

    # Checked over command lines only, and word-boundary matched. Two traps here:
    # a naive `"npm" not in text` is always false because every correct `pnpm` contains
    # it, and scanning comments too would flag prose that says "not npm".
    commands = [line for line in text.splitlines() if line.startswith((" ", "\t")) and line.strip()]
    offenders = [line.strip() for line in commands if re.search(r"(?<!p)\bnpm\b", line)]
    assert not offenders, f"bare npm invocation(s): {offenders}"


def test_site_build_matches_how_amplify_builds() -> None:
    """`--frozen-lockfile` is what CI runs; building locally without it hides lockfile drift."""
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "pnpm --dir site install --frozen-lockfile" in text


def test_gpu_check_uses_module_not_inline_script() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "uv run -m knaif._gpu_check" in text
    assert "python -c" not in text
