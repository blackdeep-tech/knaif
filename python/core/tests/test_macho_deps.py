"""Guard `scripts/check_macho_deps.py` — the check that keeps macOS artifacts portable.

Same treatment as `test_pe_imports.py` (its closest sibling) and the precedent
`test_installer_iss.py` set: assert the checker **fails** on every case it claims to catch, not
merely that it passes on a healthy tree. A checker nobody has seen fail is a checker nobody
should trust — this is the plan's own words for why this file exists (E1 in the 2026-08-02 macOS
support plan).

Tests build **synthetic Mach-O files** rather than leaning on a staged artifact: `dist/staging/`
only exists after a macOS build, and a test that depended on it would silently skip everywhere
else (the exact "the check cannot run where it matters" failure this whole area keeps producing —
this checker is explicitly designed to run on any host, including Linux/Windows CI).

Two of the cases below (`LC_DISABLE_FIND_PACKAGE_OpenSSL`-shaped unresolved @rpath, and the
missing-exe-rpath case) are not hypothetical: they are the two real defects
`check_macho_deps.py` found in this project's own first macOS artifact on 2026-08-03, before this
test file existed to pin them down.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "check_macho_deps.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_macho_deps", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # The module must be registered in sys.modules BEFORE exec_module runs: the script's
    # `@dataclass`-decorated Slice looks up `sys.modules[cls.__module__]` internally (a Python
    # 3.14 dataclasses implementation detail), which is None otherwise.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cmd = _load()


# --------------------------------------------------------------------------------------
# A minimal, structurally-real Mach-O 64-bit slice
# --------------------------------------------------------------------------------------


def _pack_dylib_cmd(lc: int, name: str) -> bytes:
    """LC_LOAD_DYLIB / LC_ID_DYLIB / LC_LOAD_WEAK_DYLIB / etc — a dylib_command."""
    name_bytes = name.encode("utf-8") + b"\0"
    size = 24 + len(name_bytes)
    padded = (size + 7) // 8 * 8
    body = struct.pack("<IIIIII", lc, padded, 24, 0, 0, 0) + name_bytes
    return body + b"\0" * (padded - size)


def _pack_rpath_cmd(path: str) -> bytes:
    path_bytes = path.encode("utf-8") + b"\0"
    size = 12 + len(path_bytes)
    padded = (size + 7) // 8 * 8
    body = struct.pack("<III", cmd.LC_RPATH, padded, 12) + path_bytes
    return body + b"\0" * (padded - size)


def _pack_build_version_cmd(major: int, minor: int, patch: int) -> bytes:
    minos = (major << 16) | (minor << 8) | patch
    return struct.pack("<IIIIII", cmd.LC_BUILD_VERSION, 24, 1, minos, 0, 0)


def macho_bytes(
    *,
    cputype: int = cmd.CPU_TYPE_ARM64,
    filetype: int = cmd.MH_EXECUTE,
    deps: list[tuple[int, str]] = (),
    id_name: str | None = None,
    rpaths: list[str] = (),
    minos: tuple[int, int, int] | None = (12, 0, 0),
    extra_cmds: bytes = b"",
) -> bytes:
    """Build one thin, well-formed 64-bit Mach-O slice as raw bytes."""
    load_cmds = b""
    for lc, name in deps:
        load_cmds += _pack_dylib_cmd(lc, name)
    if id_name is not None:
        load_cmds += _pack_dylib_cmd(cmd.LC_ID_DYLIB, id_name)
    for rp in rpaths:
        load_cmds += _pack_rpath_cmd(rp)
    if minos is not None:
        load_cmds += _pack_build_version_cmd(*minos)
    load_cmds += extra_cmds

    ncmds = len(deps) + (1 if id_name is not None else 0) + len(rpaths) + (1 if minos else 0)
    # count extra_cmds as commands too, via a caller-supplied ncmds override would be cleaner,
    # but no test below mixes extra_cmds with a manual ncmds check, so this stays simple.
    header = struct.pack(
        "<IiiIIIII",
        cmd.MH_MAGIC_64,
        cputype,
        0,
        filetype,
        ncmds,
        len(load_cmds),
        0,
        0,
    )
    return header + load_cmds


def write_macho(path: Path, **kwargs) -> Path:
    path.write_bytes(macho_bytes(**kwargs))
    return path


def write_fat(path: Path, slices: list[dict]) -> Path:
    """Build a FAT_MAGIC universal binary from a list of `macho_bytes(**kwargs)` dicts."""
    n = len(slices)
    bodies = [macho_bytes(**s) for s in slices]
    align = 0x1000
    offset = 8 + n * 20
    offset = (offset + align - 1) // align * align
    header = struct.pack(">II", cmd.FAT_MAGIC, n)
    arch_table = b""
    blob = b""
    for s, body in zip(slices, bodies, strict=True):
        this_off = offset + len(blob)
        arch_table += struct.pack(
            ">iiIII", s.get("cputype", cmd.CPU_TYPE_ARM64), 0, this_off, len(body), 12
        )
        blob += body
    buf = header + arch_table
    buf += b"\0" * (offset - len(buf))
    buf += blob
    path.write_bytes(buf)
    return path


@pytest.fixture()
def artifact(tmp_path: Path):
    """Build a bin/ dir; returns a callable taking {filename: macho_bytes(**kwargs)}."""

    def build(layout: dict[str, dict]) -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        for name, kwargs in layout.items():
            write_macho(bindir / name, **kwargs)
        return bindir

    return build


# --------------------------------------------------------------------------------------
# The parser itself
# --------------------------------------------------------------------------------------


def test_reads_dependencies_rpaths_and_floor(tmp_path: Path) -> None:
    p = write_macho(
        tmp_path / "x.dylib",
        filetype=cmd.MH_DYLIB,
        deps=[(cmd.LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib")],
        id_name="@rpath/x.dylib",
        rpaths=["@loader_path"],
        minos=(12, 0, 0),
    )
    (slc,) = cmd.parse_macho(p)
    assert slc.cputype == cmd.CPU_TYPE_ARM64
    assert slc.dependencies == [(cmd.LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib")]
    assert slc.id_name == "@rpath/x.dylib"
    assert slc.rpaths == ["@loader_path"]
    assert slc.minos == (12, 0, 0)


@pytest.mark.parametrize(
    "lc",
    [
        cmd.LC_LOAD_DYLIB,
        cmd.LC_LOAD_WEAK_DYLIB,
        cmd.LC_REEXPORT_DYLIB,
        cmd.LC_LOAD_UPWARD_DYLIB,
        cmd.LC_LAZY_LOAD_DYLIB,
    ],
)
def test_every_dependency_load_command_kind_is_read(tmp_path: Path, lc: int) -> None:
    """The plan's explicit list: LC_LOAD_DYLIB, _WEAK_, LC_REEXPORT_, _UPWARD_, LC_LAZY_LOAD_."""
    p = write_macho(tmp_path / "x.dylib", filetype=cmd.MH_DYLIB, deps=[(lc, "/usr/lib/libfoo.dylib")])
    (slc,) = cmd.parse_macho(p)
    assert slc.dependencies == [(lc, "/usr/lib/libfoo.dylib")]


