#!/usr/bin/env python3
"""Assert a staged Windows artifact has no undeclared runtime dependency.

A `dynamic-backends` build is only self-contained if **every DLL it imports** either ships
beside it or is part of Windows itself. Anything else is a dependency on the build machine,
and it fails at process start on a clean box with `STATUS_DLL_NOT_FOUND` (0xC0000135) and no
output at all — the same silent death as a missing `llama.dll`.

**This is a static check on purpose.** `installers/smoke.sh` runs the exe, so it can only
ever pass on a machine that already satisfies every dependency — which is the build box, by
definition. That blind spot is how the shipped 1.0.x tree came to import `VCRUNTIME140.dll`
without staging it: nothing that ran on the build machine could observe the problem. Reading
the import table instead makes the check machine-independent, so it fails where the mistake
was made.

The allowlist below is the real artifact here. "What a clean Windows provides" stops being
tribal knowledge and becomes a reviewable list, and adding an entry is the moment someone has
to justify a new runtime dependency.

Usage::

    python scripts/check_pe_imports.py dist/staging/knaif-1.0.2-windows-x64/bin
    python scripts/check_pe_imports.py <dir> --verbose

Exits non-zero listing every unresolved import. No third-party dependency: a PE import
directory is a short `struct` parse, and `objdump`/`strings` are absent from this repo's Git
Bash, so shelling out is not an option either.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# DLLs a clean Windows 10/11 install provides. Anything not here and not staged beside the
# binary is a dependency on the build machine.
#
# `api-ms-win-*` covers the API sets and the Universal CRT (`api-ms-win-crt-*`), which has
# been an OS component since Windows 10 — that is why the UCRT is fine to import and
# VCRUNTIME140/MSVCP140 are not: those come from the Visual C++ Redistributable, which is an
# application dependency Microsoft expects you to ship or install.
WINDOWS_PROVIDED = {
    "advapi32.dll",
    "bcrypt.dll",
    "bcryptprimitives.dll",
    "cfgmgr32.dll",
    "comdlg32.dll",
    "crypt32.dll",
    "dbghelp.dll",
    "gdi32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "mswsock.dll",
    "ncrypt.dll",
    "ntdll.dll",
    "ole32.dll",
    "oleaut32.dll",
    "powrprof.dll",
    "psapi.dll",
    "rpcrt4.dll",
    "secur32.dll",
    "setupapi.dll",
    "shell32.dll",
    "shlwapi.dll",
    "user32.dll",
    "userenv.dll",
    "version.dll",
    "winmm.dll",
    "ws2_32.dll",
}

# Installed by the GPU DRIVER — never by us, never by Windows. Only a *loadable* backend may
# import one: the runtime probes those and skips a backend that fails to load, so a box with no
# driver degrades to CPU instead of dying. An import from `knaif.exe` or a core lib would be a
# hard startup failure, so LOADABLE_ONLY below is enforced by name, not by prefix.
#
# NB the CUDA *runtime* libs (`cudart64_*`, `cublas64_*`, `cublasLt64_*`) are deliberately NOT
# here. They are redistributable payload that `installers/package.sh` copies into the artifact
# from $CUDA_PATH — so they must be STAGED, and allowlisting them would invert the check and
# pass an artifact whose CUDA payload is missing. `nvcuda.dll` is the actual driver entry point
# and is the only NVIDIA library that legitimately comes from the machine.
DRIVER_PROVIDED = {"vulkan-1.dll", "nvcuda.dll"}

# The libraries staged as *core* by package.sh. These are hard imports of `knaif.exe`, loaded at
# process start, so nothing in this set may depend on a GPU driver. Everything else matching
# `ggml-*` is a loadable backend (`ggml-cpu-*`, `ggml-vulkan`, `ggml-cuda`) — mirroring the
# staging logic in package.sh, which excludes exactly `ggml-base` from the backends glob.
#
# Matching on the `ggml-` prefix alone would wrongly classify `ggml-base.dll` — a core lib — as
# loadable, and let a GPU-driver import into the startup path unnoticed.
CORE_LIBS = {"ggml-base.dll", "ggml.dll", "llama.dll", "llama-common.dll"}

PE_SUFFIXES = {".exe", ".dll"}


def imported_dlls(path: Path) -> list[str]:
    """Return the DLL names in a PE file's import directory, lowercased."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError(f"{path.name}: not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"{path.name}: no PE signature")

    (n_sections,) = struct.unpack_from("<H", data, pe + 6)
    (opt_size,) = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic == 0x20B:  # PE32+
        dir_off = opt + 112
    elif magic == 0x10B:  # PE32
        dir_off = opt + 96
    else:
        raise ValueError(f"{path.name}: unknown optional-header magic {magic:#x}")

    (n_dirs,) = struct.unpack_from("<I", data, dir_off - 4)
    if n_dirs < 2:
        return []
    import_rva, import_size = struct.unpack_from("<II", data, dir_off + 8)
    if not import_rva or not import_size:
        return []  # no imports at all

    # Section table maps RVAs to file offsets; the import directory is described in RVAs.
    sections = []
    sec_off = opt + opt_size
    for i in range(n_sections):
        base = sec_off + i * 40
        virt_size, virt_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((virt_addr, max(virt_size, raw_size), raw_ptr))

    def to_offset(rva: int) -> int | None:
        for virt_addr, size, raw_ptr in sections:
            if virt_addr <= rva < virt_addr + size:
                return int(raw_ptr + (rva - virt_addr))
        return None

    def read_cstr(offset: int) -> str:
        end = data.index(b"\0", offset)
        return data[offset:end].decode("ascii", "replace")

    names: list[str] = []
    table = to_offset(import_rva)
    if table is None:
        return []
    # 20-byte IMAGE_IMPORT_DESCRIPTOR entries, terminated by an all-zero one.
    while True:
        entry = data[table : table + 20]
        if len(entry) < 20 or entry == b"\0" * 20:
            break
        name_rva = struct.unpack_from("<I", entry, 12)[0]
        name_off = to_offset(name_rva)
        if name_off is not None:
            names.append(read_cstr(name_off).lower())
        table += 20
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bindir", type=Path, help="the artifact's bin/ directory")
    parser.add_argument("--verbose", action="store_true", help="list every resolved import")
    args = parser.parse_args(argv)

    if not args.bindir.is_dir():
        print(f"not a directory: {args.bindir}", file=sys.stderr)
        return 2

    binaries = sorted(p for p in args.bindir.iterdir() if p.suffix.lower() in PE_SUFFIXES)
    if not binaries:
        print(f"no PE files in {args.bindir}", file=sys.stderr)
        return 2

    staged = {p.name.lower() for p in binaries}
    failures: list[str] = []

    for binary in binaries:
        try:
            imports = imported_dlls(binary)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        for dll in sorted(set(imports)):
            if dll in staged or dll.startswith("api-ms-win-") or dll in WINDOWS_PROVIDED:
                if args.verbose:
                    where = "staged" if dll in staged else "windows"
                    print(f"  ok   {binary.name} -> {dll} ({where})")
                continue
            if dll in DRIVER_PROVIDED:
                # Tolerated only in a loadable backend, which the runtime skips when the driver
                # is absent. `ggml-base.dll` is a CORE lib despite the ggml- prefix, so it is
                # excluded here — a driver import from the startup path is a hard failure.
                name = binary.name.lower()
                if name.startswith("ggml-") and name not in CORE_LIBS:
                    if args.verbose:
                        print(f"  ok   {binary.name} -> {dll} (gpu driver, loadable backend)")
                    continue
                failures.append(
                    f"{binary.name} imports {dll}, which only a loadable ggml-* backend may "
                    "depend on. This binary is loaded at process start, so a machine without "
                    "the driver would fail to launch knaif at all"
                )
                continue
            failures.append(
                f"{binary.name} imports {dll}, which is neither staged in "
                f"{args.bindir.name}/ nor provided by a clean Windows install"
            )

    if failures:
        print(f"FAIL: {len(failures)} undeclared runtime dependency(ies)\n", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nThese resolve on this machine only because it has developer tooling "
            "installed.\nOn a clean Windows box the process dies at startup with "
            "STATUS_DLL_NOT_FOUND (0xC0000135),\nprinting nothing. Stage the DLLs beside the "
            "exe, or add them to WINDOWS_PROVIDED if\nthey genuinely ship with Windows.",
            file=sys.stderr,
        )
        return 1

    print(f"  ok  {len(binaries)} binaries: every import is staged or Windows-provided")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
