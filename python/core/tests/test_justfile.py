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


def test_web_build_invokes_mkdocs_as_python_module() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "uv run python -m mkdocs build -f site/mkdocs.yml" in text
    assert "uv run mkdocs build -f site/mkdocs.yml" not in text


def test_gpu_check_uses_module_not_inline_script() -> None:
    text = JUSTFILE.read_text(encoding="utf-8")

    assert "uv run -m knaif._gpu_check" in text
    assert "python -c" not in text
