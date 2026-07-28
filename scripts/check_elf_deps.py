#!/usr/bin/env python3
"""Audit a staged Linux artifact's shared-library and symbol-version requirements.

The Linux counterpart of ``scripts/check_pe_imports.py``, and it answers a question the glibc
version alone cannot: **which machines can actually run this artifact?**

Two independent axes decide that, and quoting only the first is how a "supported distro" table
becomes wrong:

1. **``DT_NEEDED``** — every shared library the binary must resolve at load time. Anything not
   staged beside it and not part of a base Linux system is a dependency on the build machine.
2. **Versioned symbols** — glibc, libstdc++ and the C++ ABI use symbol versioning, so a binary
   built against a newer toolchain fails on an older host *even when the library filename is
   present*. A distro can satisfy ``GLIBC_2.35`` and still lack ``GLIBCXX_3.4.30``.

The maximum required version on each axis **is** the floor. This script reports it, so the
compatibility table in the docs is measured rather than assumed.

Usage::

    python3 scripts/check_elf_deps.py dist/staging/knaif-1.1.0-linux-x64/bin
    python3 scripts/check_elf_deps.py <dir> --verbose

Exits non-zero if a library is neither staged nor on the baseline. No third-party dependency and
no ``readelf`` shell-out: this must run anywhere the artifact is built or verified, including a
container with a minimal toolchain.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

# Libraries every glibc-based Linux provides. Deliberately short: anything outside it is either
# staged beside the binary or a genuine external requirement someone must justify.
BASE_SYSTEM = {
    "libc.so.6",
    "libm.so.6",
    "libdl.so.2",
    "libpthread.so.0",
    "librt.so.1",
    "libgcc_s.so.1",
    "libstdc++.so.6",
    "libresolv.so.2",
    "libutil.so.1",
    "ld-linux-x86-64.so.2",
    "ld-linux-aarch64.so.1",
}

# Installed by the GPU driver, never by us and never by the base system. Only a *loadable* backend
# may need one: the runtime probes those and skips a backend that fails to load, so a machine with
# no driver degrades to CPU. A core library depending on one would refuse to start.
# NB libcudart/libcublas are NOT here — package.sh stages those into the CUDA payload, so they are
# required, not assumed. Allowlisting them would invert the check.
DRIVER_PROVIDED = {"libvulkan.so.1", "libcuda.so.1"}

# Core libraries staged by package.sh, mirroring its Linux staging loop. Everything else matching
# libggml-* is a loadable backend. Matching on the prefix alone would wrongly treat
# libggml-base.so — loaded at process start — as skippable.
CORE_STEMS = {"libggml-base", "libggml", "libllama", "libllama-common"}

DT_NEEDED, DT_SONAME = 1, 14
SHT_DYNAMIC, SHT_GNU_VERNEED = 6, 0x6FFFFFFE


def _verkey(v: str) -> list[int]:
    """Sort key for a versioned-symbol string like GLIBCXX_3.4.30 or CXXABI_1.3.13."""
    return [int(p) for p in re.findall(r"\d+", v)] or [0]


def _sections(data: bytes):
    """Yield (name_off, sh_type, sh_offset, sh_size, sh_link, sh_entsize) plus the shstrtab."""
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2:
        raise ValueError("not ELF64 (32-bit binaries are not shipped)")
    (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    out = []
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        sh_name, sh_type = struct.unpack_from("<II", data, base)
        sh_offset, sh_size = struct.unpack_from("<QQ", data, base + 0x18)
        (sh_link,) = struct.unpack_from("<I", data, base + 0x28)
        out.append((sh_name, sh_type, sh_offset, sh_size, sh_link))
    return out


def _cstr(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("utf-8", "replace")


def analyse(path: Path) -> tuple[list[str], dict[str, str]]:
    """Return (DT_NEEDED names, {library: max required symbol version})."""
    data = path.read_bytes()
    sections = _sections(data)

    needed: list[str] = []
    versions: dict[str, list[str]] = {}

    for _name, sh_type, sh_offset, sh_size, sh_link in sections:
        if sh_type == SHT_DYNAMIC:
            # sh_link names the string table this .dynamic section indexes into.
            strtab_off = sections[sh_link][2]
            for off in range(sh_offset, sh_offset + sh_size, 16):
                tag, val = struct.unpack_from("<qQ", data, off)
                if tag == 0:  # DT_NULL
                    break
                if tag == DT_NEEDED:
                    needed.append(_cstr(data, strtab_off + val))
        elif sh_type == SHT_GNU_VERNEED:
            strtab_off = sections[sh_link][2]
            off = sh_offset
            while True:
                _ver, cnt, file_off, aux_off, next_off = struct.unpack_from("<HHIII", data, off)
                lib = _cstr(data, strtab_off + file_off)
                aux = off + aux_off
                for _ in range(cnt):
                    _hash, _flags, _other, name_off, aux_next = struct.unpack_from(
                        "<IHHII", data, aux
                    )
                    versions.setdefault(lib, []).append(_cstr(data, strtab_off + name_off))
                    if not aux_next:
                        break
                    aux += aux_next
                if not next_off:
                    break
                off += next_off

    # Group by version PREFIX, not by library. libstdc++ carries two independent families —
    # GLIBCXX_* and CXXABI_* — and taking one maximum per library hides whichever sorts lower
    # numerically: GLIBCXX_3.4.30 silently masked CXXABI_1.3.13, which a real loader then
    # reported as a separate missing version. Same for libc.so.6, which can carry GLIBC_* and
    # others. The floor is the max of EACH family.
    out: dict[str, str] = {}
    for lib, vs in versions.items():
        by_family: dict[str, list[str]] = {}
        for v in vs:
            by_family.setdefault(v.rsplit("_", 1)[0], []).append(v)
        for family, members in by_family.items():
            out[f"{lib} ({family})"] = max(members, key=_verkey)
    return needed, out


def _is_core(name: str) -> bool:
    stem = name.split(".so")[0]
    return stem in CORE_STEMS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bindir", type=Path, help="the artifact's bin/ directory")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.bindir.is_dir():
        print(f"not a directory: {args.bindir}", file=sys.stderr)
        return 2

    binaries = sorted(
        p
        for p in args.bindir.iterdir()
        if p.is_file()
        and not p.is_symlink()
        and (p.suffix == ".so" or ".so." in p.name or p.suffix == "")
    )
    if not binaries:
        print(f"no ELF files in {args.bindir}", file=sys.stderr)
        return 2

    staged = {p.name for p in args.bindir.iterdir()}
    failures: list[str] = []
    floor: dict[str, str] = {}

    for binary in binaries:
        try:
            needed, versions = analyse(binary)
        except ValueError as exc:
            if args.verbose:
                print(f"  --   {binary.name}: {exc} (skipped)")
            continue

        for lib in needed:
            if lib in staged or lib in BASE_SYSTEM:
                continue
            if lib in DRIVER_PROVIDED:
                if not _is_core(binary.name) and binary.name.startswith("libggml-"):
                    continue
                failures.append(
                    f"{binary.name} needs {lib}, which only a loadable ggml-* backend may "
                    "depend on. This library is loaded at process start, so a machine without "
                    "the driver could not launch knaif at all"
                )
                continue
            failures.append(
                f"{binary.name} needs {lib}, which is neither staged in "
                f"{args.bindir.name}/ nor part of a base Linux system"
            )

        for lib, v in versions.items():
            if lib not in floor or _verkey(v) > _verkey(floor[lib]):
                floor[lib] = v
        if args.verbose:
            reqs = ", ".join(f"{k}:{v}" for k, v in sorted(versions.items())) or "none"
            print(f"  ok   {binary.name} -> {reqs}")

    print(f"\nRequired symbol versions across {len(binaries)} binaries — THIS IS THE FLOOR:")
    for lib in sorted(floor):
        print(f"  {lib:22s} {floor[lib]}")
    print(
        "\nA distro must satisfy EVERY line above, not just the glibc one. Check the claimed\n"
        "support table against these, and remember libstdc++/CXXABI move with GCC, not glibc."
    )

    if failures:
        print(f"\nFAIL: {len(failures)} undeclared runtime dependency(ies)\n", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nThese resolve on the build machine only because it has the toolchain installed.\n"
            "Stage the library beside the binary, or add it to BASE_SYSTEM if it genuinely\n"
            "ships with every supported distro.",
            file=sys.stderr,
        )
        return 1

    print(f"\n  ok  {len(binaries)} binaries: every DT_NEEDED is staged or base-system")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