def test_legacy_version_min_macosx_is_read_when_build_version_absent(tmp_path: Path) -> None:
    body = macho_bytes(minos=None)
    # Splice in a hand-built LC_VERSION_MIN_MACOSX (cmd=0x24) instead of LC_BUILD_VERSION.
    legacy = struct.pack("<IIII", cmd.LC_VERSION_MIN_MACOSX, 16, (11 << 16) | (0 << 8) | 0, 0)
    header = bytearray(body[:32])
    ncmds, sizeofcmds = struct.unpack_from("<II", header, 16)
    struct.pack_into("<II", header, 16, ncmds + 1, sizeofcmds + 16)
    p = tmp_path / "legacy.dylib"
    p.write_bytes(bytes(header) + body[32:] + legacy)
    (slc,) = cmd.parse_macho(p)
    assert slc.minos == (11, 0, 0)


# --------------------------------------------------------------------------------------
# Malformed input — must fail LOUDLY (MachOError), never crash or hang
# --------------------------------------------------------------------------------------


def test_too_short_to_be_a_macho(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_bytes(b"\0\0\0")
    with pytest.raises(cmd.MachOError):
        cmd.parse_macho(p)


def test_wrong_magic_is_reported_not_ignored(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_bytes(b"not a macho at all, but long enough to look plausible" * 4)
    with pytest.raises(cmd.MachOError):
        cmd.parse_macho(p)


def test_32bit_magic_is_rejected_explicitly(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_bytes(struct.pack("<IiiIIIII", cmd.MH_MAGIC, cmd.CPU_TYPE_ARM64, 0, cmd.MH_EXECUTE, 0, 0, 0, 0))
    with pytest.raises(cmd.MachOError, match="32-bit"):
        cmd.parse_macho(p)


def test_zero_cmdsize_fails_instead_of_hanging(tmp_path: Path) -> None:
    """A cmdsize of 0 would never advance the read cursor — this must not be an infinite loop."""
    header = struct.pack("<IiiIIIII", cmd.MH_MAGIC_64, cmd.CPU_TYPE_ARM64, 0, cmd.MH_EXECUTE, 1, 8, 0, 0)
    bogus_cmd = struct.pack("<II", cmd.LC_LOAD_DYLIB, 0)
    p = tmp_path / "x"
    p.write_bytes(header + bogus_cmd)
    with pytest.raises(cmd.MachOError, match="cmdsize"):
        cmd.parse_macho(p)


def test_load_command_table_truncated_mid_command(tmp_path: Path) -> None:
    """cmdsize claims more bytes than the file actually has."""
    header = struct.pack("<IiiIIIII", cmd.MH_MAGIC_64, cmd.CPU_TYPE_ARM64, 0, cmd.MH_EXECUTE, 1, 200, 0, 0)
    truncated_cmd = struct.pack("<II", cmd.LC_LOAD_DYLIB, 200)  # claims 200 bytes, none follow
    p = tmp_path / "x"
    p.write_bytes(header + truncated_cmd)
    with pytest.raises(cmd.MachOError):
        cmd.parse_macho(p)


def test_name_offset_with_no_null_terminator(tmp_path: Path) -> None:
    """A dylib name string that never terminates must fail cleanly, not raise a bare ValueError."""
    name_off = 24
    cmdsize = 32
    body = struct.pack("<IIIIII", cmd.LC_LOAD_DYLIB, cmdsize, name_off, 0, 0, 0) + b"no-null-here-at-all"
    header = struct.pack("<IiiIIIII", cmd.MH_MAGIC_64, cmd.CPU_TYPE_ARM64, 0, cmd.MH_EXECUTE, 1, len(body), 0, 0)
    p = tmp_path / "x"
    p.write_bytes(header + body)
    with pytest.raises(cmd.MachOError):
        cmd.parse_macho(p)


def test_fat_arch_offset_past_eof(tmp_path: Path) -> None:
    """A corrupted fat_arch offset pointing outside the file must fail, not crash."""
    header = struct.pack(">II", cmd.FAT_MAGIC, 1)
    arch = struct.pack(">iiIII", cmd.CPU_TYPE_ARM64, 0, 0xFFFFFF, 4, 12)  # offset way past EOF
    p = tmp_path / "x"
    p.write_bytes(header + arch)
    with pytest.raises(cmd.MachOError):
        cmd.parse_macho(p)


# --------------------------------------------------------------------------------------
# Fat (universal) binaries — walk every slice, reject non-arm64 (D4)
# --------------------------------------------------------------------------------------


def test_fat_binary_all_arm64_slices_are_read(tmp_path: Path) -> None:
    p = write_fat(
        tmp_path / "fat.dylib",
        [
            {"filetype": cmd.MH_DYLIB, "deps": [(cmd.LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib")]},
            {"filetype": cmd.MH_DYLIB, "deps": [(cmd.LC_LOAD_DYLIB, "/usr/lib/libc++.1.dylib")]},
        ],
    )
    slices = cmd.parse_macho(p)
    assert len(slices) == 2
    assert all(s.cputype == cmd.CPU_TYPE_ARM64 for s in slices)


def test_fat_binary_with_a_non_arm64_slice_is_rejected(tmp_path: Path) -> None:
    """D4: arm64-only. A universal binary carrying (say) an x86_64 slice must fail the audit."""
    CPU_TYPE_X86_64 = 0x01000007
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_fat(
        bindir / "universal.dylib",
        [
            {"cputype": cmd.CPU_TYPE_ARM64, "filetype": cmd.MH_DYLIB},
            {"cputype": CPU_TYPE_X86_64, "filetype": cmd.MH_DYLIB},
        ],
    )
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("non-arm64" in f for f in failures)


# --------------------------------------------------------------------------------------
# Resolution — the whole point (§1.3's warning: judge by resolution, not path shape)
# --------------------------------------------------------------------------------------


def test_system_paths_resolve(artifact) -> None:
    bindir = artifact(
        {
            "knaif": {
                "filetype": cmd.MH_EXECUTE,
                "deps": [
                    (cmd.LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib"),
                    (
                        cmd.LC_LOAD_DYLIB,
                        "/System/Library/Frameworks/Metal.framework/Versions/A/Metal",
                    ),
                ],
            }
        }
    )
    assert cmd.audit(bindir, (12, 0, 0), verbose=False) == []


def test_rpath_dependency_staged_beside_it_resolves(artifact) -> None:
    bindir = artifact(
        {
            "knaif": {
                "filetype": cmd.MH_EXECUTE,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libggml-base.0.dylib")],
                "rpaths": ["@loader_path"],
            },
            "libggml-base.0.dylib": {
                "filetype": cmd.MH_DYLIB,
                "id_name": "@rpath/libggml-base.0.dylib",
            },
        }
    )
    assert cmd.audit(bindir, (12, 0, 0), verbose=False) == []


def test_the_libomp_trap_an_unstaged_rpath_dependency_fails(artifact, capsys) -> None:
    """The exact defect §1.3 warns about: no foreign path string anywhere, just a missing file."""
    bindir = artifact(
        {
            "libggml-base.0.dylib": {
                "filetype": cmd.MH_DYLIB,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libomp.dylib")],
                "rpaths": ["@loader_path"],
            }
        }
    )
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert len(failures) == 1
    assert "libomp.dylib" in failures[0]
    assert "does not resolve" in failures[0]


def test_a_broken_symlink_target_is_unresolved(artifact) -> None:
    """Path.exists() follows symlinks — a dangling one must fail exactly like a missing file."""
    bindir = artifact(
        {
            "knaif": {
                "filetype": cmd.MH_EXECUTE,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libggml-base.0.dylib")],
                "rpaths": ["@loader_path"],
            }
        }
    )
    (bindir / "libggml-base.0.dylib").symlink_to(bindir / "does-not-exist.dylib")
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("libggml-base.0.dylib" in f for f in failures)


def test_a_foreign_absolute_path_fails_even_though_it_LOOKS_suspicious(artifact) -> None:
    """The easy case — included so the checker is not ONLY good at the subtle one."""
    bindir = artifact(
        {
            "libllama-common.dylib": {
                "filetype": cmd.MH_DYLIB,
                "deps": [(cmd.LC_LOAD_DYLIB, "/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib")],
            }
        }
    )
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("openssl@3" in f for f in failures)


# --------------------------------------------------------------------------------------
# The exe-rpath regression this checker exists to catch (A5's real finding, 2026-08-03)
# --------------------------------------------------------------------------------------


def test_exe_with_rpath_deps_but_no_rpath_command_fails(artifact) -> None:
    """llama-cpp-sys-2 emits ZERO rpath link args — the default build has this exact shape."""
    bindir = artifact(
        {
            "knaif": {
                "filetype": cmd.MH_EXECUTE,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libggml-base.0.dylib")],
                "rpaths": [],  # <-- the regression: no @loader_path, no @executable_path
            },
            "libggml-base.0.dylib": {"filetype": cmd.MH_DYLIB},
        }
    )
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("no @loader_path" in f or "@executable_path" in f for f in failures)


def test_exe_with_executable_path_rpath_also_passes(artifact) -> None:
    """@executable_path is an equally valid spelling (A5 — both resolve to the same directory)."""
    bindir = artifact(
        {
            "knaif": {
                "filetype": cmd.MH_EXECUTE,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libggml-base.0.dylib")],
                "rpaths": ["@executable_path"],
            },
            "libggml-base.0.dylib": {"filetype": cmd.MH_DYLIB},
        }
    )
    assert cmd.audit(bindir, (12, 0, 0), verbose=False) == []


def test_a_loadable_backend_needs_no_rpath_of_its_own(artifact) -> None:
    """B3's finding: only the EXE needs @loader_path — a dlopen'd .so backend needs none."""
    bindir = artifact(
        {
            "libggml-metal.so": {
                "filetype": cmd.MH_BUNDLE,
                "deps": [(cmd.LC_LOAD_DYLIB, "@rpath/libggml-base.0.dylib")],
                "rpaths": [],
            },
            "libggml-base.0.dylib": {"filetype": cmd.MH_DYLIB},
        }
    )
    assert cmd.audit(bindir, (12, 0, 0), verbose=False) == []


# --------------------------------------------------------------------------------------
# Deployment floor (D9)
# --------------------------------------------------------------------------------------


def test_floor_mismatch_fails(artifact) -> None:
    bindir = artifact({"knaif": {"filetype": cmd.MH_EXECUTE, "minos": (11, 0, 0)}})
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("11.0.0" in f and "12.0.0" in f for f in failures)


def test_floor_match_passes(artifact) -> None:
    bindir = artifact({"knaif": {"filetype": cmd.MH_EXECUTE, "minos": (12, 0, 0)}})
    assert cmd.audit(bindir, (12, 0, 0), verbose=False) == []


def test_missing_floor_declaration_fails(artifact) -> None:
    bindir = artifact({"knaif": {"filetype": cmd.MH_EXECUTE, "minos": None}})
    failures = cmd.audit(bindir, (12, 0, 0), verbose=False)
    assert any("no LC_BUILD_VERSION" in f for f in failures)


# --------------------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------------------


def test_a_directory_with_no_files_is_an_error(tmp_path: Path) -> None:
    empty = tmp_path / "bin"
    empty.mkdir()
    assert cmd.main([str(empty), "--min-os", "12.0"]) == 2
    assert cmd.main([str(tmp_path / "nope"), "--min-os", "12.0"]) == 2


def test_bad_min_os_format_is_rejected(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_macho(bindir / "knaif")
    assert cmd.main([str(bindir), "--min-os", "not-a-version"]) == 2


def test_end_to_end_pass_and_fail_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.mkdir()
    write_macho(good / "knaif", filetype=cmd.MH_EXECUTE, minos=(12, 0, 0))
    assert cmd.main([str(good), "--min-os", "12.0"]) == 0

    bad = tmp_path / "bad"
    bad.mkdir()
    write_macho(bad / "knaif", filetype=cmd.MH_EXECUTE, minos=(11, 0, 0))
    assert cmd.main([str(bad), "--min-os", "12.0"]) == 1
