"""Guard `scripts/check_pe_imports.py` — the check that keeps Windows artifacts portable.

That script is the only thing standing between a build box with Visual Studio and an artifact
that dies at process start on every clean Windows machine. It earns the same treatment as
``test_installer_iss.py``: assert it **fails** on the cases it claims to catch, not merely that
it passes on a good tree.

An external audit of the first version found two classification holes that a
passes-on-real-artifact test would never have surfaced, because a healthy CPU artifact imports
neither a GPU driver nor a CUDA runtime:

* loadable backends were matched on the ``ggml-`` prefix, which also matches **``ggml-base.dll``
  — a core library loaded at process start**, so a GPU-driver import there would have been
  waved through while making the CLI unlaunchable;
* the CUDA runtime libraries were allowlisted as driver-provided even though ``package.sh``
  *stages* them from ``$CUDA_PATH``, which **inverted** the check — an artifact whose CUDA
  payload was missing would have passed.

Both are covered below. The tests build **synthetic PE files** rather than leaning on a staged
artifact: `dist/staging/` exists only after a Windows build, so a test that depended on it
would silently skip everywhere else — which is the same "the check cannot run where it
matters" failure this whole area keeps producing.
"""

from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(".").resolve()
SCRIPT = ROOT / "scripts" / "check_pe_imports.py"
PACKAGE_SH = ROOT / "installers" / "package.sh"


def _load():
    spec = importlib.util.spec_from_file_location("check_pe_imports", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpi = _load()


# --------------------------------------------------------------------------------------
# A minimal PE64 with a chosen import table
# --------------------------------------------------------------------------------------


def write_pe(path: Path, imports: list[str]) -> Path:
    """Write a PE32+ file whose import directory names exactly ``imports``.

    Only the structures the checker walks are real: DOS stub → PE signature → COFF header →
    PE32+ optional header → data directory 1 → one section holding the import descriptors and
    their name strings. Everything else is zero, which is fine because nothing loads these.
    """
    sect_rva = 0x1000
    descriptors = b""
    names_blob = b""
    # Names sit after the descriptor array (n entries + one null terminator).
    names_base = (len(imports) + 1) * 20
    for name in imports:
        name_rva = sect_rva + names_base + len(names_blob)
        # IMAGE_IMPORT_DESCRIPTOR: name RVA lives at offset 12.
        descriptors += struct.pack("<IIIII", 0, 0, 0, name_rva, 0)
        names_blob += name.encode("ascii") + b"\0"
    descriptors += b"\0" * 20  # terminator
    section_data = descriptors + names_blob

    n_dirs = 16
    opt_size = 112 + n_dirs * 8
    pe_off = 0x80
    sect_table_off = pe_off + 24 + opt_size
    raw_ptr = (sect_table_off + 40 + 0x1FF) & ~0x1FF  # align the section's file offset

    buf = bytearray(raw_ptr + len(section_data))
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, pe_off)
    buf[pe_off : pe_off + 4] = b"PE\0\0"
    # COFF: machine=AMD64, 1 section, opt_size at +20.
    struct.pack_into("<HH", buf, pe_off + 4, 0x8664, 1)
    struct.pack_into("<H", buf, pe_off + 20, opt_size)

    opt = pe_off + 24
    struct.pack_into("<H", buf, opt, 0x20B)  # PE32+
    struct.pack_into("<I", buf, opt + 108, n_dirs)  # NumberOfRvaAndSizes
    # Data directory 1 = import table (directory 0 is the export table).
    struct.pack_into("<II", buf, opt + 112 + 8, sect_rva, len(descriptors))

    # One section covering the whole blob.
    struct.pack_into(
        "<8sIIII",
        buf,
        sect_table_off,
        b".rdata\0\0",
        len(section_data),
        sect_rva,
        len(section_data),
        raw_ptr,
    )
    buf[raw_ptr : raw_ptr + len(section_data)] = section_data
    path.write_bytes(bytes(buf))
    return path


@pytest.fixture()
def artifact(tmp_path: Path):
    """Build a bin/ dir; returns a callable taking ``{filename: [imports]}``."""

    def build(layout: dict[str, list[str]]) -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        for name, imports in layout.items():
            write_pe(bindir / name, imports)
        return bindir

    return build


# --------------------------------------------------------------------------------------
# The parser itself
# --------------------------------------------------------------------------------------


def test_reads_the_import_table(tmp_path: Path) -> None:
    pe = write_pe(tmp_path / "x.dll", ["KERNEL32.dll", "VCRUNTIME140.dll"])
    assert cpi.imported_dlls(pe) == ["kernel32.dll", "vcruntime140.dll"]


def test_a_binary_with_no_imports_is_not_an_error(tmp_path: Path) -> None:
    pe = write_pe(tmp_path / "x.dll", [])
    assert cpi.imported_dlls(pe) == []


def test_a_non_pe_file_is_reported_not_ignored(tmp_path: Path) -> None:
    junk = tmp_path / "x.dll"
    junk.write_bytes(b"not a PE at all")
    with pytest.raises(ValueError):
        cpi.imported_dlls(junk)


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


