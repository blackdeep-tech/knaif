#!/usr/bin/env python3
"""Assert a staged macOS artifact has no undeclared, unresolvable runtime dependency.

The Darwin counterpart of ``scripts/check_pe_imports.py`` (Windows) and
``scripts/check_elf_deps.py`` (Linux). Same rationale, same shape: parse the binary's own
dependency metadata in pure Python so this runs on ANY machine — including a Windows or Linux
dev box working on this plan — and therefore fails where the mistake was made, not only on the
one platform that would suffer it (a stock Mac with no Homebrew/Xcode).

**Judge by RESOLUTION, not path shape.** This is the one property that makes this checker
different from "grep for a suspicious path": ``@rpath/libomp.dylib`` carries no
``/opt/homebrew/...`` string anywhere — LLVM builds ``libomp.dylib`` with an ``LC_ID_DYLIB`` of
exactly that ``@rpath`` form — so a checker that only flags visibly-foreign absolute paths would
report a clean bill of health on an artifact that fails to load on any Mac without that file
staged. Every ``@rpath``/``@loader_path``/``@executable_path`` dependency is therefore resolved
against the artifact's own directory; anything that does not resolve there, and is not a genuine
system path (``/usr/lib/**``, ``/System/Library/Frameworks/**``), is a failure — regardless of
whether the string looks suspicious.

Also asserts, per binary:

* **every architecture slice of a fat (universal) binary** is arm64 — this project ships
  arm64-only (D4 in the macOS support plan); an x86_64 slice would mean a Rosetta-only or
  accidentally-universal build slipped through.
* **the deployment floor** (``LC_BUILD_VERSION``'s ``minos``, or the legacy
  ``LC_VERSION_MIN_MACOSX``) matches ``--min-os`` exactly. A floor that is *stated* in
  ``MACOSX_DEPLOYMENT_TARGET`` but never *verified* on the actual output is the exact pattern
  that already shipped two portability defects on other platforms (Windows' ``VCOMP140.dll``,
  Linux's ``libgomp.so.1`` — both resolved on the build box and nowhere else).
* **the main executable declares an ``@loader_path``/``@executable_path`` rpath** if any of its
  dependencies use ``@rpath`` at all — llama-cpp-sys-2's build.rs emits no rpath link args, so the
  default build has ZERO ``LC_RPATH`` entries and aborts with "no LC_RPATH's found" (verified
  against a real build); this catches that regression directly, not just its symptom.

Usage::

    python3 scripts/check_macho_deps.py dist/staging/knaif-1.1.0-macos-arm64/bin --min-os 12.0
    python3 scripts/check_macho_deps.py <dir> --min-os 12.0 --verbose

Exits non-zero listing every unresolved/foreign dependency, non-arm64 slice, or floor mismatch.
No third-party dependency (no ``otool``/``lipo`` shell-out): Mach-O headers are a short ``struct``
parse, same as the PE and ELF siblings.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------------------------
# Mach-O / fat-binary constants (mach-o/loader.h, mach-o/fat.h)
# ---------------------------------------------------------------------------------------------

MH_MAGIC_64 = 0xFEEDFACF
MH_MAGIC = 0xFEEDFACE  # 32-bit — not shipped, reported as an error rather than silently skipped
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

CPU_TYPE_ARM64 = 0x0100000C

MH_EXECUTE = 0x2
MH_DYLIB = 0x6
MH_BUNDLE = 0x8  # a `dynamic-backends` loadable ggml-* backend is this filetype

LC_REQ_DYLD = 0x80000000
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB = 0x18 | LC_REQ_DYLD
LC_RPATH = 0x1C | LC_REQ_DYLD
LC_REEXPORT_DYLIB = 0x1F | LC_REQ_DYLD
LC_LAZY_LOAD_DYLIB = 0x20
LC_VERSION_MIN_MACOSX = 0x24
LC_LOAD_UPWARD_DYLIB = 0x23 | LC_REQ_DYLD
LC_BUILD_VERSION = 0x32

# All the load commands that name a dependency this binary must resolve at load time — the
# plan's explicit list, plus LC_ID_DYLIB (a dylib's own declared name) tracked separately below.
DYLIB_DEP_CMDS = {
    LC_LOAD_DYLIB,
    LC_LOAD_WEAK_DYLIB,
    LC_REEXPORT_DYLIB,
    LC_LOAD_UPWARD_DYLIB,
    LC_LAZY_LOAD_DYLIB,
}

# Genuine system paths: present on every Mac, never staged by us. Prefix match, not an
# allowlist of exact names — /System/Library/Frameworks/Foundation.framework/Versions/C/Foundation
# and dozens like it are all "the system", and enumerating each one would be the same mistake
# WINDOWS_PROVIDED/BASE_SYSTEM exist to avoid on the other two platforms (an allowlist of
# individual files instead of the two directories that are unconditionally trustworthy).
SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/Frameworks/")

RPATH_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")


class MachOError(ValueError):
    """Raised for a file that is not a (fat or thin) 64-bit Mach-O this checker can parse."""


@dataclass
class Slice:
    """One architecture slice of a Mach-O file."""

    cputype: int
    filetype: int
    dependencies: list[tuple[int, str]] = field(default_factory=list)  # (cmd, name)
    id_name: str | None = None
    rpaths: list[str] = field(default_factory=list)
    minos: tuple[int, int, int] | None = None


def _cstr(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("utf-8", "replace")


def _decode_version(packed: int) -> tuple[int, int, int]:
    """X.Y.Z packed as (X << 16) | (Y << 8) | Z — the encoding both version load commands use."""
    return (packed >> 16) & 0xFFFF, (packed >> 8) & 0xFF, packed & 0xFF


def _parse_slice(data: bytes, base: int) -> Slice:
    try:
        magic = struct.unpack_from("<I", data, base)[0]
    except struct.error as exc:
        # A fat_arch offset pointing past EOF — a corrupted/mutated fat header, not a real slice.
        raise MachOError(f"slice offset {base} out of range: {exc}") from exc
    if magic == MH_MAGIC:
        raise MachOError("32-bit Mach-O (MH_MAGIC) is not supported — nothing ships 32-bit")
    if magic != MH_MAGIC_64:
        raise MachOError(f"not a 64-bit Mach-O slice (magic={magic:#010x})")

    try:
        cputype, _cpusubtype, filetype, ncmds, _sizeofcmds = struct.unpack_from(
            "<iiIII", data, base + 4
        )
        slc = Slice(cputype=cputype, filetype=filetype)

        off = base + 32  # sizeof(mach_header_64)
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, off)
            # A load command is at least 8 bytes (its own cmd/cmdsize fields). Zero (or a
            # sub-8 value from a corrupted file) would either infinite-loop or walk backwards —
            # a malformed/mutated fixture must fail loudly here, not hang or crash uncaught.
            if cmdsize < 8:
                raise MachOError(f"load command at offset {off} has cmdsize={cmdsize} (< 8)")
            if cmd in DYLIB_DEP_CMDS or cmd == LC_ID_DYLIB:
                (name_off,) = struct.unpack_from("<I", data, off + 8)
                name = _cstr(data, off + name_off)
                if cmd == LC_ID_DYLIB:
                    slc.id_name = name
                else:
                    slc.dependencies.append((cmd, name))
            elif cmd == LC_RPATH:
                (path_off,) = struct.unpack_from("<I", data, off + 8)
                slc.rpaths.append(_cstr(data, off + path_off))
            elif cmd == LC_BUILD_VERSION:
                _platform, minos, _sdk, _ntools = struct.unpack_from("<IIII", data, off + 8)
                slc.minos = _decode_version(minos)
            elif cmd == LC_VERSION_MIN_MACOSX and slc.minos is None:
                # Legacy pre-LC_BUILD_VERSION toolchains. Only used if LC_BUILD_VERSION is
                # absent — a binary would not carry both, but prefer the modern one if it did.
                (version,) = struct.unpack_from("<I", data, off + 8)
                slc.minos = _decode_version(version)
            off += cmdsize
    except (struct.error, ValueError) as exc:
        # Anything short of a clean parse — truncated mid-command, a name/rpath offset pointing
        # past EOF or with no NUL terminator, cmdsize walking off the end of the file — is a
        # malformed Mach-O, not a Python crash. Re-raised as the one exception type callers
        # already handle (a bare ValueError from `_cstr`'s `bytes.index` miss is folded in here
        # for the same reason: it is still "this file is not a well-formed Mach-O").
        if isinstance(exc, MachOError):
            raise
        raise MachOError(f"malformed load command table: {exc}") from exc
    return slc


def parse_macho(path: Path) -> list[Slice]:
    """Return every architecture slice in *path* (one, unless it's a fat/universal binary)."""
    data = path.read_bytes()
    if len(data) < 8:
        raise MachOError("file too short to be a Mach-O")

    try:
        fat_magic = struct.unpack_from(">I", data, 0)[0]
        if fat_magic == FAT_MAGIC:
            (nfat_arch,) = struct.unpack_from(">I", data, 4)
            slices = []
            for i in range(nfat_arch):
                base = 8 + i * 20
                _cputype, _cpusubtype, offset, _size, _align = struct.unpack_from(
                    ">iiIII", data, base
                )
                slices.append(_parse_slice(data, offset))
            return slices
        if fat_magic == FAT_MAGIC_64:
            (nfat_arch,) = struct.unpack_from(">I", data, 4)
            slices = []
            for i in range(nfat_arch):
                base = 8 + i * 32
                _cputype, _cpusubtype, offset, _size, _align, _reserved = struct.unpack_from(
                    ">iiQQII", data, base
                )
                slices.append(_parse_slice(data, offset))
            return slices
    except struct.error as exc:
        raise MachOError(f"malformed fat header: {exc}") from exc

    return [_parse_slice(data, 0)]


# ---------------------------------------------------------------------------------------------
# The staged-directory audit
# ---------------------------------------------------------------------------------------------


def _resolves(name: str, bindir: Path) -> bool:
    """Does dependency *name* resolve — to a genuine system path, or a file staged in bindir?"""
    if name.startswith(SYSTEM_PREFIXES):
        return True
    for prefix in RPATH_PREFIXES:
        if name.startswith(prefix):
            # Our own artifacts are a flat bin/ directory whose rpaths all reduce to "this same
            # directory" (B3: @loader_path only, verified empirically) — so resolution against
            # bindir is not an approximation for this artifact shape, it IS the real mechanism.
            # `Path.exists()` follows symlinks, so a symlink whose target is missing correctly
            # reports unresolved rather than a false pass.
            rel = name[len(prefix) :]
            return (bindir / rel).exists()
    return False  # any other absolute/relative path is a foreign, non-system dependency


def audit(bindir: Path, min_os: tuple[int, int, int], verbose: bool) -> list[str]:
    failures: list[str] = []
    binaries = sorted(
        p for p in bindir.iterdir() if p.is_file() and not p.is_symlink() and p.name != ".DS_Store"
    )
    for binary in binaries:
        try:
            slices = parse_macho(binary)
        except MachOError as exc:
            if verbose:
                print(f"  --   {binary.name}: {exc} (skipped)")
            continue

        for slc in slices:
            if slc.cputype != CPU_TYPE_ARM64:
                failures.append(
                    f"{binary.name}: non-arm64 slice (cputype={slc.cputype:#x}) — this project "
                    "ships arm64-only (D4 in the macOS support plan)"
                )
                continue

            if slc.minos is None:
                failures.append(
                    f"{binary.name}: no LC_BUILD_VERSION/LC_VERSION_MIN_MACOSX — the deployment "
                    "floor cannot be verified on this binary at all"
                )
            elif slc.minos != min_os:
                got = ".".join(map(str, slc.minos))
                want = ".".join(map(str, min_os))
                failures.append(
                    f"{binary.name}: deployment floor is {got}, expected {want} — "
                    "MACOSX_DEPLOYMENT_TARGET was not honoured for this binary (it is not a "
                    "rerun-if-env-changed input, so a stale cached build silently keeps the old "
                    "floor — see D9)"
                )

            uses_rpath = False
            for _cmd, dep in slc.dependencies:
                if _resolves(dep, bindir):
                    if dep.startswith(RPATH_PREFIXES):
                        uses_rpath = True
                    if verbose:
                        print(f"  ok   {binary.name} -> {dep}")
                    continue
                failures.append(
                    f"{binary.name} needs {dep}, which does not resolve: not a system path "
                    f"({'/'.join(SYSTEM_PREFIXES)}) and not staged in {bindir.name}/. This is "
                    "the libomp.dylib trap — an @rpath dependency reads identically to a "
                    "resolvable one until you check whether the target actually exists"
                )

            if uses_rpath and slc.filetype == MH_EXECUTE:
                loader_rpath = any(
                    r in ("@loader_path", "@loader_path/", "@executable_path", "@executable_path/")
                    for r in slc.rpaths
                )
                if not loader_rpath:
                    failures.append(
                        f"{binary.name}: has @rpath dependencies but declares no "
                        "@loader_path/@executable_path LC_RPATH — the default llama-cpp-sys-2 "
                        "build emits ZERO rpath entries (verified), so this binary will abort "
                        "with \"Library not loaded ... no LC_RPATH's found\" the moment it is "
                        "moved off the build box"
                    )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bindir", type=Path, help="the artifact's bin/ directory")
    parser.add_argument(
        "--min-os",
        required=True,
        metavar="X.Y",
        help="the declared MACOSX_DEPLOYMENT_TARGET floor every binary must carry, e.g. 12.0",
    )
    parser.add_argument("--verbose", action="store_true", help="list every resolved dependency")
    args = parser.parse_args(argv)

    if not args.bindir.is_dir():
        print(f"not a directory: {args.bindir}", file=sys.stderr)
        return 2

    parts = args.min_os.split(".")
    if not (1 <= len(parts) <= 3) or not all(p.isdigit() for p in parts):
        print(f"--min-os must look like X.Y or X.Y.Z, got {args.min_os!r}", file=sys.stderr)
        return 2
    min_os = tuple(int(p) for p in parts) + (0, 0, 0)
    min_os = min_os[:3]

    binaries = [p for p in args.bindir.iterdir() if p.is_file() and not p.is_symlink()]
    if not binaries:
        print(f"no files in {args.bindir}", file=sys.stderr)
        return 2

    failures = audit(args.bindir, min_os, args.verbose)

    if failures:
        print(f"FAIL: {len(failures)} dependency/portability issue(s)\n", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nAn unresolved @rpath dependency resolves on THIS machine only because the file "
            "happens\nto exist here (a build box, a Mac with Homebrew, ...). Stage it beside the "
            "binary, or\nremove the dependency (see D3 for the OpenMP case).",
            file=sys.stderr,
        )
        return 1

    print(f"  ok  {len(binaries)} files: every dependency resolves, arm64-only, floor honoured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
