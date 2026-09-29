"""The gate against the packaged binaries of a two-OS release: each cell against its own OS's binary.

`gate --native-bin` took one binary, and the 1.2.0 matrix has Windows and Linux cells: handed the
Windows `knaif.exe`, every Linux cell read "changed since the run: native_binary" (R5c T17,
2026-09-29). Several binaries are accepted now, each recognised from its header (format AND
architecture) as a `platforms.yaml` id; a cell keyed `model|os|backend` is checked against its OS's
binary, and a record that names no OS (an L3 cell, a flat layer) against whichever given binary it
recorded. Hardened after a Codex audit: flat layers keep the check, the release record uses the
binaries, arm64 is not taken for x64.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from knaif.evalsuite.gate import (
    binaries_by_os,
    evaluate_skill,
    evidence_tuple,
    record_layers,
    write_release_record,
)
from knaif.evalsuite.matrix import cell_key

from .test_acceptance_matrix import MODEL, _matrix
from .test_gate import make_tree

ENTRIES = [
    {"os": "windows-x64", "backend": "cuda", "coverage": "full"},
    {"os": "linux-x64", "backend": "cuda", "coverage": "full"},
]
WIN = cell_key(MODEL, "windows-x64", "cuda")
LIN = cell_key(MODEL, "linux-x64", "cuda")


def _pe(machine: int = 0x8664) -> bytes:
    head = bytearray(0x40)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = (0x40).to_bytes(4, "little")
    return bytes(head) + b"PE\x00\x00" + machine.to_bytes(2, "little")


def _elf(machine: int = 0x3E) -> bytes:
    head = bytearray(20)
    head[0:4] = b"\x7fELF"
    head[4], head[5] = 2, 1  # 64-bit, little-endian
    head[18:20] = machine.to_bytes(2, "little")
    return bytes(head)


def _binary(path: Path, header: bytes, body: bytes) -> tuple[Path, str]:
    path.write_bytes(header + body)
    return path, hashlib.sha256(header + body).hexdigest()


@pytest.fixture
def tree(tmp_path: Path):
    tree = make_tree(tmp_path)
    _matrix(tree, entries=ENTRIES)
    exe, exe_sha = _binary(tmp_path / "knaif.exe", _pe(), b"windows build")
    elf, elf_sha = _binary(tmp_path / "knaif", _elf(), b"linux build")
    base = evidence_tuple("demo", tree)
    record_layers("demo", tree, {"L1": {"summary": "green"}, "L2": {"summary": "green"}})
    l3 = {"cell": MODEL, "summary": "parity", "passed": True}
    record_layers("demo", tree, {"L3": {**l3, "evidence": {**base, "native_binary": exe_sha}}})
    for cell, sha in ((WIN, exe_sha), (LIN, elf_sha)):
        l4 = {"cell": cell, "summary": "ACCEPTED", "passed": True}
        record_layers("demo", tree, {"L4": {**l4, "evidence": {**base, "native_binary": sha}}})
    return tree, exe, elf


def _l4(tree: Path, binaries):
    gate = evaluate_skill("demo", tree, "supported", native_binary=binaries)
    return gate, next(s for s in gate.layers if s.layer == "L4")


def test_each_cell_is_checked_against_its_own_os_binary(tree) -> None:
    tree, exe, elf = tree
    gate, l4 = _l4(tree, [exe, elf])
    assert {s.layer: s.state for s in gate.layers}["L3"] == "valid"
    assert l4.state == "valid" and gate.derived == "supported"


def test_a_cell_whose_binary_changed_is_stale(tree, tmp_path) -> None:
    tree, exe, _ = tree
    other, _ = _binary(tmp_path / "other", _elf(), b"a different linux build")
    _, l4 = _l4(tree, [exe, other])
    assert l4.state == "stale" and LIN in l4.detail and WIN not in l4.detail


def test_a_cell_whose_os_binary_was_not_given_is_reported_not_checked(tree) -> None:
    tree, exe, _ = tree
    _, l4 = _l4(tree, [exe])
    assert l4.state == "valid"
    assert "not checked here: native_binary" in l4.detail and LIN in l4.detail


def test_one_binary_as_a_path_still_works_as_before(tree) -> None:
    tree, exe, _ = tree
    gate = evaluate_skill("demo", tree, "supported", native_binary=exe)
    assert next(s for s in gate.layers if s.layer == "L3").state == "valid"


def test_a_flat_layer_keeps_its_binary_check_when_given_a_list(tmp_path) -> None:
    """No matrix: the flat L4 record was measured on one binary; a list naming another must not
    read valid (Codex: a list dropped the check for flat layers altogether)."""
    tree = make_tree(tmp_path)
    exe, exe_sha = _binary(tmp_path / "knaif.exe", _pe(), b"windows build")
    other, _ = _binary(tmp_path / "other.exe", _pe(), b"another windows build")
    base = evidence_tuple("demo", tree)
    record_layers("demo", tree, {"L1": {"summary": "green"}, "L2": {"summary": "green"}})
    for layer in ("L3", "L4"):
        entry = {"summary": "run", "passed": True, "evidence": {**base, "native_binary": exe_sha}}
        record_layers("demo", tree, {layer: entry})
    ok = evaluate_skill("demo", tree, "supported", native_binary=[exe])
    bad = evaluate_skill("demo", tree, "supported", native_binary=[other])
    assert {s.layer: s.state for s in ok.layers}["L4"] == "valid"
    assert {s.layer: s.state for s in bad.layers}["L4"] == "stale"


def test_the_release_record_is_judged_against_the_given_binaries(tree, tmp_path) -> None:
    tree, exe, _ = tree
    other, _ = _binary(tmp_path / "other", _elf(), b"a different linux build")
    out = write_release_record(tree, "9.9.0", skills=["demo"], native_binary=[exe, other])
    verdict = json.loads((Path(out) / "release.json").read_text(encoding="utf-8"))
    assert verdict["skills"]["demo"]["layers"]["L4"]["state"] == "stale"


@pytest.mark.parametrize(
    "header",
    [
        b"#!/bin/sh\n",
        _elf(machine=0xB7),  # aarch64 ELF is not linux-x64
        _pe(machine=0xAA64),  # arm64 PE is not windows-x64
    ],
)
def test_a_binary_not_of_a_platform_the_release_ships_is_refused(tmp_path, header) -> None:
    odd = tmp_path / "knaif.bin"
    odd.write_bytes(header)
    with pytest.raises(ValueError):
        binaries_by_os([odd])


def test_two_binaries_for_one_os_are_refused(tmp_path) -> None:
    a, _ = _binary(tmp_path / "a", _pe(), b"one")
    b, _ = _binary(tmp_path / "b", _pe(), b"two")
    with pytest.raises(ValueError):
        binaries_by_os([a, b])
