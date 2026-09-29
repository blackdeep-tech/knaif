"""Carrying accepted results over to a rebuild that differs from the measured build only in text.

1.2.0's release candidate shipped a v1 model name in help text; the owner chose (2026-09-29) to fix
the text, rebuild, and keep the acceptance results, verified by a few sample requests. Every L3/L4
record pins the measured binary's sha256 and the native source fingerprint, both of which a rebuild
changes. An equivalence entry maps exactly those old values to exactly the new ones; the gate honours
it only for that mapping, and always says so. It is written by `evalsuite equivalence`, which refuses
unless the source difference is made of the declared text replacements only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.gate import (
    evaluate_skill,
    evidence_tuple,
    record_layers,
    replacement_only,
)
from knaif.evalsuite.matrix import cell_key

from .test_acceptance_matrix import MODEL, _matrix
from .test_gate import make_tree

CELL = cell_key(MODEL, "windows-x64", "cuda")


def _exe(path: Path, body: bytes) -> tuple[Path, str]:
    import hashlib

    head = bytearray(0x40)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data = bytes(head) + b"PE\x00\x00" + (0x8664).to_bytes(2, "little") + body
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


@pytest.fixture
def tree(tmp_path: Path):
    tree = make_tree(tmp_path)
    _matrix(tree, entries=[{"os": "windows-x64", "backend": "cuda", "coverage": "full"}])
    old, old_sha = _exe(tmp_path / "old.exe", b"measured build")
    base = evidence_tuple("demo", tree)
    record_layers("demo", tree, {"L1": {"summary": "green"}, "L2": {"summary": "green"}})
    for layer, cell in (("L3", MODEL), ("L4", CELL)):
        entry = {"cell": cell, "summary": "ok", "passed": True}
        record_layers(
            "demo", tree, {layer: {**entry, "evidence": {**base, "native_binary": old_sha}}}
        )
    # The text fix: the native sources change, so the native fingerprint moves.
    src = tree / "native" / "crates" / "knaif-core" / "src" / "planner.rs"
    src.write_text(src.read_text(encoding="utf-8") + "// v2\n", encoding="utf-8")
    new, new_sha = _exe(tmp_path / "new.exe", b"rebuilt build")
    return tree, base["native"], old_sha, new, new_sha


def _write(tree: Path, entries: list[dict]) -> None:
    path = tree / "evals" / "acceptance" / "equivalences.json"
    path.write_text(json.dumps({"equivalences": entries}), encoding="utf-8")


def _l4(tree: Path, binary: Path):
    gate = evaluate_skill("demo", tree, "supported", native_binary=[binary])
    return next(s for s in gate.layers if s.layer == "L4"), gate


def test_without_an_equivalence_the_rebuild_is_stale(tree) -> None:
    tree, _, _, new, _ = tree
    l4, _ = _l4(tree, new)
    assert l4.state == "stale"


def test_an_equivalence_carries_the_results_over_and_says_so(tree) -> None:
    tree, old_native, old_sha, new, new_sha = tree
    new_native = evidence_tuple("demo", tree)["native"]
    _write(
        tree,
        [
            {
                "id": "textfix",
                "fingerprints": {"native": {"from": old_native, "to": new_native}},
                "binaries": {"windows-x64": {"from": old_sha, "to": new_sha}},
            }
        ],
    )
    l4, gate = _l4(tree, new)
    assert l4.state == "valid" and gate.derived == "supported"
    assert "equivalent: textfix" in l4.detail


def test_an_equivalence_covers_only_the_exact_values_it_names(tree, tmp_path) -> None:
    tree, old_native, old_sha, _, new_sha = tree
    new_native = evidence_tuple("demo", tree)["native"]
    _write(
        tree,
        [
            {
                "id": "textfix",
                "fingerprints": {"native": {"from": old_native, "to": new_native}},
                "binaries": {"windows-x64": {"from": old_sha, "to": new_sha}},
            }
        ],
    )
    other, _ = _exe(tmp_path / "other.exe", b"some other build")
    l4, _ = _l4(tree, other)
    assert l4.state == "stale"


def test_the_source_check_accepts_only_the_declared_replacements() -> None:
    ok = (
        "--- a/apps/cli/src/main.rs\n+++ b/apps/cli/src/main.rs\n"
        "@@ -164 +164 @@\n-    /// (e.g. `knaif-qwen3-4b-v1`) or a GGUF\n"
        "+    /// (e.g. `knaif-qwen3-4b-v2`) or a GGUF\n"
    )
    pairs = [("knaif-qwen3-4b-v1", "knaif-qwen3-4b-v2")]
    assert replacement_only(ok, pairs)
    code_change = ok.replace("+    /// (e.g.", "+    let x = 1; /// (e.g.")
    assert not replacement_only(code_change, pairs)
    added_line = ok + "+    unsafe_call();\n"
    assert not replacement_only(added_line, pairs)
    assert not replacement_only("", pairs), "an empty diff is not a text fix"
