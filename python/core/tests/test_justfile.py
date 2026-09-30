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


def _check_contracts_recipe() -> str:
    """The body of the `check-contracts:` recipe, up to the next top-level item."""
    text = JUSTFILE.read_text(encoding="utf-8")
    start = text.index("\ncheck-contracts:\n")
    rest = text[start + 1 :]
    end = re.search(r"\n(?=[^\s#])", rest)
    return rest[: end.start()] if end else rest


def _parity_test_files() -> set[str]:
    return {p.name for p in (ROOT / "python" / "core" / "tests").glob("test_*_parity.py")}


def test_check_contracts_runs_every_parity_test() -> None:
    """`check-contracts` enumerates its Python test files by hand, so a new contract test
    is silently not run until someone remembers to edit this recipe.

    The Rust half has no such hazard — `cargo test -p knaif-core --test parity` runs the
    whole binary, so a new `#[test]` there is picked up for free. That asymmetry bit during
    the 2026-09-15 arg-gate port: the Rust case ran on its own, the Python one did not.

    Scoped to the `*_parity.py` naming family, which is exactly the L1/L2 contract
    consumers. The recipe legitimately lists other checks too (generation settings, the
    scoring contract, outcomes); those are not constrained here.
    """
    recipe = _check_contracts_recipe()
    missing = sorted(name for name in _parity_test_files() if name not in recipe)
    assert not missing, (
        f"check-contracts does not run {missing} — a contract test nobody runs is a "
        f"contract nobody checks. Add it to the recipe in justfile."
    )


def test_the_parity_guard_would_notice_an_omission() -> None:
    """The guard above is only worth having if it can fail; prove it on a doctored recipe."""
    recipe = _check_contracts_recipe()
    files = _parity_test_files()
    assert files, "no *_parity.py tests found — the guard would vacuously pass"
    victim = sorted(files)[0]
    doctored = recipe.replace(f"python/core/tests/{victim}", "")
    assert victim not in doctored, f"{victim} was not actually removed from the copy"
