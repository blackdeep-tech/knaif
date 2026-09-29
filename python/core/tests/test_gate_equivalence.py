"""Carrying accepted results over to a rebuild that differs from the measured build only in text.

1.2.0's release candidate shipped a v1 model name in help text; the owner chose (2026-09-29) to fix
the text, rebuild, and keep the acceptance results, verified by a few sample requests. Every L3/L4
record pins the measured binary's sha256 and the native source fingerprint, both of which a rebuild
changes. An equivalence entry maps exactly those old values to exactly the new ones; the gate honours
it only for that mapping, from ONE entry covering source and binary together, and always says so.
It is written by `evalsuite equivalence`, which refuses unless the source difference is the declared
replacements inside string literals or comments. Hardened after a Codex audit (2026-09-29).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knaif.evalsuite.gate import (
    evaluate_skill,
    evidence_tuple,
    record_layers,
    rust_text_spans,
    text_only_change,
    write_release_record,
)
from knaif.evalsuite.matrix import cell_key

from .test_acceptance_matrix import MODEL, _matrix
from .test_gate import make_tree

CELL = cell_key(MODEL, "windows-x64", "cuda")
LIN_CELL = cell_key(MODEL, "linux-x64", "cuda")
V1, V2 = "knaif-qwen3-4b-v1", "knaif-qwen3-4b-v2"


def _exe(path: Path, body: bytes) -> tuple[Path, str]:
    import hashlib

    head = bytearray(0x40)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data = bytes(head) + b"PE\x00\x00" + (0x8664).to_bytes(2, "little") + body
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def _elf(path: Path, body: bytes) -> tuple[Path, str]:
    import hashlib

    head = bytearray(20)
    head[0:4] = b"\x7fELF"
    head[4], head[5] = 2, 1
    head[18:20] = (0x3E).to_bytes(2, "little")
    data = bytes(head) + body
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
    return tree, base["native"], old, old_sha, new, new_sha


def _entry(eid: str, native: tuple[str, str], binaries: dict[str, tuple[str, str]]) -> dict:
    return {
        "id": eid,
        "fingerprints": {"native": {"from": native[0], "to": native[1]}},
        "binaries": {os_: {"from": a, "to": b} for os_, (a, b) in binaries.items()},
    }


def _write(tree: Path, entries: list[dict]) -> None:
    path = tree / "evals" / "acceptance" / "equivalences.json"
    path.write_text(json.dumps({"equivalences": entries}), encoding="utf-8")


def _layers(tree: Path, binaries):
    gate = evaluate_skill("demo", tree, "supported", native_binary=binaries)
    return {s.layer: s for s in gate.layers}, gate


def test_without_an_equivalence_the_rebuild_is_stale(tree) -> None:
    tree, _, _, _, new, _ = tree
    layers, _ = _layers(tree, [new])
    assert layers["L4"].state == "stale"


def test_an_equivalence_carries_the_results_over_and_says_so(tree) -> None:
    tree, old_native, _, old_sha, new, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    _write(tree, [_entry("textfix", (old_native, now), {"windows-x64": (old_sha, new_sha)})])
    layers, gate = _layers(tree, [new])
    assert layers["L4"].state == "valid" and layers["L3"].state == "valid"
    assert gate.derived == "supported"
    assert "equivalent: textfix" in layers["L4"].detail


def test_an_equivalence_covers_only_the_exact_values_it_names(tree, tmp_path) -> None:
    tree, old_native, _, old_sha, _, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    _write(tree, [_entry("textfix", (old_native, now), {"windows-x64": (old_sha, new_sha)})])
    other, _ = _exe(tmp_path / "other.exe", b"some other build")
    layers, _ = _layers(tree, [other])
    assert layers["L4"].state == "stale"


def test_the_measured_binary_does_not_pass_with_the_rebuilt_source(tree) -> None:
    tree, old_native, old, old_sha, _, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    _write(tree, [_entry("textfix", (old_native, now), {"windows-x64": (old_sha, new_sha)})])
    layers, _ = _layers(tree, [old])
    assert layers["L4"].state == "stale" and "equivalent rebuild" in layers["L4"].detail


def test_source_and_binary_must_come_from_one_entry(tree) -> None:
    """Entry A maps the source, entry B maps the binary: neither certifies the pair."""
    tree, old_native, _, old_sha, new, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    _write(
        tree,
        [
            _entry("a", (old_native, now), {"windows-x64": ("x" * 64, "y" * 64)}),
            _entry("b", ("p" * 64, "q" * 64), {"windows-x64": (old_sha, new_sha)}),
        ],
    )
    layers, _ = _layers(tree, [new])
    assert layers["L4"].state == "stale"


@pytest.mark.parametrize("key", ["contracts", "grading", "model", "policy"])
def test_a_hand_added_mapping_for_another_fingerprint_is_ignored(tree, key) -> None:
    tree, old_native, _, old_sha, new, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    entry = _entry("textfix", (old_native, now), {"windows-x64": (old_sha, new_sha)})
    entry["fingerprints"][key] = {"from": "a" * 64, "to": "b" * 64}
    _write(tree, [entry])
    layers, _ = _layers(tree, [new])
    assert layers["L4"].state == "stale", "an entry with an extra mapping is not honoured"


def test_an_l3_record_does_not_depend_on_binary_order(tree, tmp_path) -> None:
    tree, old_native, _, old_sha, new, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    elf, elf_sha = _elf(tmp_path / "knaif", b"linux rebuild")
    _write(
        tree,
        [
            _entry(
                "textfix",
                (old_native, now),
                {"windows-x64": (old_sha, new_sha), "linux-x64": ("l" * 64, elf_sha)},
            )
        ],
    )
    for order in ([new, elf], [elf, new]):
        layers, _ = _layers(tree, order)
        assert layers["L3"].state == "valid", order


def test_the_release_record_keeps_the_equivalences(tree) -> None:
    tree, old_native, _, old_sha, new, new_sha = tree
    now = evidence_tuple("demo", tree)["native"]
    _write(tree, [_entry("textfix", (old_native, now), {"windows-x64": (old_sha, new_sha)})])
    out = write_release_record(tree, "9.9.0", skills=["demo"], native_binary=[new])
    assert (Path(out) / "equivalences.json").is_file()


# ── the text-only check ───────────────────────────────────────────────────────────────────────


def test_a_replacement_inside_help_text_is_text_only() -> None:
    old = (
        f"/// Model: an installed NAME (e.g. `{V1}`) or a path\n"
        "fn f() -> &'static str {\n    \"e.g. just native x --model " + V1 + '"\n}\n'
    )
    assert text_only_change(old, old.replace(V1, V2), [(V1, V2)])


def test_a_multiline_string_continuation_is_still_text() -> None:
    old = 'bail!(\n    "usage\\n  \\\n     e.g. --model ' + V1 + '",\n    x\n);\n'
    assert text_only_change(old, old.replace(V1, V2), [(V1, V2)])


def test_a_replacement_in_code_is_not_text_only() -> None:
    old = f'fn pick(m: &str) -> bool {{ m == "{V1}" }}\nconst {V1.replace("-", "_")}: u8 = 1;\n'
    old = old.replace(V1.replace("-", "_"), V1)  # the name as a bare token in code
    assert not text_only_change(old, old.replace(V1, V2), [(V1, V2)])


def test_nothing_else_may_change() -> None:
    old = f'let a = "{V1}";\nlet b = 1;\n'
    new = old.replace(V1, V2).replace("b = 1", "b = 2")
    assert not text_only_change(old, new, [(V1, V2)])
    assert not text_only_change(old, old, [(V1, V2)]), "no change is not a text fix"


def test_the_lexer_finds_strings_and_comments() -> None:
    src = 'a // c1\n"s\\"q" /* b /* n */ c */ r#"raw "x" "# \'c\' \'a\n'
    kinds = [src[s:e] for s, e in rust_text_spans(src)]
    assert "// c1" in kinds
    assert '"s\\"q"' in kinds
    assert "/* b /* n */ c */" in kinds
    assert 'r#"raw "x" "#' in kinds


def test_some_occurrences_may_stay_as_they_were() -> None:
    """The real fix changed the help text and left the unit tests' v1 test data alone."""
    old = f'/// e.g. `{V1}`\nfn t() {{ assert!(m("{V1}")); }}\n'
    new = f'/// e.g. `{V2}`\nfn t() {{ assert!(m("{V1}")); }}\n'
    assert text_only_change(old, new, [(V1, V2)])
