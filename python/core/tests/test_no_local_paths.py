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


def test_a_checkout_inside_the_home_directory_is_named_as_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # llama.cpp compiles its backend folder (under target/) into the binaries as a value, which no
    # remap reaches. A Mac checkout usually lives under ~, so "use the build script" misleads there.
    home = tmp_path / "alice"
    checkout = home / "src" / "knaif"
    staged = checkout / "dist" / "staging"
    staged.mkdir(parents=True)
    (staged / "knaif").write_bytes(
        f"x {checkout}/target/release-metal/build/out/backends y".encode()
    )

    monkeypatch.chdir(checkout)
    assert guard.main([str(staged), "--forbid", str(home)]) == 1
    assert "checkout is inside" in capsys.readouterr().out

    monkeypatch.chdir(tmp_path)
    assert guard.main([str(staged), "--forbid", str(home)]) == 1
    assert "checkout is inside" not in capsys.readouterr().out


def test_package_runs_the_guard_on_what_it_staged() -> None:
    text = (ROOT / "installers" / "package.sh").read_text(encoding="utf-8")
    assert "check_no_local_paths.py" in text


# ── the Windows build flags ────────────────────────────────────────────────────────────────


def _hygiene(cargo_home: str, root: str, *compiler: str) -> dict[str, str]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    script = (ROOT / "scripts" / "path_hygiene.sh").as_posix()
    out = subprocess.run(
        [bash, "-c", f'source "{script}"; path_hygiene_env "$@"', "_", cargo_home, root, *compiler],
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


# ── the macOS build flags ──────────────────────────────────────────────────────────────────


def test_clang_maps_the_file_macros_for_both_and_remaps_rust_the_same_way() -> None:
    env = _hygiene("/Users/alice/.cargo/", "/Users/alice/src/knaif", "clang")
    flags = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    assert "--remap-path-prefix=/Users/alice/.cargo=/cargo" in flags
    assert "--remap-path-prefix=/Users/alice/src/knaif=/knaif" in flags
    for var in ("CFLAGS", "CXXFLAGS"):
        assert "-ffile-prefix-map=/Users/alice/.cargo=/cargo" in env[var].split()
        assert "-ffile-prefix-map=/Users/alice/src/knaif=/knaif" in env[var].split()
    assert "CUDAFLAGS" not in env


def test_the_macos_build_applies_the_clang_hygiene_flags() -> None:
    text = (ROOT / "scripts" / "build_native_kind.sh").read_text(encoding="utf-8")
    assert 'path_hygiene_env "${CARGO_HOME:-$HOME/.cargo}" "$ROOT" clang' in text


# ── the repo itself ────────────────────────────────────────────────────────────────────────


def test_no_tracked_file_carries_this_machine_s_home_or_checkout() -> None:
    """Eval runs used to record absolute fixture and model paths (`C:/.../knaif/sandbox/...`,
    a home-directory model store), and the repo is public. Scrubbed to `<repo>` and `~` on
    2026-09-27. Checked against wherever this test runs, so no username is written down here."""
    home = Path.home()
    prefixes = [str(ROOT)] + ([str(home)] if str(home) not in ("/", "/root") else [])
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    bad = []
    for name in filter(None, files):
        path = ROOT / name.decode()
        if not path.is_file() or path.suffix in {".png", ".jpg", ".ico", ".pdf", ".woff2", ".gguf"}:
            continue
        if n := guard.hits(path.read_bytes(), prefixes):
            bad.append(f"{n} {name.decode()}")
    assert not bad, "tracked files carry a local path (replace with <repo> or ~):\n" + "\n".join(
        bad
    )


def test_checkout_mode_also_forbids_the_repo_path(tmp_path: Path, monkeypatch) -> None:
    """The pre-commit hook form: several files, and the checkout's own path is forbidden too."""
    monkeypatch.chdir(tmp_path)
    clean = tmp_path / "a.json"
    clean.write_text('{"p": "<repo>/sandbox/x.mp4"}', encoding="utf-8")
    leaky = tmp_path / "b.json"
    leaky.write_text(f'{{"p": "{tmp_path.as_posix()}/sandbox/x.mp4"}}', encoding="utf-8")

    nobody = "C:/Users/nobody"
    assert guard.main(["--forbid", nobody, str(clean), str(leaky)]) == 0
    assert guard.main(["--forbid", nobody, "--checkout", str(clean)]) == 0
    assert guard.main(["--forbid", nobody, "--checkout", str(clean), str(leaky)]) == 1


def test_a_commit_hook_runs_the_checkout_check() -> None:
    text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "check_no_local_paths.py --checkout" in text
