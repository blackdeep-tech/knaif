"""Guard the macOS signing pipeline and the Homebrew formula (installers/macos/).

F2 asks for the release order to be "encoded in a script so it cannot be got wrong by hand". That
property is testable without a Mac: every Apple tool the scripts call (codesign, xcrun, spctl,
pkgbuild, productbuild, zip) is replaced by a fake that logs its arguments, and the log is read
back in order. What the fakes cannot prove — that Apple accepts the result — is the Mac's job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
MACOS = REPO / "installers" / "macos"
TEAM = "ABCDE12345"


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def _posix(path: Path) -> str:
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = f"/{text[0].lower()}{text[2:]}"
    return text


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _macho(path: Path, filetype: int) -> None:
    """A header-only arm64 Mach-O: enough for `check_macho_deps.py --list` to classify it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, filetype, 0, 0, 0, 0))


# Each fake appends one line to $FAKE_LOG, so the log is the order things happened in.
FAKES = {
    "codesign": r"""
file="${@: -1}"
case "$*" in
  *--sign*) echo "codesign sign $(basename "$file") $*" >> "$FAKE_LOG" ;;
  *--verify*) exit 0 ;;
  *-dv*)
    # A Developer ID signature whose CDHash is the file name in hex, so the log can match it.
    h="$(printf '%s' "$(basename "$file")" | od -An -tx1 | tr -d ' \n')"
    cat >&2 <<EOF
CodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+0 location=embedded
CDHash=$h
Authority=Developer ID Application: Example ($FAKE_TEAM)
Timestamp=1 Oct 2026 at 10:00:00
TeamIdentifier=$FAKE_TEAM
EOF
    ;;
  *-R=notarized*) echo "codesign check-notarization $(basename "$file")" >> "$FAKE_LOG" ;;
esac
exit 0
""",
    "xcrun": r"""
case "$1 $2" in
  "notarytool submit")
    echo "notarytool submit $(basename "$3")" >> "$FAKE_LOG"
    echo '{"id": "job-1", "status": "Accepted"}'
    ;;
  "notarytool log")
    echo "notarytool log $3" >> "$FAKE_LOG"
    "$FAKE_PY" - "$FAKE_CDHASHES" "${@: -1}" "$FAKE_STATUS" <<'EOF'
import json, sys
hashes = json.load(open(sys.argv[1]))
log = {"status": sys.argv[3], "issues": None,
       "ticketContents": [{"path": n, "cdhash": h} for n, h in hashes.items()]}
json.dump(log, open(sys.argv[2], "w"))
EOF
    ;;
  "stapler staple" | "stapler validate") echo "stapler $2 $(basename "$3")" >> "$FAKE_LOG" ;;
esac
exit 0
""",
    "spctl": 'echo "spctl $(basename "${@: -1}")" >> "$FAKE_LOG"\n',
    "zip": 'echo "zip $(basename "$2")" >> "$FAKE_LOG"\ntouch "$2"\n',
    "pkgbuild": 'echo "pkgbuild $(basename "${@: -1}")" >> "$FAKE_LOG"\ntouch "${@: -1}"\n',
    "productbuild": 'echo "productbuild $(basename "${@: -1}") $*" >> "$FAKE_LOG"\n'
    'touch "${@: -1}"\n',
}


@pytest.fixture()
def apple(tmp_path: Path):
    """Fake Apple tools on PATH and a staged tree with two libraries and the exe."""
    fakes = tmp_path / "fakes"
    for name, body in FAKES.items():
        _exe(fakes / name, body)
    dist = tmp_path / "dist"
    stage = dist / "staging" / "knaif-9.9.9-macos-arm64"
    _macho(stage / "bin" / "knaif", 0x2)
    _macho(stage / "bin" / "libllama.dylib", 0x6)
    _macho(stage / "bin" / "libggml-metal.so", 0x8)
    for d in ("contracts", "licenses", "skills/ffmpeg", "skills/documents"):
        (stage / d).mkdir(parents=True)
    for f in ("LICENSE", "NOTICE", "README.txt"):
        (stage / f).write_text(f)
    log = tmp_path / "log"
    env = {
        **os.environ,
        "PATH": ":".join(
            [
                _posix(fakes),
                _posix(Path(sys.executable).parent),
                _posix(Path(_bash()).parent),
                "/usr/bin",
                "/bin",
            ]
        ),
        "FAKE_LOG": log.as_posix(),
        "FAKE_TEAM": TEAM,
        "FAKE_PY": _posix(Path(sys.executable)),
        "FAKE_STATUS": "Accepted",
        "FAKE_CDHASHES": (dist / "notary" / f"{stage.name}.cdhashes.json").as_posix(),
        "KNAIF_DIST_DIR": dist.as_posix(),
        "KNAIF_SIGN_IDENTITY": f"Developer ID Application: Example ({TEAM})",
        "KNAIF_INSTALLER_IDENTITY": f"Developer ID Installer: Example ({TEAM})",
        "KNAIF_TEAM_ID": TEAM,
        "KNAIF_NOTARY_PROFILE": "knaif-notary",
    }
    return stage, env, log