def test_staged_and_windows_imports_pass(artifact) -> None:
    bindir = artifact(
        {
            "knaif.exe": ["kernel32.dll", "llama.dll", "api-ms-win-crt-stdio-l1-1-0.dll"],
            "llama.dll": ["kernel32.dll"],
        }
    )
    assert cpi.main([str(bindir)]) == 0


def test_the_ucrt_forwarders_are_accepted(artifact) -> None:
    """`api-ms-win-crt-*` is the Universal CRT, an OS component since Windows 10.

    Confirmed empirically: a clean Windows 11 24H2 image reports `ucrtbase.dll` PRESENT while
    VCRUNTIME140/MSVCP140/VCOMP140 are all ABSENT. That asymmetry is the whole basis of the
    allowlist, so it gets a test rather than living only in a comment.
    """
    bindir = artifact({"knaif.exe": ["api-ms-win-crt-heap-l1-1-0.dll"]})
    assert cpi.main([str(bindir)]) == 0


def test_an_unstaged_vc_runtime_fails(artifact, capsys) -> None:
    """W1 itself: the defect that shipped in every 1.0.x Windows artifact."""
    bindir = artifact({"knaif.exe": ["vcruntime140.dll"]})
    assert cpi.main([str(bindir)]) == 1
    assert "vcruntime140.dll" in capsys.readouterr().err


def test_a_staged_vc_runtime_passes(artifact) -> None:
    bindir = artifact({"knaif.exe": ["vcruntime140.dll"], "vcruntime140.dll": []})
    assert cpi.main([str(bindir)]) == 0


def test_a_loadable_backend_may_import_the_gpu_driver(artifact) -> None:
    """`ggml-vulkan` is dlopened and skipped when the driver is absent, so this is fine."""
    bindir = artifact({"knaif.exe": [], "ggml-vulkan.dll": ["vulkan-1.dll"]})
    assert cpi.main([str(bindir)]) == 0


@pytest.mark.parametrize("core", ["ggml-base.dll", "ggml.dll", "llama.dll", "knaif.exe"])
def test_a_core_binary_may_not_import_the_gpu_driver(artifact, capsys, core: str) -> None:
    """The audit finding. `ggml-base.dll` matches the `ggml-` prefix but is a CORE library.

    Core libraries are hard imports resolved at process start, so a GPU-driver dependency there
    does not degrade to CPU — it stops knaif launching at all on any machine without the driver.
    Matching loadable backends by prefix alone let exactly that through.
    """
    bindir = artifact({"knaif.exe": [], core: ["vulkan-1.dll"]})
    assert cpi.main([str(bindir)]) == 1
    err = capsys.readouterr().err
    assert core in err and "process start" in err


def test_cuda_runtime_libraries_must_be_staged(artifact, capsys) -> None:
    """The other audit finding: these are payload `package.sh` copies, not driver files.

    Allowlisting them inverted the check — an artifact whose CUDA payload failed to stage would
    have been reported clean.
    """
    bindir = artifact({"knaif.exe": [], "ggml-cuda.dll": ["cudart64_12.dll", "nvcuda.dll"]})
    assert cpi.main([str(bindir)]) == 1
    err = capsys.readouterr().err
    assert "cudart64_12.dll" in err
    assert "nvcuda.dll" not in err  # the real driver entry point stays allowlisted


def test_cuda_runtime_libraries_pass_once_staged(artifact) -> None:
    bindir = artifact(
        {"knaif.exe": [], "ggml-cuda.dll": ["cudart64_12.dll", "nvcuda.dll"], "cudart64_12.dll": []}
    )
    assert cpi.main([str(bindir)]) == 0


def test_a_directory_with_no_binaries_is_an_error(tmp_path: Path) -> None:
    """Exit 2, not 0. An empty bin/ means the artifact never staged — silence would be a pass."""
    empty = tmp_path / "bin"
    empty.mkdir()
    assert cpi.main([str(empty)]) == 2
    assert cpi.main([str(tmp_path / "nope")]) == 2


# --------------------------------------------------------------------------------------
# The allowlist must not drift from what package.sh actually stages
# --------------------------------------------------------------------------------------


def test_core_libs_match_the_staging_list_in_package_sh() -> None:
    """`CORE_LIBS` encodes package.sh's own core/loadable split; the two must agree.

    package.sh stages `ggml-base ggml llama llama-common` as core and then globs the backends
    directory for everything else. If that list grows, a new core library would be classified as
    a loadable backend and allowed to depend on a GPU driver.
    """
    text = PACKAGE_SH.read_text(encoding="utf-8")
    match = re.search(
        r"# Core libs: plain unversioned DLLs.*?\n\s*for stem in ([^;\n]+); do", text, re.S
    )
    assert match, "could not find the Windows core-lib staging loop in package.sh"
    stems = match.group(1).split()
    assert {f"{s}.dll" for s in stems} == cpi.CORE_LIBS


def test_cuda_payload_libraries_are_not_allowlisted() -> None:
    """Whatever package.sh stages from $CUDA_PATH must be *required*, never assumed present."""
    text = PACKAGE_SH.read_text(encoding="utf-8")
    assert "cudart64_*.dll" in text, "package.sh no longer stages the CUDA runtime — recheck this"
    for name in cpi.DRIVER_PROVIDED:
        assert not name.startswith(
            ("cudart", "cublas")
        ), f"{name} is staged payload, not a driver file; allowlisting it inverts the check"
