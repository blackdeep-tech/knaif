"""A published artifact must not carry the builder's home directory (and so their username).

Found 2026-09-27: every Windows 1.1.0 binary embedded `C:\\Users\\<name>\\.cargo\\registry\\...` about
1,200 times, as Rust panic locations and C `__FILE__` strings from crates built out of the cargo
registry. The Linux artifacts, built in a container, carried none. `scripts/check_no_local_paths.py`
is the guard `package.sh` runs on every staged tree; `scripts/path_hygiene.sh` is what keeps a
Windows build from producing the paths in the first place.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(".").resolve()


def _load():
    path = ROOT / "scripts" / "check_no_local_paths.py"
    spec = importlib.util.spec_from_file_location("check_no_local_paths", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_no_local_paths"] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


# ── the guard ──────────────────────────────────────────────────────────────────────────────


def test_a_home_directory_is_found_in_either_slash_style_and_any_case() -> None:
    forbid = ["C:\\Users\\alice"]
    for blob in (
        b"\x00panic at C:\\Users\\alice\\.cargo\\registry\\src\\x.rs\x00",
        b"\x00c:/users/Alice/.cargo/registry/src/x.rs\x00",
        b"\x00C:\\\\Users\\\\alice\\\\.cargo\x00",  # an escaped string literal
    ):
        assert guard.hits(blob, forbid) == 1, blob


def test_a_different_user_or_a_longer_name_is_not_a_hit() -> None:
    forbid = ["C:\\Users\\alice"]
    assert guard.hits(b"C:\\Users\\bob\\.cargo", forbid) == 0
    assert guard.hits(b"C:\\Users\\alicexyz\\.cargo", forbid) == 0


def test_a_linux_home_is_forbidden_but_a_container_root_is_not() -> None:
    assert guard.forbidden_prefixes(home="/home/alice", windows=False) == ["/home/alice"]
    assert guard.forbidden_prefixes(home="/root", windows=False) == []


def test_the_cli_fails_on_a_staged_file_with_the_home_directory(tmp_path: Path) -> None:
    (tmp_path / "ok.dll").write_bytes(b"nothing to see")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bad.dll").write_bytes(b"x C:\\Users\\alice\\.cargo\\registry y")

    assert guard.main([str(tmp_path), "--forbid", "C:\\Users\\alice"]) == 1
    assert guard.main([str(tmp_path), "--forbid", "C:\\Users\\bob"]) == 0


def test_package_runs_the_guard_on_what_it_staged() -> None:
    text = (ROOT / "installers" / "package.sh").read_text(encoding="utf-8")
    assert "check_no_local_paths.py" in text


# ── the Windows build flags ────────────────────────────────────────────────────────────────


def _hygiene(cargo_home: str, root: str) -> dict[str, str]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    script = (ROOT / "scripts" / "path_hygiene.sh").as_posix()
    out = subprocess.run(
        [bash, "-c", f'source "{script}"; path_hygiene_env "$1" "$2"', "_", cargo_home, root],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    env = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition("=")
        env[key] = value
    return env


def test_rust_paths_are_remapped_for_the_cargo_home_and_the_checkout() -> None:
    env = _hygiene("C:\\Users\\alice\\.cargo", "C:\\src\\knaif")
    flags = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    assert "--remap-path-prefix=C:\\Users\\alice\\.cargo=/cargo" in flags
    assert "--remap-path-prefix=C:\\src\\knaif=/knaif" in flags


def test_c_cxx_and_cuda_file_macros_are_trimmed_for_both() -> None:
    env = _hygiene("C:\\Users\\alice\\.cargo", "C:\\src\\knaif")
    for var in ("CFLAGS", "CXXFLAGS"):
        assert "/d1trimfile:C:\\Users\\alice\\.cargo" in env[var].split()
        assert "/d1trimfile:C:\\src\\knaif" in env[var].split()
    assert "-Xcompiler=/d1trimfile:C:\\Users\\alice\\.cargo" in env["CUDAFLAGS"].split()


def test_a_path_with_a_space_is_refused_rather_than_split() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    script = (ROOT / "scripts" / "path_hygiene.sh").as_posix()
    out = subprocess.run(
        [
            bash,
            "-c",
            f'source "{script}"; path_hygiene_env "$1" "$2"',
            "_",
            "C:\\Users\\Al Ice\\.cargo",
            "C:\\src\\knaif",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode != 0
    assert "space" in out.stderr


def test_the_windows_build_applies_the_hygiene_flags() -> None:
    text = (ROOT / "scripts" / "build_native_kind.sh").read_text(encoding="utf-8")
    assert "path_hygiene.sh" in text and "path_hygiene_env" in text