# check_macos_signing.py runs `codesign` itself, and Windows Python cannot exec a bash fake.
execs_fakes_from_python = pytest.mark.skipif(
    os.name == "nt", reason="the signature check spawns codesign from Python; no bash fakes there"
)


def _run(script: str, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash(), (MACOS / script).as_posix(), *args], capture_output=True, text=True, env=env
    )


@execs_fakes_from_python
def test_sign_signs_inside_out_with_the_hardened_runtime(apple) -> None:
    stage, env, log = apple
    proc = _run("sign.sh", [stage.as_posix()], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    signed = [line.split()[2] for line in log.read_text().splitlines()]
    assert signed[-1] == "knaif" and set(signed) == {"knaif", "libllama.dylib", "libggml-metal.so"}
    first = log.read_text().splitlines()[0]
    assert "--options runtime" in first and "--timestamp" in first and "--deep" not in first
    hashes = json.loads(Path(env["FAKE_CDHASHES"]).read_text())
    assert set(hashes) == {"knaif", "libllama.dylib", "libggml-metal.so"}


def test_sign_refuses_to_run_without_an_identity(apple) -> None:
    stage, env, log = apple
    env = {k: v for k, v in env.items() if k != "KNAIF_SIGN_IDENTITY"}
    proc = _run("sign.sh", [stage.as_posix()], env)
    assert proc.returncode != 0 and "KNAIF_SIGN_IDENTITY" in proc.stderr
    assert not log.exists()


@execs_fakes_from_python
def test_release_runs_f2_in_order(apple) -> None:
    stage, env, log = apple
    proc = _run("release.sh", ["--stage", stage.as_posix()], env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    steps = [" ".join(line.split()[:3]) for line in log.read_text().splitlines()]

    def at(prefix: str) -> int:
        return next(i for i, s in enumerate(steps) if s.startswith(prefix))

    zip_name, pkg_name = f"{stage.name}.zip", f"{stage.name}.pkg"
    last_sign = max(i for i, s in enumerate(steps) if s.startswith("codesign sign"))
    # Sign everything before anything is archived; the zip is rebuilt from the signed tree.
    assert last_sign < at(f"zip {zip_name}") < at(f"notarytool submit {zip_name}")
    # The .pkg is built after the zip is notarized, then notarized, stapled and validated.
    assert at(f"notarytool submit {zip_name}") < at("pkgbuild") < at(f"productbuild {pkg_name}")
    assert at(f"productbuild {pkg_name}") < at(f"notarytool submit {pkg_name}")
    assert at(f"notarytool submit {pkg_name}") < at(f"stapler staple {pkg_name}")
    assert at(f"stapler staple {pkg_name}") < at(f"stapler validate {pkg_name}")
    # Gatekeeper-style checks come last (F7). A .zip is never stapled (it cannot be).
    assert at(f"stapler validate {pkg_name}") < at("codesign check-notarization knaif")
    assert steps[-1] == f"spctl {pkg_name}"
    assert f"stapler staple {zip_name}" not in steps
    # The package is Installer-signed, with a timestamp.
    product = next(line for line in log.read_text().splitlines() if line.startswith("productbuild"))
    assert "--sign Developer ID Installer: Example" in product and "--timestamp" in product
    # SHA256SUMS is not this script's to write (RELEASE.md: once, over the complete set).
    assert not list(Path(env["KNAIF_DIST_DIR"]).rglob("SHA256SUMS*"))


@execs_fakes_from_python
def test_a_rejected_notarization_stops_before_stapling(apple) -> None:
    stage, env, log = apple
    env = {**env, "FAKE_STATUS": "Invalid"}
    proc = _run("release.sh", ["--stage", stage.as_posix()], env)
    assert proc.returncode != 0
    assert "stapler" not in log.read_text()
    assert "pkgbuild" not in log.read_text(), "a rejected .zip must stop the release"


def test_notarize_needs_credentials(apple, tmp_path: Path) -> None:
    _stage, env, _log = apple
    env = {k: v for k, v in env.items() if k != "KNAIF_NOTARY_PROFILE"}
    target = tmp_path / "x.zip"
    target.write_text("zip")
    hashes = tmp_path / "h.json"
    hashes.write_text("{}")
    proc = _run("notarize.sh", [target.as_posix(), hashes.as_posix()], env)
    assert proc.returncode == 2 and "KNAIF_NOTARY_PROFILE" in proc.stderr


# -- the Homebrew formula (D19) -----------------------------------------------------------------


def _skills() -> list[dict]:
    out = []
    for path in sorted((REPO / "skills").glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("status") != "stale":
            out.append(data)
    return out


def _external_tools() -> list[dict]:
    return [t for s in _skills() for t in s.get("dependencies", {}).get("external_tools", [])]


@pytest.fixture()
def formula(tmp_path: Path) -> tuple[str, bytes]:
    zip_path = tmp_path / "knaif-9.9.9-macos-arm64.zip"
    zip_path.write_bytes(b"the notarized archive")
    proc = subprocess.run(
        [_bash(), (MACOS / "homebrew" / "render-formula.sh").as_posix(), zip_path.as_posix()],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout, zip_path.read_bytes()


def test_the_formula_names_the_release_and_its_checksum(formula) -> None:
    text, data = formula
    assert "@" not in re.sub(r"#.*", "", text)
    assert "/download/v9.9.9/knaif-9.9.9-macos-arm64.zip" in text
    assert f'sha256 "{hashlib.sha256(data).hexdigest()}"' in text
    manifest = yaml.safe_load(
        (REPO / "contracts/models/model-manifest.yaml").read_text(encoding="utf-8")
    )
    assert f"knaif models pull {manifest['recommendations']['desktop']}" in text
    assert "depends_on arch: :arm64" in text and "depends_on macos: :monterey" in text


def test_the_formula_depends_on_exactly_the_required_tools(formula) -> None:
    # D19: a required tool is a dependency; an optional one is a caveat, never a dependency.
    text, _ = formula
    deps = set(re.findall(r'^\s*depends_on "([^"]+)"', text, re.M))
    required = {t["macos"]["brew"] for t in _external_tools() if t.get("required")}
    assert deps == required
    for tool in _external_tools():
        if tool.get("required"):
            continue
        mac = tool["macos"]
        cmd = f"brew install --cask {mac['brew']}" if mac.get("cask") else mac["brew"]
        assert cmd in text, f"{tool['name']} missing from the caveats"


def test_render_refuses_a_file_that_is_not_a_macos_release(tmp_path: Path) -> None:
    other = tmp_path / "knaif-9.9.9-linux-x64.tar.gz"
    other.write_bytes(b"x")
    proc = subprocess.run(
        [_bash(), (MACOS / "homebrew" / "render-formula.sh").as_posix(), other.as_posix()],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


# -- the clean room (E3/E4, D16), zip path, with the macOS tools faked --------------------------

ROOM_FAKES = {
    "sw_vers": 'echo "$FAKE_MACOS"\n',
    "xcode-select": "exit 2\n",
    "uuidgen": "echo 00000000-0000-0000-0000-000000000000\n",
    # Extended attributes as sidecar files, keyed by the path's checksum.
    "xattr": r"""
key() { printf '%s' "$1" | cksum | cut -d' ' -f1; }
case "$1" in
  -w) printf '%s' "$3" > "$FAKE_XATTRS/$(key "$4")" ;;
  -p) [ -e "$FAKE_XATTRS/$(key "$3")" ] ;;
esac
""",
    # Extract like Finder: every file inherits the archive's quarantine.
    "ditto": r"""
"$FAKE_PY" -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$3" "$4"
chmod +x "$4"/*/bin/*
if xattr -p com.apple.quarantine "$3"; then
  find "$4" -type f | while IFS= read -r f; do xattr -w com.apple.quarantine q "$f"; done
fi
""",
}

FAKE_KNAIF = r"""
case "$1" in
  --version) echo "knaif 9.9.9" ;;
  run)
    [ -e "$(dirname "$0")/libggml-metal.so" ] && echo "offloaded 37/37 layers to MTL" >&2
    [ "${FAKE_RUN_EXIT:-0}" = 0 ] || exit "$FAKE_RUN_EXIT"
    printf '%%PDF-1.7' > rotated.pdf
    ;;
esac
"""


@pytest.fixture()
def room(tmp_path: Path):
    """Returns (run(extra_env) -> (proc, results text, room dir))."""
    import zipfile

    fakes = tmp_path / "fakes"
    for name, body in ROOM_FAKES.items():
        _exe(fakes / name, body)
    (tmp_path / "xattrs").mkdir()
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copy(MACOS / "clean-room.sh", kit / "clean-room.sh")
    _exe(kit / "smoke.sh", "exit 0\n")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "sample.pdf").write_bytes(b"%PDF-1.7 sample")
    zpath = tmp_path / "knaif-9.9.9-macos-arm64.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("knaif-9.9.9-macos-arm64/bin/knaif", "#!/bin/bash\n" + FAKE_KNAIF)
        z.writestr("knaif-9.9.9-macos-arm64/bin/libggml-metal.so", "metal")
        z.writestr("knaif-9.9.9-macos-arm64/contracts/x.yaml", "x")
    (tmp_path / "home").mkdir()
    (tmp_path / "scratch").mkdir()
    # Where the room looks for brew. The real /opt/homebrew can't be faked through PATH, and the Mac
    # running these tests usually has one, so the room is pointed at an empty stand-in.
    (tmp_path / "brew-bin").mkdir()
    room_dir = tmp_path / "room"
    room_dir.mkdir()

    def run(**extra: str):
        env = {
            **os.environ,
            "PATH": ":".join(
                [
                    _posix(fakes),
                    _posix(Path(sys.executable).parent),
                    _posix(Path(_bash()).parent),
                    "/usr/bin",
                    "/bin",
                ]
            ),
            "HOME": tmp_path.joinpath("home").as_posix(),
            "TMPDIR": tmp_path.joinpath("scratch").as_posix(),
            "FAKE_MACOS": "12.7.6",
            "FAKE_XATTRS": tmp_path.joinpath("xattrs").as_posix(),
            "FAKE_PY": _posix(Path(sys.executable)),
            "KNAIF_ROOM_BREW_DIRS": tmp_path.joinpath("brew-bin").as_posix(),
            **extra,
        }
        args = ["--zip", zpath.as_posix(), "--fixtures", fixtures.as_posix(), "--model", "m.gguf"]
        proc = subprocess.run(
            [_bash(), (kit / "clean-room.sh").as_posix(), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=room_dir,
        )
        return proc, (room_dir / "clean-room-results.txt").read_text(), room_dir

    return run


def test_the_clean_room_passes_and_gates_on_a_cpu_run(room) -> None:
    proc, results, room_dir = room()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in (
        "room_floor",
        "room_no_clt",
        "room_no_brew",
        "quarantined",
        "quarantine_propagated",
        "launch",
        "smoke",
        "cpu_run",
    ):
        assert f"PASS {name}" in results, name
    # D16: the gating run had no Metal backend; the VM's Metal run is recorded, not gated.
    assert "offloaded" not in (room_dir / "cpu_run.log").read_text()
    assert "INFO metal_in_vm" in results and "offloaded 37/37" in results


def test_the_clean_room_fails_off_the_floor(room) -> None:
    proc, results, _ = room(FAKE_MACOS="14.5")
    assert proc.returncode == 1
    assert "FAIL room_floor" in results


def test_the_clean_room_fails_where_homebrew_is_installed(room, tmp_path: Path) -> None:
    _exe(tmp_path / "brew-bin" / "brew", "exit 0\n")
    proc, results, _ = room()
    assert proc.returncode == 1
    assert "FAIL room_no_brew" in results


def test_the_clean_room_looks_for_brew_where_homebrew_installs_it() -> None:
    text = (MACOS / "clean-room.sh").read_text(encoding="utf-8")
    assert "KNAIF_ROOM_BREW_DIRS:-/opt/homebrew/bin /usr/local/bin" in text


def test_the_clean_room_fails_when_the_real_run_fails(room) -> None:
    proc, results, _ = room(FAKE_RUN_EXIT="3")
    assert proc.returncode == 1
    assert "FAIL cpu_run" in results
