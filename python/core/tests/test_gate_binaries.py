"""The gate against the packaged binaries of a two-OS release: each cell against its own OS's binary.

`gate --native-bin` took one binary, and the 1.2.0 matrix has Windows and Linux cells: handed the
Windows `knaif.exe`, every Linux cell read "changed since the run: native_binary" (R5c T17,
2026-09-29). Several binaries are accepted now, each recognised as Windows or Linux from its
header; a cell keyed `model|os|backend` is checked against its OS's binary, and an L3 cell (keyed by
model only) against whichever given binary it recorded.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knaif.evalsuite.gate import binaries_by_os, evaluate_skill, evidence_tuple, record_layers
from knaif.evalsuite.matrix import cell_key

from .test_acceptance_matrix import MODEL, _matrix
from .test_gate import make_tree

ENTRIES = [
    {"os": "windows-x64", "backend": "cuda", "coverage": "full"},
    {"os": "linux-x64", "backend": "cuda", "coverage": "full"},
]
WIN = cell_key(MODEL, "windows-x64", "cuda")
LIN = cell_key(MODEL, "linux-x64", "cuda")


def _binary(path: Path, header: bytes, body: bytes) -> tuple[Path, str]:
    path.write_bytes(header + body)
    return path, hashlib.sha256(header + body).hexdigest()


@pytest.fixture
def tree(tmp_path: Path):
    tree = make_tree(tmp_path)
    _matrix(tree, entries=ENTRIES)
    exe, exe_sha = _binary(tmp_path / "knaif.exe", b"MZ", b"windows build")
    elf, elf_sha = _binary(tmp_path / "knaif", b"\x7fELF", b"linux build")
    base = evidence_tuple("demo", tree)
    record_layers("demo", tree, {"L1": {"summary": "green"}, "L2": {"summary": "green"}})
    record_layers(
        "demo",
        tree,
        {
            "L3": {
                "cell": MODEL,
                "summary": "parity",
                "passed": True,
                "evidence": {**base, "native_binary": exe_sha},
            }
        },
    )
    for cell, sha in ((WIN, exe_sha), (LIN, elf_sha)):
        record_layers(
            "demo",
            tree,
            {
                "L4": {
                    "cell": cell,
                    "summary": "ACCEPTED",
                    "passed": True,
                    "evidence": {**base, "native_binary": sha},
                }
            },
        )
    return tree, exe, elf


def _states(tree: Path, binaries) -> dict[str, str]:
    gate = evaluate_skill("demo", tree, "supported", native_binary=binaries)
    return {s.layer: s.state for s in gate.layers} | {"derived": gate.derived}


def test_each_cell_is_checked_against_its_own_os_binary(tree) -> None:
    tree, exe, elf = tree
    states = _states(tree, [exe, elf])
    assert states["L3"] == "valid" and states["L4"] == "valid"
    assert states["derived"] == "supported"


def test_a_cell_whose_binary_changed_is_stale(tree, tmp_path) -> None:
    tree, exe, _ = tree
    other, _ = _binary(tmp_path / "other", b"\x7fELF", b"a different linux build")
    gate = evaluate_skill("demo", tree, "supported", native_binary=[exe, other])
    l4 = next(s for s in gate.layers if s.layer == "L4")
    assert l4.state == "stale" and LIN in l4.detail and WIN not in l4.detail


def test_a_cell_whose_os_binary_was_not_given_is_reported_not_checked(tree) -> None:
    tree, exe, _ = tree
    gate = evaluate_skill("demo", tree, "supported", native_binary=[exe])
    l4 = next(s for s in gate.layers if s.layer == "L4")
    assert l4.state == "valid"
    assert "not checked here: native_binary" in l4.detail


def test_one_binary_still_works_as_before(tree) -> None:
    tree, exe, _ = tree
    gate = evaluate_skill("demo", tree, "supported", native_binary=exe)
    l3 = next(s for s in gate.layers if s.layer == "L3")
    assert l3.state == "valid"


def test_a_binary_of_unknown_format_is_refused(tmp_path) -> None:
    odd = tmp_path / "knaif.bin"
    odd.write_bytes(b"#!/bin/sh\n")
    with pytest.raises(ValueError):
        binaries_by_os([odd])


def test_two_binaries_for_one_os_are_refused(tmp_path) -> None:
    a, _ = _binary(tmp_path / "a", b"MZ", b"one")
    b, _ = _binary(tmp_path / "b", b"MZ", b"two")
    with pytest.raises(ValueError):
        binaries_by_os([a, b])
